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

import os
import re
from typing import Any, Dict, Tuple

PROXMOX_NODE = os.getenv("PROXMOX_NODE", "pve")

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

# Standard Redfish role ids exposed (read-only).
_ROLES = {
    "Administrator": ["Login", "ConfigureManager", "ConfigureUsers", "ConfigureComponents", "ConfigureSelf"],
    "Operator": ["Login", "ConfigureComponents", "ConfigureSelf"],
    "ReadOnly": ["Login", "ConfigureSelf"],
}


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
            "Name": "Chassis Collection",
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
            "ChassisType": "VirtualMachine",
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
            "Name": "Accounts Collection",
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
        "Name": "Roles Collection",
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
    }


def build_subscriptions_collection() -> Dict[str, Any]:
    members = [{"@odata.id": f"/redfish/v1/EventService/Subscriptions/{sid}"} for sid in subscriptions]
    return {
        "@odata.id": "/redfish/v1/EventService/Subscriptions",
        "@odata.type": ODATA["EventDestinationCollection"],
        "Name": "Event Subscriptions Collection",
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
    # Only allow http(s) destinations (no file:// / arbitrary schemes).
    if not re.match(r"^https?://", dest):
        return (
            {"error": {"code": "Base.1.0.PropertyValueNotInList", "message": "Destination must be http(s)."}},
            400,
        )
    import hashlib

    sid = hashlib.sha256(dest.encode("utf-8")).hexdigest()[:12]
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
        "Name": "Registry File Collection",
        "Members@odata.count": 0,
        "Members": [],
    }


def build_json_schemas() -> Dict[str, Any]:
    return {
        "@odata.id": "/redfish/v1/JsonSchemas",
        "@odata.type": "#JsonSchemaFileCollection.JsonSchemaFileCollection",
        "Name": "JsonSchema File Collection",
        "Members@odata.count": 0,
        "Members": [],
    }
