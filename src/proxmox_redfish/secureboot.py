#!/usr/bin/env python3
"""
secureboot.py -- Redfish SecureBoot surface for Proxmox VMs.

Pure request/response logic: route SecureBoot URIs, validate bodies, map Redfish
intents (SecureBootEnable / SecureBoot.ResetKeys) onto named varstore *profiles*,
and serve state from a per-VM sidecar file. All host interaction (efidisk locate,
VM stop guard, dd write) is delegated to ``hostops`` -- the only module that shells
out. See docs/spec/redfish-secureboot-api.md and docs/ARCHITECTURE.md.

Wiring: the monolith's do_GET/do_POST/do_PATCH each add one delegating branch:
``if secureboot.is_secureboot_path(parts): result = secureboot.route_*(...)``.
A returned ``NOT_HANDLED`` sentinel means "not a SecureBoot URI" -> fall through to
the existing 404.
"""

import json
import logging
import os
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from proxmox_redfish import hostops

logger = logging.getLogger("proxmox-redfish.secureboot")

# Sentinel: the path is not a SecureBoot path; the caller should 404.
NOT_HANDLED = object()

Result = Union[Tuple[Dict[str, Any], int], object]

# Writable UEFI Secure Boot databases (cert CRUD targets).
SB_DATABASES: List[str] = ["PK", "KEK", "db", "dbx"]
# Full set exposed in the collection (incl. read-only factory defaults + dbr/dbt).
SB_DATABASES_ALL: List[str] = SB_DATABASES + [
    "dbr",
    "dbt",
    "PKDefault",
    "KEKDefault",
    "dbDefault",
    "dbxDefault",
    "dbrDefault",
    "dbtDefault",
]
_DB_NAMES = {
    "PK": "PK - Platform Key",
    "KEK": "KEK - Key Exchange Key Database",
    "db": "db - Authorized Signature Database",
    "dbx": "dbx - Forbidden Signature Database",
    "dbr": "dbr - Recovery Signature Database",
    "dbt": "dbt - Timestamp Signature Database",
    "PKDefault": "PKDefault - Default Platform Key",
    "KEKDefault": "KEKDefault - Default Key Exchange Key Database",
    "dbDefault": "dbDefault - Default Authorized Signature Database",
    "dbxDefault": "dbxDefault - Default Forbidden Signature Database",
    "dbrDefault": "dbrDefault - Default Recovery Signature Database",
    "dbtDefault": "dbtDefault - Default Timestamp Signature Database",
}

RESET_KEYS_TYPES = ["ResetAllKeysToDefault", "DeleteAllKeys", "DeletePK"]
DB_RESET_KEYS_TYPES = ["ResetAllKeysToDefault", "DeleteAllKeys"]


# --------------------------------------------------------------------------- #
# Configuration: profiles & sidecar state
# --------------------------------------------------------------------------- #
def _profiles_path() -> str:
    return os.getenv("REDFISH_SB_PROFILES", "/opt/proxmox-redfish/config/secureboot_profiles.json")


def load_profiles() -> Dict[str, Any]:
    """Load the profile catalog. Returns an empty catalog if the file is absent."""
    path = _profiles_path()
    data: Dict[str, Any]
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        logger.warning("SecureBoot profiles file not found: %s", path)
        return {"profiles": {}, "map": {}, "default_profile": None}
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read SecureBoot profiles %s: %s", path, exc)
        return {"profiles": {}, "map": {}, "default_profile": None}
    data.setdefault("profiles", {})
    data.setdefault("map", {})
    data.setdefault("default_profile", None)
    return data


def resolve_profile_name(profiles: Dict[str, Any], key: str) -> Optional[str]:
    """Map a Redfish intent key (e.g. 'SecureBootEnable:true', 'DeleteAllKeys') to a profile."""
    name: Optional[str] = profiles.get("map", {}).get(key)
    if name is None:
        name = profiles.get("default_profile")
    return name


def _state_path(vmid: int) -> str:
    return os.path.join(hostops.state_dir(), f"vm-{vmid}.sb.json")


def read_state(vmid: int) -> Optional[Dict[str, Any]]:
    try:
        with open(_state_path(vmid), "r", encoding="utf-8") as handle:
            data: Dict[str, Any] = json.load(handle)
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def write_state(vmid: int, state: Dict[str, Any]) -> None:
    """Atomically persist per-VM sidecar state."""
    directory = hostops.state_dir()
    os.makedirs(directory, exist_ok=True)
    target = _state_path(vmid)
    fd, tmp = tempfile.mkstemp(prefix=f".vm-{vmid}.sb.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, sort_keys=True)
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- #
# Error envelope
# --------------------------------------------------------------------------- #
def sb_error(exc: hostops.HostOpError) -> Tuple[Dict[str, Any], int]:
    """Map a HostOpError to a Redfish extended-error response."""
    return (
        {
            "error": {
                "code": exc.redfish_code,
                "message": str(exc),
                "@Message.ExtendedInfo": [
                    {
                        "@odata.type": "#Message.v1_1_1.Message",
                        "MessageId": exc.redfish_code,
                        "Message": str(exc),
                        "MessageSeverity": "Warning",
                        "Resolution": exc.resolution,
                    }
                ],
            }
        },
        exc.status,
    )


def _value_error(message: str, prop: str) -> Tuple[Dict[str, Any], int]:
    return (
        {
            "error": {
                "code": "Base.1.0.PropertyValueNotInList",
                "message": message,
                "@Message.ExtendedInfo": [
                    {
                        "@odata.type": "#Message.v1_1_1.Message",
                        "MessageId": "Base.1.0.PropertyValueNotInList",
                        "Message": message,
                        "MessageSeverity": "Warning",
                        "Resolution": "Provide a supported value and resubmit.",
                        "RelatedProperties": [f"#/{prop}"],
                    }
                ],
            }
        },
        400,
    )


# --------------------------------------------------------------------------- #
# Response builders
# --------------------------------------------------------------------------- #
def _secureboot_body(vmid: int, state: Optional[Dict[str, Any]], profiles: Dict[str, Any]) -> Dict[str, Any]:
    enabled = bool(state.get("enabled", False)) if state else False
    mode = state.get("mode", "SetupMode") if state else "SetupMode"
    active = state.get("profile") if state else None
    current_boot = "Enabled" if enabled else "Disabled"
    base = f"/redfish/v1/Systems/{vmid}/SecureBoot"
    return {
        "@odata.id": base,
        "@odata.type": "#SecureBoot.v1_1_1.SecureBoot",
        "Id": "SecureBoot",
        "Name": "UEFI Secure Boot",
        "SecureBootEnable": enabled,
        "SecureBootMode": mode,
        "SecureBootCurrentBoot": current_boot,
        "SecureBootDatabases": {"@odata.id": f"{base}/SecureBootDatabases"},
        "Actions": {
            "#SecureBoot.ResetKeys": {
                "target": f"{base}/Actions/SecureBoot.ResetKeys",
                "ResetKeysType@Redfish.AllowableValues": RESET_KEYS_TYPES,
            }
        },
        "Oem": {
            "Proxmox": {
                "ActiveProfile": active,
                "@Redfish.AllowableProfiles": sorted(profiles.get("profiles", {}).keys()),
            }
        },
    }


# --------------------------------------------------------------------------- #
# Core operations
# --------------------------------------------------------------------------- #
def apply_profile(proxmox: Any, vmid: int, profile_name: Optional[str]) -> hostops.WriteResult:
    """Stop-guard the VM, write the profile's varstore image, and reconcile sidecar state."""
    profiles = load_profiles()
    if not profile_name:
        raise hostops.TemplateMissingError("No SecureBoot profile is configured for this operation")
    profile = profiles.get("profiles", {}).get(profile_name)
    if not profile:
        raise hostops.TemplateMissingError(f"SecureBoot profile {profile_name!r} is not defined")

    image_path = profile.get("image_path")
    if not image_path:
        raise hostops.TemplateMissingError(f"Profile {profile_name!r} has no image_path")
    expected_sha = profile.get("image_sha256")
    allow_autostop = os.getenv("REDFISH_SB_ALLOW_AUTOSTOP", "0") == "1"

    with hostops.stopped_vm_guard(proxmox, vmid, allow_autostop=allow_autostop):
        efi = hostops.locate_efidisk(proxmox, vmid)
        result = hostops.write_varstore_image(efi, image_path, expected_sha256=expected_sha)

    databases = profile.get("databases", {})
    enabled = bool(profile.get("secure_boot", False))
    state = {
        "enabled": enabled,
        "profile": profile_name,
        "mode": "UserMode" if databases.get("PK") else "SetupMode",
        "has_pk": bool(databases.get("PK", False)),
        "has_kek": bool(databases.get("KEK", False)),
        "has_db": bool(databases.get("db", False)),
        "has_dbx": bool(databases.get("dbx", False)),
        "applied_at": time.time(),
        "image_sha256": result.image_sha256,
        "dry_run": result.dry_run,
    }
    write_state(vmid, state)
    return result


def get_secureboot(proxmox: Any, vmid: int) -> Tuple[Dict[str, Any], int]:
    try:
        hostops.locate_efidisk(proxmox, vmid)  # validates efidisk presence + efitype
    except hostops.HostOpError as exc:
        return sb_error(exc)
    profiles = load_profiles()
    state = read_state(vmid)
    return _secureboot_body(vmid, state, profiles), 200


def patch_secureboot(proxmox: Any, vmid: int, data: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    if not isinstance(data, dict) or "SecureBootEnable" not in data:
        return _value_error("PATCH body must include the SecureBootEnable property.", "SecureBootEnable")
    enable = data["SecureBootEnable"]
    if not isinstance(enable, bool):
        return _value_error("SecureBootEnable must be a boolean.", "SecureBootEnable")
    profiles = load_profiles()
    try:
        # Dynamic path: if public certs have been staged for this VM, build a varstore
        # from them. Otherwise fall back to the static profile image.
        if enable and _staged_certs_by_db(vmid):
            result = apply_staged_certs(proxmox, vmid)
        else:
            profile_name = resolve_profile_name(profiles, f"SecureBootEnable:{str(enable).lower()}")
            result = apply_profile(proxmox, vmid, profile_name)
    except hostops.HostOpError as exc:
        return sb_error(exc)
    state = read_state(vmid)
    body = _secureboot_body(vmid, state, profiles)
    body["Oem"]["Proxmox"]["LastOperation"] = result.message
    body["Oem"]["Proxmox"]["DryRun"] = result.dry_run
    return body, 200


def action_reset_keys(proxmox: Any, vmid: int, data: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    reset_type = data.get("ResetKeysType") if isinstance(data, dict) else None
    if reset_type not in RESET_KEYS_TYPES:
        return _value_error(f"ResetKeysType {reset_type!r} is not one of {RESET_KEYS_TYPES}.", "ResetKeysType")
    profiles = load_profiles()
    profile_name = resolve_profile_name(profiles, reset_type)
    try:
        result = apply_profile(proxmox, vmid, profile_name)
    except hostops.HostOpError as exc:
        return sb_error(exc)
    return (
        {
            "@odata.type": "#Message.v1_1_1.Message",
            "MessageId": "Base.1.0.Success",
            "Message": f"SecureBoot.ResetKeys ({reset_type}) applied via profile {profile_name!r}: {result.message}",
            "MessageSeverity": "OK",
            "Resolution": "None",
            "Oem": {"Proxmox": {"DryRun": result.dry_run, "Profile": profile_name}},
        },
        200,
    )


def get_db_collection(vmid: int) -> Tuple[Dict[str, Any], int]:
    base = f"/redfish/v1/Systems/{vmid}/SecureBoot/SecureBootDatabases"
    return (
        {
            "@odata.id": base,
            "@odata.type": "#SecureBootDatabaseCollection.SecureBootDatabaseCollection",
            "Members@odata.count": len(SB_DATABASES_ALL),
            "Members": [{"@odata.id": f"{base}/{dbid}"} for dbid in SB_DATABASES_ALL],
        },
        200,
    )


def get_db(vmid: int, dbid: str) -> Tuple[Dict[str, Any], int]:
    if dbid not in SB_DATABASES_ALL:
        return (
            {
                "error": {
                    "code": "Base.1.0.ResourceMissingAtURI",
                    "message": f"SecureBoot database {dbid!r} not found.",
                }
            },
            404,
        )
    base = f"/redfish/v1/Systems/{vmid}/SecureBoot/SecureBootDatabases/{dbid}"
    body: Dict[str, Any] = {
        "@odata.id": base,
        "@odata.type": "#SecureBootDatabase.v1_0_2.SecureBootDatabase",
        "Id": dbid,
        "Name": _DB_NAMES[dbid],
        "DatabaseId": dbid,
        "Certificates": {"@odata.id": f"{base}/Certificates"},
        "Signatures": {"@odata.id": f"{base}/Signatures"},
    }
    # Writable databases advertise the per-database ResetKeys action.
    if dbid in SB_DATABASES:
        body["Actions"] = {
            "#SecureBootDatabase.ResetKeys": {
                "target": f"{base}/Actions/SecureBootDatabase.ResetKeys",
                "ResetKeysType@Redfish.AllowableValues": DB_RESET_KEYS_TYPES,
            }
        }
    return body, 200


def get_signatures_collection(vmid: int, dbid: str) -> Tuple[Dict[str, Any], int]:
    if dbid not in SB_DATABASES_ALL:
        return ({"error": {"code": "Base.1.0.ResourceMissingAtURI", "message": f"db {dbid} not found"}}, 404)
    base = f"/redfish/v1/Systems/{vmid}/SecureBoot/SecureBootDatabases/{dbid}/Signatures"
    return (
        {
            "@odata.id": base,
            "@odata.type": "#SignatureCollection.SignatureCollection",
            "Members@odata.count": 0,
            "Members": [],
        },
        200,
    )


def db_reset_keys(vmid: int, dbid: str, data: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    """Per-database SecureBootDatabase.ResetKeys: clears this db's staged certs."""
    if dbid not in SB_DATABASES:
        return ({"error": {"code": "Base.1.0.ResourceMissingAtURI", "message": f"db {dbid} not found"}}, 404)
    reset_type = data.get("ResetKeysType") if isinstance(data, dict) else None
    if reset_type not in DB_RESET_KEYS_TYPES:
        return _value_error(f"ResetKeysType {reset_type!r} not in {DB_RESET_KEYS_TYPES}.", "ResetKeysType")
    import shutil

    shutil.rmtree(_certs_dir(vmid, dbid), ignore_errors=True)
    return (
        {
            "@odata.type": "#Message.v1_1_1.Message",
            "MessageId": "Base.1.0.Success",
            "Message": f"SecureBootDatabase.ResetKeys ({reset_type}) cleared staged certs for {dbid}.",
            "MessageSeverity": "OK",
            "Resolution": "None",
        },
        200,
    )


# --------------------------------------------------------------------------- #
# Certificate staging + CRUD (Phase 4)
# --------------------------------------------------------------------------- #
def _certs_dir(vmid: int, dbid: str) -> str:
    return os.path.join(hostops.state_dir(), f"vm-{vmid}", "sb-certs", dbid)


def _cert_id(pem: str) -> str:
    """Deterministic, traversal-safe id derived from the cert content."""
    import hashlib

    return hashlib.sha256(pem.encode("utf-8")).hexdigest()[:16]


def _list_staged(vmid: int, dbid: str) -> List[str]:
    directory = _certs_dir(vmid, dbid)
    try:
        return sorted(f[:-4] for f in os.listdir(directory) if f.endswith(".pem"))
    except FileNotFoundError:
        return []


def _read_staged(vmid: int, dbid: str, cert_id: str) -> Optional[str]:
    # cert_id is our own 16-hex-char id; reject anything else (no path traversal).
    if not (len(cert_id) == 16 and all(c in "0123456789abcdef" for c in cert_id)):
        return None
    path = os.path.join(_certs_dir(vmid, dbid), f"{cert_id}.pem")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except (FileNotFoundError, OSError):
        return None


def _cert_body(vmid: int, dbid: str, cert_id: str, pem: str) -> Dict[str, Any]:
    base = f"/redfish/v1/Systems/{vmid}/SecureBoot/SecureBootDatabases/{dbid}/Certificates/{cert_id}"
    body: Dict[str, Any] = {
        "@odata.id": base,
        "@odata.type": "#Certificate.v1_11_0.Certificate",
        "Id": cert_id,
        "Name": f"{dbid} Certificate",
        "CertificateType": "PEM",
        "CertificateString": pem,
    }
    # Enrich with parsed metadata when cryptography is available (best-effort).
    try:
        from cryptography import x509

        cert = x509.load_pem_x509_certificate(pem.encode("utf-8"))
        body["ValidNotBefore"] = cert.not_valid_before_utc.isoformat()
        body["ValidNotAfter"] = cert.not_valid_after_utc.isoformat()
        body["SerialNumber"] = hex(cert.serial_number)
        body["Subject"] = {"CommonName": cert.subject.rfc4514_string()}
        body["Issuer"] = {"CommonName": cert.issuer.rfc4514_string()}
    except Exception:  # noqa: BLE001 - metadata is optional, never fail the GET
        pass
    return body


def get_cert_collection(vmid: int, dbid: str) -> Tuple[Dict[str, Any], int]:
    if dbid not in SB_DATABASES_ALL:
        return ({"error": {"code": "Base.1.0.ResourceMissingAtURI", "message": f"db {dbid} not found"}}, 404)
    base = f"/redfish/v1/Systems/{vmid}/SecureBoot/SecureBootDatabases/{dbid}/Certificates"
    # Read-only/default databases never have staged certs.
    members = [{"@odata.id": f"{base}/{cid}"} for cid in _list_staged(vmid, dbid)] if dbid in SB_DATABASES else []
    return (
        {
            "@odata.id": base,
            "@odata.type": "#CertificateCollection.CertificateCollection",
            "Members@odata.count": len(members),
            "Members": members,
        },
        200,
    )


def get_cert(vmid: int, dbid: str, cert_id: str) -> Tuple[Dict[str, Any], int]:
    pem = _read_staged(vmid, dbid, cert_id)
    if pem is None:
        return ({"error": {"code": "Base.1.0.ResourceMissingAtURI", "message": "certificate not found"}}, 404)
    return _cert_body(vmid, dbid, cert_id, pem), 200


def add_cert(vmid: int, dbid: str, data: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    if dbid not in SB_DATABASES:
        return ({"error": {"code": "Base.1.0.ResourceMissingAtURI", "message": f"db {dbid} not found"}}, 404)
    if not isinstance(data, dict) or "CertificateString" not in data:
        return _value_error("POST body must include CertificateString.", "CertificateString")
    cert_string = data["CertificateString"]
    cert_type = data.get("CertificateType", "PEM")
    if not isinstance(cert_string, str):
        return _value_error("CertificateString must be a string.", "CertificateString")
    try:
        normalized = hostops.validate_public_certificate(cert_string, cert_type)
    except hostops.HostOpError as exc:
        return sb_error(exc)
    cert_id = _cert_id(normalized)
    directory = _certs_dir(vmid, dbid)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{cert_id}.pem")
    fd, tmp = tempfile.mkstemp(prefix=f".{cert_id}.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(normalized)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return _cert_body(vmid, dbid, cert_id, normalized), 201


def delete_cert(vmid: int, dbid: str, cert_id: str) -> Tuple[Dict[str, Any], int]:
    if dbid not in SB_DATABASES:
        return ({"error": {"code": "Base.1.0.ResourceMissingAtURI", "message": f"db {dbid} not found"}}, 404)
    if _read_staged(vmid, dbid, cert_id) is None:
        return ({"error": {"code": "Base.1.0.ResourceMissingAtURI", "message": "certificate not found"}}, 404)
    os.unlink(os.path.join(_certs_dir(vmid, dbid), f"{cert_id}.pem"))
    return {}, 204


def _staged_certs_by_db(vmid: int) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for dbid in SB_DATABASES:
        pems = [p for cid in _list_staged(vmid, dbid) if (p := _read_staged(vmid, dbid, cid))]
        if pems:
            result[dbid] = pems
    return result


def apply_staged_certs(proxmox: Any, vmid: int) -> hostops.WriteResult:
    """Dynamic build: assemble a varstore from staged public certs and enroll it."""
    certs_by_db = _staged_certs_by_db(vmid)
    if not certs_by_db:
        raise hostops.TemplateMissingError("No staged certificates to build a varstore from")
    template = os.getenv("REDFISH_SB_BLANK_TEMPLATE", os.path.join(hostops.varstore_dir(), "OVMF_VARS_4M.blank.fd"))
    out_path = os.path.join(hostops.varstore_dir(), f"built-vm-{vmid}.fd")
    sha = hostops.build_varstore_from_certs(template, certs_by_db, out_path=out_path, secure_boot=True)
    allow_autostop = os.getenv("REDFISH_SB_ALLOW_AUTOSTOP", "0") == "1"
    with hostops.stopped_vm_guard(proxmox, vmid, allow_autostop=allow_autostop):
        efi = hostops.locate_efidisk(proxmox, vmid)
        result = hostops.write_varstore_image(efi, out_path, expected_sha256=sha)
    state = {
        "enabled": True,
        "profile": "dynamic-certs",
        "mode": "UserMode" if "PK" in certs_by_db else "SetupMode",
        "has_pk": "PK" in certs_by_db,
        "has_kek": "KEK" in certs_by_db,
        "has_db": "db" in certs_by_db,
        "has_dbx": "dbx" in certs_by_db,
        "applied_at": time.time(),
        "image_sha256": result.image_sha256,
        "dry_run": result.dry_run,
    }
    write_state(vmid, state)
    return result


# --------------------------------------------------------------------------- #
# Routers (called from the monolith dispatchers)
# --------------------------------------------------------------------------- #
def is_secureboot_path(parts: List[str]) -> bool:
    """parts == path.rstrip('/').split('/'); matches /redfish/v1/Systems/<id>/SecureBoot..."""
    return len(parts) >= 6 and parts[3] == "Systems" and parts[5] == "SecureBoot"


def _vmid_from_parts(parts: List[str]) -> int:
    return hostops.validate_vmid(parts[4])


def route_get(proxmox: Any, parts: List[str]) -> Result:
    if not is_secureboot_path(parts):
        return NOT_HANDLED
    try:
        vmid = _vmid_from_parts(parts)
    except hostops.HostOpError as exc:
        return sb_error(exc)

    if len(parts) == 6:  # /SecureBoot
        return get_secureboot(proxmox, vmid)
    if len(parts) == 7 and parts[6] == "SecureBootDatabases":
        return get_db_collection(vmid)
    if len(parts) == 8 and parts[6] == "SecureBootDatabases":
        return get_db(vmid, parts[7])
    if len(parts) == 9 and parts[6] == "SecureBootDatabases" and parts[8] == "Certificates":
        return get_cert_collection(vmid, parts[7])
    if len(parts) == 9 and parts[6] == "SecureBootDatabases" and parts[8] == "Signatures":
        return get_signatures_collection(vmid, parts[7])
    if len(parts) == 10 and parts[6] == "SecureBootDatabases" and parts[8] == "Certificates":
        return get_cert(vmid, parts[7], parts[9])
    return NOT_HANDLED


def route_patch(proxmox: Any, parts: List[str], data: Dict[str, Any]) -> Result:
    if not is_secureboot_path(parts):
        return NOT_HANDLED
    try:
        vmid = _vmid_from_parts(parts)
    except hostops.HostOpError as exc:
        return sb_error(exc)
    if len(parts) == 6:  # PATCH /SecureBoot
        return patch_secureboot(proxmox, vmid, data)
    return NOT_HANDLED


def route_post(proxmox: Any, parts: List[str], data: Dict[str, Any]) -> Result:
    if not is_secureboot_path(parts):
        return NOT_HANDLED
    try:
        vmid = _vmid_from_parts(parts)
    except hostops.HostOpError as exc:
        return sb_error(exc)
    # /SecureBoot/Actions/SecureBoot.ResetKeys
    if len(parts) == 8 and parts[6] == "Actions" and parts[7] == "SecureBoot.ResetKeys":
        return action_reset_keys(proxmox, vmid, data)
    # POST .../SecureBootDatabases/{db}/Certificates
    if len(parts) == 9 and parts[6] == "SecureBootDatabases" and parts[8] == "Certificates":
        return add_cert(vmid, parts[7], data)
    # POST .../SecureBootDatabases/{db}/Actions/SecureBootDatabase.ResetKeys
    if (
        len(parts) == 10
        and parts[6] == "SecureBootDatabases"
        and parts[8] == "Actions"
        and parts[9] == "SecureBootDatabase.ResetKeys"
    ):
        return db_reset_keys(vmid, parts[7], data)
    return NOT_HANDLED


def route_delete(proxmox: Any, parts: List[str]) -> Result:
    if not is_secureboot_path(parts):
        return NOT_HANDLED
    try:
        vmid = _vmid_from_parts(parts)
    except hostops.HostOpError as exc:
        return sb_error(exc)
    # DELETE .../SecureBootDatabases/{db}/Certificates/{id}
    if len(parts) == 10 and parts[6] == "SecureBootDatabases" and parts[8] == "Certificates":
        return delete_cert(vmid, parts[7], parts[9])
    return NOT_HANDLED
