#!/usr/bin/env python3
"""
Mock-backed Redfish daemon launcher for conformance validation.

Starts the real RedfishRequestHandler with a canned, in-memory Proxmox backend so
the DMTF Redfish-Service-Validator (and our own structural harness) can crawl the
full resource tree over plain HTTP with no real Proxmox host. Auth is stubbed.

Usage:
    python tools/mock_server.py [port]        # default 8000
"""

import os
import sys
from unittest.mock import MagicMock

os.environ.setdefault("REDFISH_LOGGING_ENABLED", "false")
os.environ.setdefault("PROXMOX_NODE", "pve")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import proxmox_redfish.proxmox_redfish as mod  # noqa: E402

# ---- Canned Proxmox backend ------------------------------------------------
VM_CONFIG = {
    "name": "mock-vm",
    "memory": 4096,
    "cores": 2,
    "sockets": 1,
    "bios": "ovmf",
    "boot": "order=scsi0;ide2;net0",
    "scsi0": "local-lvm:vm-100-disk-1,size=32G",
    "ide2": "none,media=cdrom",
    "net0": "virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0",
    "efidisk0": "local-lvm:vm-100-disk-0,efitype=4m,pre-enrolled-keys=1,size=528K",
}


def _make_proxmox():
    proxmox = MagicMock()
    node = proxmox.nodes.return_value
    node.qemu.get.return_value = [{"vmid": 100, "name": "mock-vm", "status": "running"}]
    vm = node.qemu.return_value
    vm.config.get.return_value = dict(VM_CONFIG)
    vm.status.current.get.return_value = {"status": "running", "qmpstatus": "running"}
    # Power/config actions return a UPID string (JSON-serializable Task id).
    upid = "UPID:pve:0000ABCD:00000000:00000000:qmstart:100:root@pam:"
    for action in ("start", "stop", "reset", "shutdown", "reboot", "suspend", "resume"):
        getattr(vm.status, action).post.return_value = upid
    vm.config.set.return_value = upid
    vm.config.post.return_value = upid
    node.tasks.get.return_value = []
    node.tasks.return_value.status.get.return_value = {"status": "stopped", "exitstatus": "OK", "type": "qmstart"}
    proxmox.access.users.get.return_value = [{"userid": "root@pam", "enable": 1}]
    return proxmox


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    proxmox = _make_proxmox()

    # Enforce auth like the real daemon (so security assertions are exercised):
    # any request without a Basic/Token credential is rejected.
    def _validate(headers):  # noqa: ANN001
        if headers.get("Authorization") or headers.get("X-Auth-Token"):
            return (True, "mock@pam")
        return (False, "Authentication required")

    mod.validate_token = _validate  # type: ignore[assignment]
    mod.get_proxmox_api = lambda headers: proxmox  # type: ignore[assignment]
    mod.ProxmoxAPI = lambda *a, **k: proxmox  # type: ignore[assignment]
    # Enable Redfish session login (POST /SessionService/Sessions).
    mod.AUTH = "Session"
    # Validators expect full DSP0266 strictness (412 / 501); set MOCK_STRICT=0 to
    # run lenient (e.g. for real-client compatibility tests).
    mod.STRICT_PROTOCOL = os.getenv("MOCK_STRICT", "1") == "1"
    # SecureBoot locate_efidisk would shell out; return a canned EfiDisk.
    mod.secureboot.hostops.locate_efidisk = lambda p, v: mod.secureboot.hostops.EfiDisk(  # type: ignore[assignment]
        "local-lvm:vm-100-disk-0", "local-lvm", "/dev/pve/vm-100-disk-0", "4m", True, 540672
    )

    tls = "https" in sys.argv or "--tls" in sys.argv
    if tls:
        cert, key = _self_signed_cert()
        mod.SSL_CERT_FILE = cert
        mod.SSL_KEY_FILE = key
        mod.SSL_CA_FILE = "/nonexistent-ca.crt"
        print(f"Mock Redfish daemon on https://0.0.0.0:{port} (self-signed TLS, Proxmox stubbed)")
        mod.run_server_ssl(port)
    else:
        print(f"Mock Redfish daemon on http://0.0.0.0:{port} (Proxmox backend stubbed)")
        mod.run_server(port)


def _self_signed_cert():
    """Generate a throwaway self-signed cert; return (cert_path, key_path)."""
    import datetime
    import tempfile

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2020, 1, 1))
        .not_valid_after(datetime.datetime(2040, 1, 1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cdir = tempfile.mkdtemp(prefix="mock-tls-")
    cert_path = os.path.join(cdir, "server.crt")
    key_path = os.path.join(cdir, "server.key")
    with open(cert_path, "wb") as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as fh:
        fh.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
    return cert_path, key_path


if __name__ == "__main__":
    main()
