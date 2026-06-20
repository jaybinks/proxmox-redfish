#!/usr/bin/env python3
"""
hostops.py -- the ONLY module in proxmox-redfish that shells out to the host.

It implements the verified efidisk write path used by SecureBoot management:
locate a VM's OVMF varstore (efidisk) LVM logical volume, confirm the VM is
stopped, and overwrite the varstore with a pre-baked image via ``dd`` -- the
automated equivalent of the manual ``dd ... conv=notrunc`` enrollment.

Every safety invariant from docs/SECURITY.md (INV-01..INV-20) is enforced here.
The block device is opened only after every precondition passes; in dry-run mode
(default unless REDFISH_SB_ALLOW_WRITE=1) ``dd`` never runs at all. All host
commands run as argv arrays through the single ``_run`` chokepoint with
``shell=False`` -- no user value is ever interpolated into a shell string.

This module deliberately imports nothing from proxmox_redfish.proxmox_redfish to
avoid an import cycle; it reads its own configuration from the environment.
"""

import hashlib
import json
import logging
import os
import re
import stat
import subprocess  # nosec B404 - argv-only, shell=False, see _run()
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger("proxmox-redfish.hostops")

# Configuration (env-driven, independent of the main module to avoid a cycle).
PROXMOX_NODE = os.getenv("PROXMOX_NODE", "pve")

# Size of a 4m OVMF varstore (OVMF_VARS_4M.fd): 540,672 bytes.
EFI_VARSTORE_SIZE_4M = 540672

# Valid Proxmox VMID range.
VMID_MIN = 100
VMID_MAX = 999999999


def varstore_dir() -> str:
    """Allowlisted directory that profile varstore images must live under (INV-10)."""
    return os.getenv("REDFISH_SB_VARSTORE_DIR", "/opt/proxmox-redfish/varstores")


def state_dir() -> str:
    """Directory for per-VM locks and sidecar state."""
    return os.getenv("REDFISH_SB_STATE_DIR", "/var/lib/proxmox-redfish/secureboot")


def vg_allowlist() -> List[str]:
    """LVM volume groups whose block devices may be written (default: pve)."""
    return [vg.strip() for vg in os.getenv("REDFISH_SB_VG_ALLOWLIST", "pve").split(",") if vg.strip()]


def writes_allowed() -> bool:
    """A real write requires explicit opt-in (INV-16); otherwise dry-run."""
    return os.getenv("REDFISH_SB_ALLOW_WRITE", "0") == "1"


# --------------------------------------------------------------------------- #
# Exceptions -- each carries its Redfish mapping so secureboot.sb_error() is a
# single generic mapper. See docs/spec/error-model.md.
# --------------------------------------------------------------------------- #
class HostOpError(Exception):
    """Base class for host-operation failures. Fails closed."""

    message_id = "GeneralError"
    status = 500
    resolution = "An internal error occurred; no write was performed."

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    @property
    def redfish_code(self) -> str:
        return f"Base.1.0.{self.message_id}"


class InvalidVmidError(HostOpError):
    message_id = "PropertyValueFormatError"
    status = 400
    resolution = "Provide a valid integer VM ID."


class NoEfiDiskError(HostOpError):
    message_id = "ActionNotSupported"
    status = 400
    resolution = "Add an OVMF EFI disk (efidisk0) to the VM before managing Secure Boot."


class UnsupportedEfiTypeError(HostOpError):
    message_id = "ActionNotSupported"
    status = 409
    resolution = "Recreate the EFI disk with efitype=4m; 2m varstores are unsupported."


class DeviceResolveError(HostOpError):
    message_id = "GeneralError"
    status = 500
    resolution = "Internal safety check failed while resolving the EFI disk; no write performed."


class VmRunningError(HostOpError):
    message_id = "ResourceInStandby"
    status = 409
    resolution = "Stop the system before modifying Secure Boot keys."


class SourceNotAllowedError(HostOpError):
    message_id = "ActionParameterValueError"
    status = 400
    resolution = "Use a configured varstore image inside the allowlisted directory."


class TemplateMissingError(HostOpError):
    message_id = "GeneralError"
    status = 500
    resolution = "Verify the varstore image path on the Proxmox host."


class ImageSizeMismatchError(HostOpError):
    message_id = "PropertyValueConflict"
    status = 409
    resolution = "The varstore image size does not match the EFI disk; check efitype."


class ImageHashMismatchError(HostOpError):
    message_id = "PropertyValueConflict"
    status = 409
    resolution = "The varstore image failed its integrity (sha256) check."


class WriteVerifyError(HostOpError):
    message_id = "GeneralError"
    status = 500
    resolution = "The write could not be verified; inspect the audit log."


class ToolMissingError(HostOpError):
    message_id = "ActionNotSupported"
    status = 501
    resolution = "Install virt-firmware (virt-fw-vars) on the Proxmox host."


class VarstoreParseError(HostOpError):
    message_id = "GeneralError"
    status = 500
    resolution = "The varstore could not be parsed; inspect the audit log."


class PrivateKeyRejectedError(HostOpError):
    message_id = "ActionParameterValueError"
    status = 400
    resolution = "Provide a public X.509 certificate only; private key material is never accepted."


class CertificateInvalidError(HostOpError):
    message_id = "ActionParameterValueError"
    status = 400
    resolution = "Provide a valid X.509 certificate (PEM or DER)."


# Hard cap on an uploaded certificate (defense against resource-abuse). 64 KiB is
# far larger than any real PK/KEK/db cert (~1-2 KB).
MAX_CERT_BYTES = 64 * 1024


# --------------------------------------------------------------------------- #
# Data shapes
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class EfiDisk:
    volid: str  # "local-lvm:vm-3009-disk-0"
    storage: str  # "local-lvm"
    device_path: str  # "/dev/pve/vm-3009-disk-0" (verified block device)
    efitype: str  # "4m"
    pre_enrolled: bool
    size_bytes: int  # actual LV size


@dataclass(frozen=True)
class WriteResult:
    wrote: bool
    verified: bool
    image_path: str
    image_sha256: str
    device_path: str
    bytes_considered: int
    dry_run: bool
    message: str


@dataclass(frozen=True)
class SecureBootState:
    enabled: bool
    mode: str  # SetupMode | UserMode | AuditMode | DeployedMode
    has_pk: bool
    has_kek: bool
    has_db: bool
    has_dbx: bool
    source: str  # "varstore"


# --------------------------------------------------------------------------- #
# The single shell-out chokepoint (INV-14)
# --------------------------------------------------------------------------- #
def _run(argv: List[str], *, timeout: int = 120, input_bytes: Optional[bytes] = None) -> "subprocess.CompletedProcess":
    """Sole subprocess boundary. argv is always a list; shell=False always."""
    logger.debug("hostops exec: %s", argv)
    return subprocess.run(  # nosec B603 - argv list, shell=False, no shell interpolation
        argv,
        shell=False,
        check=False,
        capture_output=True,
        input=input_bytes,
        timeout=timeout,
    )


def _audit(event: str, **fields: Any) -> None:
    """Audit log of every attempt and write (INV-17)."""
    payload = {"event": event, "ts": time.time(), **fields}
    logger.info("AUDIT sb %s", json.dumps(payload, sort_keys=True))


# --------------------------------------------------------------------------- #
# Validation & parsing
# --------------------------------------------------------------------------- #
def validate_vmid(vmid: Any) -> int:
    """INV-01: vmid is a positive integer within the valid PVE range."""
    if isinstance(vmid, bool):
        raise InvalidVmidError(f"Invalid VM ID: {vmid!r}")
    try:
        value = int(vmid)
    except (TypeError, ValueError):
        raise InvalidVmidError(f"VM ID must be an integer, got {vmid!r}")
    if not (VMID_MIN <= value <= VMID_MAX):
        raise InvalidVmidError(f"VM ID {value} is outside the valid range {VMID_MIN}-{VMID_MAX}")
    return value


def parse_efidisk_config(cfg_line: str) -> Dict[str, str]:
    """
    Parse an efidisk0 config string into volid/storage/options.

    Example: "local-lvm:vm-3009-disk-0,efitype=4m,pre-enrolled-keys=1,size=528K"
    """
    segments = [s for s in cfg_line.split(",") if s]
    if not segments or ":" not in segments[0]:
        raise NoEfiDiskError(f"Malformed efidisk0 config: {cfg_line!r}")
    volid = segments[0]
    storage = volid.split(":", 1)[0]
    opts: Dict[str, str] = {}
    for seg in segments[1:]:
        if "=" in seg:
            key, val = seg.split("=", 1)
            opts[key] = val
    result = {"volid": volid, "storage": storage}
    result.update(opts)
    return result


# --------------------------------------------------------------------------- #
# Device resolution (INV-02..07)
# --------------------------------------------------------------------------- #
def _verify_device_path(path: str, vmid: int) -> None:
    """INV-05/06/07: anchored path regex, block device, vmid cross-check."""
    allowed = vg_allowlist()
    # INV-05/07: logical path must be /dev/<allowed-vg>/vm-<vmid>-disk-<N>
    matched = any(re.fullmatch(rf"/dev/{re.escape(vg)}/vm-{vmid}-disk-\d+", path) for vg in allowed)
    if not matched:
        raise DeviceResolveError(f"Resolved device {path!r} does not match the expected efidisk pattern for VM {vmid}")
    # INV-06: realpath resolves to a block device under /dev/ (LVM maps to /dev/dm-*).
    real = os.path.realpath(path)
    if not real.startswith("/dev/"):
        raise DeviceResolveError(f"Resolved device {path!r} -> {real!r} is not under /dev/")
    try:
        mode = os.stat(real).st_mode
    except OSError as exc:
        raise DeviceResolveError(f"Cannot stat device {real!r}: {exc}")
    if not stat.S_ISBLK(mode):
        raise DeviceResolveError(f"Target {real!r} is not a block device")


def _resolve_block_device(volid: str, vmid: int) -> str:
    """Resolve a Proxmox volid to a verified block-device path via ``pvesm path``."""
    res = _run(["pvesm", "path", volid])
    if res.returncode != 0:
        raise DeviceResolveError(f"pvesm path failed for {volid!r}: {res.stderr.decode(errors='replace').strip()}")
    path: str = res.stdout.decode(errors="replace").strip()
    if not path:
        raise DeviceResolveError(f"pvesm path returned no device for {volid!r}")
    _verify_device_path(path, vmid)
    return path


def _block_size(path: str) -> int:
    res = _run(["blockdev", "--getsize64", path])
    if res.returncode != 0:
        raise DeviceResolveError(f"blockdev --getsize64 failed for {path!r}")
    try:
        return int(res.stdout.decode(errors="replace").strip())
    except ValueError:
        raise DeviceResolveError(f"blockdev returned a non-integer size for {path!r}")


def locate_efidisk(proxmox: Any, vmid: Any) -> EfiDisk:
    """
    Resolve a VM's efidisk to a verified block device.

    Enforces INV-01 (vmid), INV-02/03 (config-sourced efidisk0), INV-04 (efitype=4m),
    INV-05/06/07 (path/device verification). Raises a HostOpError on any failure;
    no write is performed.
    """
    vmid = validate_vmid(vmid)
    cfg = proxmox.nodes(PROXMOX_NODE).qemu(vmid).config.get()
    if not cfg or "efidisk0" not in cfg:  # INV-03
        raise NoEfiDiskError(f"VM {vmid} has no efidisk0 (OVMF EFI disk)")
    parsed = parse_efidisk_config(str(cfg["efidisk0"]))  # INV-02 (from config)
    efitype = parsed.get("efitype", "")
    if efitype != "4m":  # INV-04
        raise UnsupportedEfiTypeError(f"VM {vmid} efidisk efitype={efitype or 'unset'!r}; only 4m is supported")
    device_path = _resolve_block_device(parsed["volid"], vmid)  # INV-05/06/07
    size_bytes = _block_size(device_path)
    return EfiDisk(
        volid=parsed["volid"],
        storage=parsed["storage"],
        device_path=device_path,
        efitype=efitype,
        pre_enrolled=parsed.get("pre-enrolled-keys") == "1",
        size_bytes=size_bytes,
    )


# --------------------------------------------------------------------------- #
# VM power state (INV-08/09)
# --------------------------------------------------------------------------- #
def vm_is_running(proxmox: Any, vmid: int) -> bool:
    status = proxmox.nodes(PROXMOX_NODE).qemu(vmid).status.current.get() or {}
    state = status.get("qmpstatus") or status.get("status")
    return state == "running"


def _wait_stopped(proxmox: Any, vmid: int, timeout: int = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not vm_is_running(proxmox, vmid):
            return
        time.sleep(1)
    raise VmRunningError(f"VM {vmid} did not reach 'stopped' within {timeout}s")


# Per-VM in-process write locks (INV-15). A single threaded daemon process;
# an flock-based cross-process lock is a future hardening item (ROADMAP P5).
_vm_locks: Dict[int, threading.Lock] = {}
_vm_locks_guard = threading.Lock()


def _vm_lock(vmid: int) -> threading.Lock:
    with _vm_locks_guard:
        if vmid not in _vm_locks:
            _vm_locks[vmid] = threading.Lock()
        return _vm_locks[vmid]


@contextmanager
def stopped_vm_guard(proxmox: Any, vmid: Any, *, allow_autostop: bool = False) -> Iterator[bool]:
    """
    Hold an exclusive per-VM lock while the VM is confirmed stopped.

    INV-08: confirm stopped before the write. INV-15: exclusive lock for the whole
    operation. INV-09: re-check stopped after acquiring the lock. Yields whether the
    VM was running on entry; restarts it on exit only if allow_autostop is set.
    """
    vmid = validate_vmid(vmid)
    was_running = vm_is_running(proxmox, vmid)  # INV-08
    if was_running:
        if not allow_autostop:
            raise VmRunningError(f"VM {vmid} is running; stop it before modifying Secure Boot")
        _audit("vm.autostop", vmid=vmid)
        proxmox.nodes(PROXMOX_NODE).qemu(vmid).status.stop.post()
        _wait_stopped(proxmox, vmid)
    lock = _vm_lock(vmid)
    lock.acquire()  # INV-15
    try:
        if vm_is_running(proxmox, vmid):  # INV-09
            raise VmRunningError(f"VM {vmid} started during the operation; aborting")
        yield was_running
    finally:
        lock.release()
    if allow_autostop and was_running:
        _audit("vm.autostart", vmid=vmid)
        proxmox.nodes(PROXMOX_NODE).qemu(vmid).status.start.post()


# --------------------------------------------------------------------------- #
# Hashing helpers
# --------------------------------------------------------------------------- #
def _sha256_file(path: str) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _region_sha256(path: str, size: int) -> Optional[str]:
    """sha256 of the first ``size`` bytes of a device/file, or None on failure."""
    try:
        hasher = hashlib.sha256()
        remaining = size
        with open(path, "rb") as handle:
            while remaining > 0:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                hasher.update(chunk)
                remaining -= len(chunk)
        if remaining != 0:
            return None
        return hasher.hexdigest()
    except OSError as exc:
        logger.warning("Could not read region of %s: %s", path, exc)
        return None


def _is_within(path: str, directory: str) -> bool:
    directory = os.path.realpath(directory)
    path = os.path.realpath(path)
    return path == directory or path.startswith(directory + os.sep)


# --------------------------------------------------------------------------- #
# The write (INV-10..20)
# --------------------------------------------------------------------------- #
def write_varstore_image(
    efi: EfiDisk,
    image_path: str,
    *,
    expected_sha256: Optional[str] = None,
    dry_run: Optional[bool] = None,
) -> WriteResult:
    """
    Overwrite a VM's efidisk varstore with ``image_path``.

    Order matters: cheap/derivation checks first, the device is opened last, and any
    failure aborts before the write (INV-20, fail closed). Enforces INV-10 (allowlist),
    INV-11 (regular file + sha256), INV-12 (size), INV-19 (idempotent short-circuit),
    INV-16 (dry-run), INV-14 (argv ``dd``), INV-18 (post-write verify), INV-17 (audit).
    """
    real_img = os.path.realpath(image_path)

    if not _is_within(real_img, varstore_dir()):  # INV-10
        raise SourceNotAllowedError(f"Varstore image {image_path!r} is outside the allowlisted directory")
    if not os.path.isfile(real_img):  # INV-11
        raise TemplateMissingError(f"Varstore image {image_path!r} not found")

    actual_sha = _sha256_file(real_img)
    if expected_sha256 and actual_sha.lower() != expected_sha256.lower():  # INV-11
        raise ImageHashMismatchError(f"Varstore image {image_path!r} sha256 does not match the catalog")

    img_size = os.path.getsize(real_img)
    if img_size > efi.size_bytes:  # INV-12
        raise ImageSizeMismatchError(f"Varstore image ({img_size} B) is larger than the EFI disk ({efi.size_bytes} B)")

    # INV-19: idempotent short-circuit if the device already holds this image.
    current_sha = _region_sha256(efi.device_path, img_size)
    if current_sha is not None and current_sha == actual_sha:
        _audit("write.noop", vmid_device=efi.device_path, image=real_img, sha256=actual_sha)
        return WriteResult(
            wrote=False,
            verified=True,
            image_path=real_img,
            image_sha256=actual_sha,
            device_path=efi.device_path,
            bytes_considered=img_size,
            dry_run=False,
            message="no-op: efidisk already matches the requested varstore",
        )

    argv = ["dd", f"if={real_img}", f"of={efi.device_path}", "bs=1M", "conv=fsync,notrunc"]
    effective_dry = dry_run if dry_run is not None else (not writes_allowed())

    _audit(
        "write.attempt",
        device=efi.device_path,
        image=real_img,
        image_sha256=actual_sha,
        image_size=img_size,
        lv_size=efi.size_bytes,
        dry_run=effective_dry,
        argv=argv,
    )

    if effective_dry:  # INV-16
        return WriteResult(
            wrote=False,
            verified=False,
            image_path=real_img,
            image_sha256=actual_sha,
            device_path=efi.device_path,
            bytes_considered=img_size,
            dry_run=True,
            message="dry-run: all safety checks passed; no write performed",
        )

    res = _run(argv, timeout=120)
    if res.returncode != 0:
        _audit("write.failed", device=efi.device_path, stderr=res.stderr.decode(errors="replace").strip())
        raise WriteVerifyError(f"dd failed writing {efi.device_path}: {res.stderr.decode(errors='replace').strip()}")

    written_sha = _region_sha256(efi.device_path, img_size)  # INV-18
    verified = written_sha is not None and written_sha == actual_sha
    _audit("write.done", device=efi.device_path, verified=verified, written_sha256=written_sha)
    if not verified:
        raise WriteVerifyError(f"Post-write verification failed for {efi.device_path}")

    return WriteResult(
        wrote=True,
        verified=True,
        image_path=real_img,
        image_sha256=actual_sha,
        device_path=efi.device_path,
        bytes_considered=img_size,
        dry_run=False,
        message="varstore written and verified",
    )


# --------------------------------------------------------------------------- #
# Read-back state (used to bootstrap/reconcile sidecar; VM must be stopped)
# --------------------------------------------------------------------------- #
def validate_public_certificate(cert_string: str, cert_type: str) -> str:
    """
    Validate an uploaded certificate is a PUBLIC X.509 cert and return normalized PEM.

    Security (INV-13, exceptional-security): private key material is never accepted.
    Layered checks, fail-closed:
      1. Size cap (MAX_CERT_BYTES).
      2. Reject any private-key markers outright (PEM "PRIVATE KEY", PKCS#8/1/EC).
      3. Require an X.509 certificate body.
      4. If `cryptography` is installed, fully parse to confirm it is a real cert
         (and reject anything that also embeds a key).
    Raises PrivateKeyRejectedError / CertificateInvalidError. Never shells out.
    """
    if cert_type not in ("PEM", "DER"):
        raise CertificateInvalidError(f"Unsupported CertificateType {cert_type!r}; use PEM or DER")
    if not cert_string:
        raise CertificateInvalidError("Empty certificate")
    if len(cert_string.encode("utf-8", errors="replace")) > MAX_CERT_BYTES:
        raise CertificateInvalidError("Certificate exceeds the maximum allowed size")

    upper = cert_string.upper()
    # 2. Any private-key marker is an immediate, unconditional rejection.
    private_markers = (
        "PRIVATE KEY",
        "BEGIN RSA PRIVATE",
        "BEGIN EC PRIVATE",
        "BEGIN DSA PRIVATE",
        "BEGIN OPENSSH PRIVATE",
        "BEGIN PGP PRIVATE",
    )
    if any(marker in upper for marker in private_markers):
        raise PrivateKeyRejectedError("Input contains private key material; only public certificates are accepted")

    if cert_type == "PEM":
        if "BEGIN CERTIFICATE" not in upper:
            raise CertificateInvalidError("PEM does not contain an X.509 CERTIFICATE block")
        raw = cert_string.encode("utf-8")
        loader = "pem"
    else:  # DER provided as base64 in CertificateString
        import base64
        import binascii

        try:
            raw = base64.b64decode(cert_string, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise CertificateInvalidError(f"DER certificate is not valid base64: {exc}")
        loader = "der"

    # 4. Full parse when cryptography is available (best-effort otherwise).
    try:
        from cryptography import x509  # type: ignore
    except ImportError:
        logger.warning("cryptography not installed; certificate accepted on header checks only")
        return cert_string if cert_type == "PEM" else cert_string

    try:
        if loader == "pem":
            x509.load_pem_x509_certificate(raw)
        else:
            x509.load_der_x509_certificate(raw)
    except Exception as exc:  # noqa: BLE001 - any parse failure => invalid
        raise CertificateInvalidError(f"Certificate failed X.509 validation: {exc}")
    return cert_string


def build_varstore_from_certs(
    template_path: str,
    certs_by_db: Dict[str, List[str]],
    *,
    out_path: str,
    secure_boot: bool = True,
    no_microsoft: bool = True,
    guid: Optional[str] = None,
) -> str:
    """
    Build an OVMF varstore from public certs using ``virt-fw-vars``.

    `certs_by_db` maps a database id (PK/KEK/db/dbx) to a list of PEM strings. The
    template and output must live in the allowlisted varstore dir (INV-10). All certs
    are re-validated as public (INV-13) before use. Returns the sha256 of the built
    image. Raises ToolMissingError if virt-fw-vars is absent. argv-only (INV-14).
    """
    import shutil
    import tempfile

    if shutil.which("virt-fw-vars") is None:
        raise ToolMissingError("virt-fw-vars is not installed on the host")
    if not _is_within(os.path.realpath(template_path), varstore_dir()):
        raise SourceNotAllowedError("Varstore template is outside the allowlisted directory")
    if not os.path.isfile(template_path):
        raise TemplateMissingError(f"Varstore template {template_path!r} not found")
    if not _is_within(os.path.realpath(out_path), varstore_dir()):
        raise SourceNotAllowedError("Varstore output path is outside the allowlisted directory")

    owner_guid: str = guid or os.environ.get("REDFISH_SB_OWNER_GUID") or "00000000-0000-0000-0000-000000000000"
    argv: List[str] = ["virt-fw-vars", "--input", template_path, "--output", out_path]
    if secure_boot:
        argv.append("--secure-boot")
    if no_microsoft:
        argv.append("--no-microsoft")

    tmpdir = tempfile.mkdtemp(prefix="sb-certs-")
    try:
        for db_id, pems in certs_by_db.items():
            flag = {"PK": "--set-pk", "KEK": "--add-kek", "db": "--add-db", "dbx": "--add-dbx"}.get(db_id)
            if not flag:
                continue
            for idx, pem in enumerate(pems):
                validate_public_certificate(pem, "PEM")  # re-validate before enrolling
                cert_file = os.path.join(tmpdir, f"{db_id}-{idx}.pem")
                with open(cert_file, "w", encoding="utf-8") as handle:
                    handle.write(pem)
                # --set-pk/--add-* take: <guid> <file>
                argv.extend([flag, owner_guid, cert_file])
        _audit("varstore.build", template=template_path, out=out_path, dbs=list(certs_by_db), argv=argv)
        res = _run(argv, timeout=120)
        if res.returncode != 0:
            raise VarstoreParseError(f"virt-fw-vars failed: {res.stderr.decode(errors='replace').strip()}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    if not os.path.isfile(out_path):
        raise VarstoreParseError("virt-fw-vars did not produce an output varstore")
    return _sha256_file(out_path)


def read_varstore_state(efi: EfiDisk) -> SecureBootState:
    """
    Parse the live varstore with ``virt-fw-vars --print``.

    Only safe while the VM is stopped. Raises ToolMissingError if virt-fw-vars is
    absent. Best-effort: presence of PK/KEK/db/dbx and SecureBootEnable.
    """
    import shutil
    import tempfile

    if shutil.which("virt-fw-vars") is None:
        raise ToolMissingError("virt-fw-vars is not installed on the host")

    tmp = tempfile.NamedTemporaryFile(prefix="ovmf-vars-", suffix=".fd", delete=False)
    try:
        tmp.close()
        dd = _run(["dd", f"if={efi.device_path}", f"of={tmp.name}", "bs=1M", "count=1"], timeout=60)
        if dd.returncode != 0:
            raise VarstoreParseError(f"Failed to read varstore from {efi.device_path}")
        out = _run(["virt-fw-vars", "--input", tmp.name, "--print"], timeout=60)
        if out.returncode != 0:
            raise VarstoreParseError("virt-fw-vars --print failed")
        text = out.stdout.decode(errors="replace")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    has_pk = bool(re.search(r"\bPK\b", text))
    has_kek = bool(re.search(r"\bKEK\b", text))
    has_db = bool(re.search(r"\bdb\b", text))
    has_dbx = bool(re.search(r"\bdbx\b", text))
    enabled = "SecureBootEnable" in text and not re.search(r"SecureBootEnable[^\n]*\b0\b", text)
    mode = "UserMode" if has_pk else "SetupMode"
    return SecureBootState(
        enabled=enabled,
        mode=mode,
        has_pk=has_pk,
        has_kek=has_kek,
        has_db=has_db,
        has_dbx=has_dbx,
        source="varstore",
    )
