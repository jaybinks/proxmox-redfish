#!/usr/bin/env python3
"""
redfish_services.py -- remaining top-level Redfish services for spec parity
(Phases 6-9): Chassis, AccountService/Accounts/Roles, EventService/Subscriptions,
UpdateService, plus discovery stubs (Registries / JsonSchemas).

Pure builders returning dict or (dict, status). VM-exceptions are documented inline
(VMs have no physical sensors, no firmware to update) and reported as synthetic /
Absent rather than omitted, so a conformance crawler finds a complete tree. No
import cycle; reads its node name from the environment.
"""

import ipaddress
import logging
import os
import re
import socket
import time
from typing import Any, Dict, Tuple, Union
from urllib.parse import urlparse

logger = logging.getLogger("proxmox-redfish.services")


def _iso_utc(ts: float) -> str:
    """Epoch seconds -> Redfish-friendly ISO 8601 UTC timestamp."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


PROXMOX_NODE = os.getenv("PROXMOX_NODE", "pve")


# Allow event delivery to internal/loopback/link-local targets only when the admin
# explicitly opts in. By default such destinations are blocked (SSRF defense): an
# authenticated caller must not be able to make the root daemon POST to the cloud
# metadata service (169.254.169.254), localhost services (Proxmox API on :8006), or
# RFC1918 hosts behind the daemon.
def _allow_internal_event_targets() -> bool:
    return os.getenv("REDFISH_EVENT_ALLOW_INTERNAL", "0") == "1"


def _is_blocked_ip(ip: "Union[ipaddress.IPv4Address, ipaddress.IPv6Address]") -> bool:
    return bool(
        ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved or ip.is_private
    )


def validate_event_destination(dest: str) -> Tuple[bool, str]:
    """
    SSRF guard for an event Destination URL. Returns (ok, reason).

    Enforces http(s) only and, unless REDFISH_EVENT_ALLOW_INTERNAL=1, rejects
    destinations that resolve to loopback/link-local/private/reserved addresses.
    """
    parsed = urlparse(dest)
    if parsed.scheme not in ("http", "https"):
        return False, "Destination must be an http(s) URL."
    host = parsed.hostname
    if not host:
        return False, "Destination has no host."
    if _allow_internal_event_targets():
        return True, "ok"
    # A literal internal IP is rejected outright. For a hostname, reject only when it
    # *resolves* to an internal address; an unresolvable host is allowed (delivery
    # will simply fail later and cannot reach an internal service).
    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        if _is_blocked_ip(literal_ip):
            return False, (
                "Destination resolves to a non-routable/internal address; "
                "set REDFISH_EVENT_ALLOW_INTERNAL=1 to allow."
            )
        return True, "ok"
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
        candidates = [ipaddress.ip_address(info[4][0]) for info in infos]
    except (socket.gaierror, ValueError, UnicodeError):
        return True, "ok"  # unresolvable -> cannot reach an internal target
    for ip in candidates:
        if _is_blocked_ip(ip):
            return False, (
                "Destination resolves to a non-routable/internal address; "
                "set REDFISH_EVENT_ALLOW_INTERNAL=1 to allow."
            )
    return True, "ok"


ODATA = {
    "ChassisCollection": "#ChassisCollection.ChassisCollection",
    "Chassis": "#Chassis.v1_25_0.Chassis",
    "AccountService": "#AccountService.v1_15_0.AccountService",
    "ManagerAccountCollection": "#ManagerAccountCollection.ManagerAccountCollection",
    "ManagerAccount": "#ManagerAccount.v1_12_0.ManagerAccount",
    "RoleCollection": "#RoleCollection.RoleCollection",
    "Role": "#Role.v1_3_2.Role",
    "EventService": "#EventService.v1_10_0.EventService",
    "EventDestinationCollection": "#EventDestinationCollection.EventDestinationCollection",
    "EventDestination": "#EventDestination.v1_15_0.EventDestination",
    "UpdateService": "#UpdateService.v1_14_0.UpdateService",
    "ChassisPower": "#Power.v1_7_1.Power",
    "ChassisThermal": "#Thermal.v1_7_1.Thermal",
}

# In-memory event subscriptions (process-local; mirrors the session store pattern).
subscriptions: Dict[str, Dict[str, Any]] = {}

# Upper bound on stored subscriptions to keep the in-memory store from growing
# without limit (DoS defense).
MAX_SUBSCRIPTIONS = int(os.getenv("REDFISH_MAX_SUBSCRIPTIONS", "128"))

# Standard Redfish role ids exposed (read-only).
_ROLES = {
    "Administrator": ["Login", "ConfigureManager", "ConfigureUsers", "ConfigureComponents", "ConfigureSelf"],
    "Operator": ["Login", "ConfigureComponents", "ConfigureSelf"],
    "ReadOnly": ["Login", "ConfigureSelf"],
}


# --------------------------------------------------------------------------- #
# LogService -- VM console/serial + Proxmox task log surfaced as Redfish entries
# --------------------------------------------------------------------------- #
_LOG_IDS = {
    "SEL": "System Event Log (Proxmox VM task log)",
}


def _serial_log_ids(vmid: int) -> list:
    """
    Serial LogService ids for a VM. Port 0 ('SerialLog') is always advertised so the
    resource exists even when the VM is stopped; additional ports (SerialLog1..3) are
    listed only when their QEMU socket is present (VM running with serialN: socket).
    """
    from proxmox_redfish import serial_capture

    ids = ["SerialLog"]
    for port in serial_capture.available_ports(vmid):
        if port > 0:
            ids.append(serial_capture.log_id_for_port(port))
    return ids


def build_log_service_collection(vmid: int) -> Tuple[Dict[str, Any], int]:
    base = f"/redfish/v1/Systems/{vmid}/LogServices"
    ids = list(_LOG_IDS) + _serial_log_ids(vmid)
    return (
        {
            "@odata.id": base,
            "@odata.type": "#LogServiceCollection.LogServiceCollection",
            "Name": "Log Service Collection",
            "Members@odata.count": len(ids),
            "Members": [{"@odata.id": f"{base}/{lid}"} for lid in ids],
        },
        200,
    )


def build_log_service(vmid: int, log_id: str) -> Tuple[Dict[str, Any], int]:
    from proxmox_redfish import serial_capture

    serial_port = serial_capture.port_from_log_id(log_id)
    if log_id not in _LOG_IDS and serial_port is None:
        return ({"error": {"code": "Base.1.0.ResourceMissingAtURI", "message": "log service not found"}}, 404)
    base = f"/redfish/v1/Systems/{vmid}/LogServices/{log_id}"
    is_serial = serial_port is not None
    name = f"Serial console (serial{serial_port})" if is_serial else _LOG_IDS[log_id]
    body = {
        "@odata.id": base,
        "@odata.type": "#LogService.v1_3_0.LogService",
        "Id": log_id,
        "Name": name,
        "ServiceEnabled": True,
        "OverWritePolicy": "WrapsWhenFull",
        "LogEntryType": "OEM" if is_serial else "Event",
        "Entries": {"@odata.id": f"{base}/Entries"},
    }
    if is_serial:
        body["Oem"] = {
            "Proxmox": {
                "Note": (
                    f"VM serial console (serial{serial_port}). Requires "
                    f"'serial{serial_port}: socket' on the VM and REDFISH_SERIAL_CAPTURE=1 "
                    "on the daemon; capture is from first access onward and is held in a "
                    "bounded in-memory ring buffer."
                ),
                "SerialPort": serial_port,
                "CaptureEnabled": serial_capture.capture_enabled(),
                "SocketPresent": serial_port in serial_capture.available_ports(vmid),
            }
        }
    return body, 200


def _vm_tasks(proxmox: Any, vmid: int) -> list:
    try:
        return proxmox.nodes(PROXMOX_NODE).tasks.get(vmid=vmid, limit=100) or []
    except Exception:  # noqa: BLE001 - vmid filter unsupported on old PVE -> fall back
        try:
            return [t for t in (proxmox.nodes(PROXMOX_NODE).tasks.get() or []) if str(t.get("id")) == str(vmid)]
        except Exception:  # noqa: BLE001
            return []


def build_log_entries(proxmox: Any, vmid: int, log_id: str) -> Tuple[Dict[str, Any], int]:
    from proxmox_redfish import serial_capture

    serial_port = serial_capture.port_from_log_id(log_id)
    if log_id not in _LOG_IDS and serial_port is None:
        return ({"error": {"code": "Base.1.0.ResourceMissingAtURI", "message": "log service not found"}}, 404)
    base = f"/redfish/v1/Systems/{vmid}/LogServices/{log_id}/Entries"
    members = []
    if log_id == "SEL":
        for idx, task in enumerate(_vm_tasks(proxmox, vmid), start=1):
            status = task.get("status", "")
            severity = "OK" if status in ("OK", "", "running", None) else "Critical"
            members.append(
                {
                    "@odata.id": f"{base}/{idx}",
                    "@odata.type": "#LogEntry.v1_11_0.LogEntry",
                    "Id": str(idx),
                    "Name": "Log Entry",
                    "EntryType": "Event",
                    "Severity": severity,
                    "Message": "{} ({})".format(task.get("type", "task"), status or "n/a"),
                    "MessageId": "Base.1.0.Success" if severity == "OK" else "Base.1.0.GeneralError",
                    "OemRecordFormat": "Proxmox",
                }
            )
    elif serial_port is not None:
        # Captured VM serial console (opt-in REDFISH_SERIAL_CAPTURE=1). Each output
        # line becomes an OEM LogEntry; capture is from first-access onward.
        for idx, (ts, line) in enumerate(serial_capture.get_lines(int(vmid), serial_port), start=1):
            members.append(
                {
                    "@odata.id": f"{base}/{idx}",
                    "@odata.type": "#LogEntry.v1_11_0.LogEntry",
                    "Id": str(idx),
                    "Name": "Serial Console Line",
                    "EntryType": "Oem",
                    "OemRecordFormat": "Proxmox.SerialConsole",
                    "Created": _iso_utc(ts),
                    "Message": line,
                }
            )
    return (
        {
            "@odata.id": base,
            "@odata.type": "#LogEntryCollection.LogEntryCollection",
            "Name": "Log Entry Collection",
            "Members@odata.count": len(members),
            "Members": members,
        },
        200,
    )


def build_managers_collection(proxmox: Any) -> Tuple[Dict[str, Any], int]:
    try:
        vms = proxmox.nodes(PROXMOX_NODE).qemu.get() or []
    except Exception:  # noqa: BLE001
        vms = []
    members = [{"@odata.id": f"/redfish/v1/Managers/{vm['vmid']}"} for vm in vms if "vmid" in vm]
    return (
        {
            "@odata.id": "/redfish/v1/Managers",
            "@odata.type": "#ManagerCollection.ManagerCollection",
            "Members@odata.count": len(members),
            "Members": members,
        },
        200,
    )


# --------------------------------------------------------------------------- #
# Chassis (synthetic for VMs)
# --------------------------------------------------------------------------- #
def build_chassis_collection(proxmox: Any) -> Tuple[Dict[str, Any], int]:
    try:
        vms = proxmox.nodes(PROXMOX_NODE).qemu.get() or []
    except Exception:  # noqa: BLE001
        vms = []
    members = [{"@odata.id": f"/redfish/v1/Chassis/{vm['vmid']}"} for vm in vms if "vmid" in vm]
    return (
        {
            "@odata.id": "/redfish/v1/Chassis",
            "@odata.type": ODATA["ChassisCollection"],
            # The DMTF Service-Validator's resolved ChassisCollection schema rejects a
            # "Name" property; omit it here to keep the conformance run clean.
            "Members@odata.count": len(members),
            "Members": members,
        },
        200,
    )


def build_chassis(proxmox: Any, chassis_id: str) -> Tuple[Dict[str, Any], int]:
    if not chassis_id.isdigit():
        return ({"error": {"code": "Base.1.0.ResourceMissingAtURI", "message": "chassis not found"}}, 404)
    base = f"/redfish/v1/Chassis/{chassis_id}"
    return (
        {
            "@odata.id": base,
            "@odata.type": ODATA["Chassis"],
            "Id": chassis_id,
            "Name": f"VM {chassis_id} Chassis",
            "ChassisType": "Other",  # no VM type in the enum; "Other" + Oem note
            "Status": {"State": "Enabled", "Health": "OK"},
            "Power": {"@odata.id": f"{base}/Power"},
            "Thermal": {"@odata.id": f"{base}/Thermal"},
            "Links": {
                "ComputerSystems": [{"@odata.id": f"/redfish/v1/Systems/{chassis_id}"}],
                "ManagedBy": [{"@odata.id": f"/redfish/v1/Managers/{chassis_id}"}],
            },
            "Oem": {"Proxmox": {"Synthetic": True, "Note": "VM chassis; no physical sensors."}},
        },
        200,
    )


def build_chassis_power(chassis_id: str) -> Tuple[Dict[str, Any], int]:
    base = f"/redfish/v1/Chassis/{chassis_id}/Power"
    return (
        {
            "@odata.id": base,
            "@odata.type": ODATA["ChassisPower"],
            "Id": "Power",
            "Name": "Power",
            "PowerControl": [],
            "Oem": {"Proxmox": {"Synthetic": True}},
        },
        200,
    )


def build_chassis_thermal(chassis_id: str) -> Tuple[Dict[str, Any], int]:
    base = f"/redfish/v1/Chassis/{chassis_id}/Thermal"
    return (
        {
            "@odata.id": base,
            "@odata.type": ODATA["ChassisThermal"],
            "Id": "Thermal",
            "Name": "Thermal",
            "Temperatures": [],
            "Fans": [],
            "Oem": {"Proxmox": {"Synthetic": True, "Note": "No physical thermal sensors for a VM."}},
        },
        200,
    )


# --------------------------------------------------------------------------- #
# AccountService (read-only mapping of Proxmox users/roles)
# --------------------------------------------------------------------------- #
def build_account_service() -> Dict[str, Any]:
    return {
        "@odata.id": "/redfish/v1/AccountService",
        "@odata.type": ODATA["AccountService"],
        "Id": "AccountService",
        "Name": "Account Service",
        "ServiceEnabled": True,
        "Accounts": {"@odata.id": "/redfish/v1/AccountService/Accounts"},
        "Roles": {"@odata.id": "/redfish/v1/AccountService/Roles"},
        "Oem": {"Proxmox": {"ReadOnly": True, "Note": "Identity is managed by Proxmox (pveum)."}},
    }


def _account_id(userid: str) -> str:
    """Make a URL-safe account id from a Proxmox userid (e.g. root@pam -> root_pam)."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", userid)


def build_accounts_collection(proxmox: Any) -> Tuple[Dict[str, Any], int]:
    try:
        users = proxmox.access.users.get() or []
    except Exception:  # noqa: BLE001
        users = []
    members = [
        {"@odata.id": f"/redfish/v1/AccountService/Accounts/{_account_id(u['userid'])}"}
        for u in users
        if u.get("userid")
    ]
    return (
        {
            "@odata.id": "/redfish/v1/AccountService/Accounts",
            "@odata.type": ODATA["ManagerAccountCollection"],
            "Members@odata.count": len(members),
            "Members": members,
        },
        200,
    )


def build_account(proxmox: Any, account_id: str) -> Tuple[Dict[str, Any], int]:
    try:
        users = proxmox.access.users.get() or []
    except Exception:  # noqa: BLE001
        users = []
    match = next((u for u in users if _account_id(u.get("userid", "")) == account_id), None)
    if not match:
        return ({"error": {"code": "Base.1.0.ResourceMissingAtURI", "message": "account not found"}}, 404)
    enabled = bool(match.get("enable", 1))
    return (
        {
            "@odata.id": f"/redfish/v1/AccountService/Accounts/{account_id}",
            "@odata.type": ODATA["ManagerAccount"],
            "Id": account_id,
            "Name": "User Account",
            "UserName": match.get("userid"),
            "Enabled": enabled,
            "AccountTypes": ["Redfish"],
            "RoleId": "Administrator" if match.get("userid", "").startswith("root@") else "Operator",
            "Links": {"Role": {"@odata.id": "/redfish/v1/AccountService/Roles/Administrator"}},
            "Oem": {"Proxmox": {"ReadOnly": True}},
        },
        200,
    )


def build_roles_collection() -> Dict[str, Any]:
    return {
        "@odata.id": "/redfish/v1/AccountService/Roles",
        "@odata.type": ODATA["RoleCollection"],
        "Members@odata.count": len(_ROLES),
        "Members": [{"@odata.id": f"/redfish/v1/AccountService/Roles/{r}"} for r in _ROLES],
    }


def build_role(role_id: str) -> Tuple[Dict[str, Any], int]:
    privileges = _ROLES.get(role_id)
    if privileges is None:
        return ({"error": {"code": "Base.1.0.ResourceMissingAtURI", "message": "role not found"}}, 404)
    return (
        {
            "@odata.id": f"/redfish/v1/AccountService/Roles/{role_id}",
            "@odata.type": ODATA["Role"],
            "Id": role_id,
            "Name": f"{role_id} Role",
            "RoleId": role_id,
            "IsPredefined": True,
            "AssignedPrivileges": privileges,
        },
        200,
    )


# --------------------------------------------------------------------------- #
# EventService
# --------------------------------------------------------------------------- #
def build_event_service() -> Dict[str, Any]:
    return {
        "@odata.id": "/redfish/v1/EventService",
        "@odata.type": ODATA["EventService"],
        "Id": "EventService",
        "Name": "Event Service",
        "ServiceEnabled": True,
        "DeliveryRetryAttempts": 3,
        "DeliveryRetryIntervalSeconds": 60,
        "EventFormatTypes": ["Event"],
        "ResourceTypes": ["ComputerSystem", "SecureBoot", "Manager"],
        "Subscriptions": {"@odata.id": "/redfish/v1/EventService/Subscriptions"},
        "Actions": {
            "#EventService.SubmitTestEvent": {"target": "/redfish/v1/EventService/Actions/EventService.SubmitTestEvent"}
        },
    }


def build_subscriptions_collection() -> Dict[str, Any]:
    members = [{"@odata.id": f"/redfish/v1/EventService/Subscriptions/{sid}"} for sid in subscriptions]
    return {
        "@odata.id": "/redfish/v1/EventService/Subscriptions",
        "@odata.type": ODATA["EventDestinationCollection"],
        "Members@odata.count": len(members),
        "Members": members,
    }


def _subscription_body(sid: str, sub: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "@odata.id": f"/redfish/v1/EventService/Subscriptions/{sid}",
        "@odata.type": ODATA["EventDestination"],
        "Id": sid,
        "Name": "Event Subscription",
        "Destination": sub.get("Destination"),
        "Protocol": sub.get("Protocol", "Redfish"),
        "SubscriptionType": sub.get("SubscriptionType", "RedfishEvent"),
        "Context": sub.get("Context", ""),
    }


def build_subscription(sid: str) -> Tuple[Dict[str, Any], int]:
    sub = subscriptions.get(sid)
    if not sub:
        return ({"error": {"code": "Base.1.0.ResourceMissingAtURI", "message": "subscription not found"}}, 404)
    return _subscription_body(sid, sub), 200


def create_subscription(data: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    if not isinstance(data, dict) or not data.get("Destination"):
        return (
            {
                "error": {
                    "code": "Base.1.0.PropertyValueNotInList",
                    "message": "Destination is required.",
                    "@Message.ExtendedInfo": [
                        {
                            "@odata.type": "#Message.v1_1_1.Message",
                            "MessageId": "Base.1.0.PropertyMissing",
                            "Message": "The required property Destination was not provided.",
                            "MessageSeverity": "Warning",
                            "Resolution": "Provide a Destination URI.",
                        }
                    ],
                }
            },
            400,
        )
    dest = str(data["Destination"])
    # Only allow http(s) destinations (no file:// / arbitrary schemes) and block
    # internal/metadata targets (SSRF) unless the admin explicitly opted in.
    ok, reason = validate_event_destination(dest)
    if not ok:
        return (
            {"error": {"code": "Base.1.0.PropertyValueNotInList", "message": reason}},
            400,
        )
    # Reject conflicting/unsupported Protocol values (only Redfish is supported).
    protocol = data.get("Protocol", "Redfish")
    if protocol != "Redfish":
        return (
            {
                "error": {
                    "code": "Base.1.0.PropertyValueNotInList",
                    "message": f"Unsupported subscription Protocol {protocol!r}; only 'Redfish' is supported.",
                }
            },
            400,
        )
    import hashlib

    sid = hashlib.sha256(dest.encode("utf-8")).hexdigest()[:12]
    # Bound the store: a brand-new destination is refused once the cap is reached
    # (re-subscribing an existing destination is idempotent and always allowed).
    if sid not in subscriptions and len(subscriptions) >= MAX_SUBSCRIPTIONS:
        return (
            {
                "error": {
                    "code": "Base.1.0.GeneralError",
                    "message": "Maximum number of event subscriptions reached.",
                }
            },
            507,
        )
    subscriptions[sid] = {
        "Destination": dest,
        "Protocol": data.get("Protocol", "Redfish"),
        "SubscriptionType": data.get("SubscriptionType", "RedfishEvent"),
        "Context": data.get("Context", ""),
    }
    return _subscription_body(sid, subscriptions[sid]), 201


def delete_subscription(sid: str) -> Tuple[Dict[str, Any], int]:
    if sid in subscriptions:
        del subscriptions[sid]
        return {}, 204
    return ({"error": {"code": "Base.1.0.ResourceMissingAtURI", "message": "subscription not found"}}, 404)


# --------------------------------------------------------------------------- #
# UpdateService (Absent for VMs -- no host firmware to manage)
# --------------------------------------------------------------------------- #
def build_update_service() -> Dict[str, Any]:
    return {
        "@odata.id": "/redfish/v1/UpdateService",
        "@odata.type": ODATA["UpdateService"],
        "Id": "UpdateService",
        "Name": "Update Service",
        "ServiceEnabled": False,
        "Status": {"State": "Absent", "Health": "OK"},
        "Oem": {"Proxmox": {"Note": "No firmware update surface for virtual machines."}},
    }


# --------------------------------------------------------------------------- #
# Discovery stubs
# --------------------------------------------------------------------------- #
def build_registries() -> Dict[str, Any]:
    return {
        "@odata.id": "/redfish/v1/Registries",
        "@odata.type": "#MessageRegistryFileCollection.MessageRegistryFileCollection",
        "Members@odata.count": 0,
        "Members": [],
    }


def build_json_schemas() -> Dict[str, Any]:
    return {
        "@odata.id": "/redfish/v1/JsonSchemas",
        "@odata.type": "#JsonSchemaFileCollection.JsonSchemaFileCollection",
        "Members@odata.count": 0,
        "Members": [],
    }


# --------------------------------------------------------------------------- #
# CertificateService (the daemon's own TLS certificate)
# --------------------------------------------------------------------------- #
def build_certificate_service() -> Dict[str, Any]:
    return {
        "@odata.id": "/redfish/v1/CertificateService",
        "@odata.type": "#CertificateService.v1_0_4.CertificateService",
        "Id": "CertificateService",
        "Name": "Certificate Service",
        "CertificateLocations": {"@odata.id": "/redfish/v1/CertificateService/CertificateLocations"},
    }


def build_certificate_locations() -> Dict[str, Any]:
    cert_file = os.getenv("SSL_CERT_FILE", "")
    members = []
    if cert_file:
        members.append({"@odata.id": "/redfish/v1/Managers/redfish/NetworkProtocol/HTTPS/Certificates/1"})
    return {
        "@odata.id": "/redfish/v1/CertificateService/CertificateLocations",
        "@odata.type": "#CertificateLocations.v1_0_2.CertificateLocations",
        "Id": "CertificateLocations",
        "Name": "Certificate Locations",
        "Links": {"Certificates": members, "Certificates@odata.count": len(members)},
    }


# --------------------------------------------------------------------------- #
# Account mutation (opt-in; Proxmox owns identity)
# --------------------------------------------------------------------------- #
def account_mutation_enabled() -> bool:
    return os.getenv("REDFISH_ALLOW_ACCOUNT_MUTATION", "0") == "1"


def _mutation_disabled_error() -> Tuple[Dict[str, Any], int]:
    return (
        {
            "error": {
                "code": "Base.1.0.ActionNotSupported",
                "message": "Account mutation is disabled (set REDFISH_ALLOW_ACCOUNT_MUTATION=1).",
            }
        },
        405,
    )


def create_account(proxmox: Any, data: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    if not account_mutation_enabled():
        return _mutation_disabled_error()
    if not isinstance(data, dict) or not data.get("UserName") or not data.get("Password"):
        return (
            {"error": {"code": "Base.1.0.PropertyValueNotInList", "message": "UserName and Password are required."}},
            400,
        )
    userid = str(data["UserName"])
    if "@" not in userid:
        userid += "@pam"
    try:
        proxmox.access.users.post(userid=userid, password=str(data["Password"]))
    except Exception as exc:  # noqa: BLE001
        return ({"error": {"code": "Base.1.0.GeneralError", "message": f"Account create failed: {exc}"}}, 500)
    body, _ = build_account(proxmox, _account_id(userid))
    return body, 201


def delete_account(proxmox: Any, account_id: str) -> Tuple[Dict[str, Any], int]:
    if not account_mutation_enabled():
        return _mutation_disabled_error()
    try:
        users = proxmox.access.users.get() or []
    except Exception:  # noqa: BLE001
        users = []
    match = next((u for u in users if _account_id(u.get("userid", "")) == account_id), None)
    if not match:
        return ({"error": {"code": "Base.1.0.ResourceMissingAtURI", "message": "account not found"}}, 404)
    try:
        proxmox.access.users(match["userid"]).delete()
    except Exception as exc:  # noqa: BLE001
        return ({"error": {"code": "Base.1.0.GeneralError", "message": f"Account delete failed: {exc}"}}, 500)
    return {}, 204


# --------------------------------------------------------------------------- #
# Event delivery
# --------------------------------------------------------------------------- #
def emit_event(message_id: str, message: str, severity: str = "OK") -> int:
    """
    Deliver a Redfish event to every subscription (best-effort, short timeout).
    Returns the number of successful deliveries. Never raises.
    """
    if not subscriptions:
        return 0
    import requests

    payload = {
        "@odata.type": "#Event.v1_7_0.Event",
        "Id": message_id,
        "Name": "Event",
        "Events": [
            {
                "EventType": "Other",
                "MessageId": message_id,
                "Message": message,
                "MessageSeverity": severity,
            }
        ],
    }
    # TLS verification for event delivery (default on). An admin who terminates
    # against a self-signed receiver can opt out with REDFISH_EVENT_VERIFY=false.
    verify = os.getenv("REDFISH_EVENT_VERIFY", "true").lower() == "true"
    delivered = 0
    for sid, sub in list(subscriptions.items()):
        # Re-validate at delivery time (defends against DNS rebinding between the
        # subscribe call and the actual POST).
        ok, reason = validate_event_destination(str(sub.get("Destination", "")))
        if not ok:
            logger.warning("Event delivery to %s skipped: %s", sid, reason)
            continue
        body = dict(payload)
        if sub.get("Context"):
            body["Context"] = sub["Context"]
        try:
            resp = requests.post(sub["Destination"], json=body, timeout=5, verify=verify)
            if 200 <= resp.status_code < 300:
                delivered += 1
        except Exception as exc:  # noqa: BLE001 - delivery is best-effort
            logger.warning("Event delivery to %s failed: %s", sid, exc)
    return delivered


def submit_test_event(data: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    """EventService.SubmitTestEvent: deliver a test event to all subscribers."""
    message_id = (data or {}).get("MessageId", "Base.1.0.TestMessage")
    message = (data or {}).get("Message", "Test event")
    severity = (data or {}).get("MessageSeverity", "OK")
    delivered = emit_event(message_id, message, severity)
    return (
        {
            "@odata.type": "#Message.v1_1_1.Message",
            "MessageId": "Base.1.0.Success",
            "Message": f"Test event submitted; delivered to {delivered} subscriber(s).",
            "MessageSeverity": "OK",
            "Resolution": "None",
        },
        200,
    )
