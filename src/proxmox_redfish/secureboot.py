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

# Standard UEFI Secure Boot databases we expose.
SB_DATABASES: List[str] = ["PK", "KEK", "db", "dbx"]
_DB_NAMES = {
    "PK": "PK - Platform Key",
    "KEK": "KEK - Key Exchange Key Database",
    "db": "db - Authorized Signature Database",
    "dbx": "dbx - Forbidden Signature Database",
}

RESET_KEYS_TYPES = ["ResetAllKeysToDefault", "DeleteAllKeys", "DeletePK"]


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
    profile_name = resolve_profile_name(profiles, f"SecureBootEnable:{str(enable).lower()}")
    try:
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
            "Name": "UEFI SecureBoot Database Collection",
            "Members@odata.count": len(SB_DATABASES),
            "Members": [{"@odata.id": f"{base}/{dbid}"} for dbid in SB_DATABASES],
        },
        200,
    )


def get_db(vmid: int, dbid: str) -> Tuple[Dict[str, Any], int]:
    if dbid not in SB_DATABASES:
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
    return (
        {
            "@odata.id": base,
            "@odata.type": "#SecureBootDatabase.v1_0_2.SecureBootDatabase",
            "Id": dbid,
            "Name": _DB_NAMES[dbid],
            "DatabaseId": dbid,
            "Certificates": {"@odata.id": f"{base}/Certificates"},
        },
        200,
    )


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
    return NOT_HANDLED
