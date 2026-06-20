#!/usr/bin/env python3
"""
redfish_core.py -- spec-hygiene Redfish resources (Phase 2/3 parity work).

Pure builders for ServiceRoot, SessionService/Sessions, TaskService/Tasks, and the
Memory resource. Kept out of the monolith for testability; each function takes a
proxmoxer client (where it needs Proxmox data) and returns either a dict or a
(dict, status_code) tuple, matching the handler's contract. No import cycle: this
module imports nothing from proxmox_redfish.proxmox_redfish and reads its own node
name from the environment.
"""

import os
from typing import Any, Dict, List, Optional, Tuple

PROXMOX_NODE = os.getenv("PROXMOX_NODE", "pve")

# Current @odata.type versions (bumped from the legacy v1_0_0 the daemon emitted).
# These track the pinned schema mirror in docs/redfish-reference/schemas/.
ODATA_TYPES = {
    "ServiceRoot": "#ServiceRoot.v1_16_0.ServiceRoot",
    "ComputerSystem": "#ComputerSystem.v1_22_0.ComputerSystem",
    "Manager": "#Manager.v1_16_0.Manager",
    "Task": "#Task.v1_7_3.Task",
    "TaskService": "#TaskService.v1_2_0.TaskService",
    "SessionService": "#SessionService.v1_1_9.SessionService",
    "Session": "#Session.v1_7_0.Session",
    "SessionCollection": "#SessionCollection.SessionCollection",
    "TaskCollection": "#TaskCollection.TaskCollection",
    "MemoryCollection": "#MemoryCollection.MemoryCollection",
    "Memory": "#Memory.v1_19_0.Memory",
}

# Redfish protocol version this service targets.
REDFISH_VERSION = "1.18.0"


def service_root_uuid() -> str:
    return os.getenv("REDFISH_SERVICE_UUID", "00000000-0000-0000-0000-000000000000")


# --------------------------------------------------------------------------- #
# ServiceRoot
# --------------------------------------------------------------------------- #
def build_service_root() -> Dict[str, Any]:
    """Complete ServiceRoot advertising every implemented top-level resource."""
    return {
        "@odata.id": "/redfish/v1",
        "@odata.type": ODATA_TYPES["ServiceRoot"],
        "Id": "RootService",
        "Name": "Proxmox Redfish Service",
        "RedfishVersion": REDFISH_VERSION,
        "UUID": service_root_uuid(),
        "Systems": {"@odata.id": "/redfish/v1/Systems"},
        "Managers": {"@odata.id": "/redfish/v1/Managers"},
        "SessionService": {"@odata.id": "/redfish/v1/SessionService"},
        "TaskService": {"@odata.id": "/redfish/v1/TaskService"},
        "Links": {"Sessions": {"@odata.id": "/redfish/v1/SessionService/Sessions"}},
    }


# --------------------------------------------------------------------------- #
# SessionService / Sessions
# --------------------------------------------------------------------------- #
def build_session_service() -> Dict[str, Any]:
    return {
        "@odata.id": "/redfish/v1/SessionService",
        "@odata.type": ODATA_TYPES["SessionService"],
        "Id": "SessionService",
        "Name": "Session Service",
        "ServiceEnabled": True,
        "SessionTimeout": int(os.getenv("REDFISH_SESSION_TIMEOUT", "3600")),
        "Sessions": {"@odata.id": "/redfish/v1/SessionService/Sessions"},
    }


def build_sessions_collection(sessions: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    members = [{"@odata.id": f"/redfish/v1/SessionService/Sessions/{tok}"} for tok in sessions]
    return {
        "@odata.id": "/redfish/v1/SessionService/Sessions",
        "@odata.type": ODATA_TYPES["SessionCollection"],
        "Name": "Session Collection",
        "Members@odata.count": len(members),
        "Members": members,
    }


def build_session(token: str, sessions: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, Any], int]:
    session = sessions.get(token)
    if not session:
        return (
            {
                "error": {
                    "code": "Base.1.0.ResourceMissingAtURI",
                    "message": f"Session {token} not found.",
                }
            },
            404,
        )
    return (
        {
            "@odata.id": f"/redfish/v1/SessionService/Sessions/{token}",
            "@odata.type": ODATA_TYPES["Session"],
            "Id": token,
            "Name": "User Session",
            "UserName": session.get("username"),
        },
        200,
    )


def delete_session(token: str, sessions: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, Any], int]:
    """Logout: remove the session token. Idempotent-ish: 404 if unknown."""
    if token in sessions:
        del sessions[token]
        return {}, 204
    return (
        {"error": {"code": "Base.1.0.ResourceMissingAtURI", "message": f"Session {token} not found."}},
        404,
    )


# --------------------------------------------------------------------------- #
# TaskService / Tasks  (maps Proxmox UPID tasks to Redfish Tasks)
# --------------------------------------------------------------------------- #
def build_task_service() -> Dict[str, Any]:
    return {
        "@odata.id": "/redfish/v1/TaskService",
        "@odata.type": ODATA_TYPES["TaskService"],
        "Id": "TaskService",
        "Name": "Task Service",
        "ServiceEnabled": True,
        "CompletedTaskOverWritePolicy": "Oldest",
        "LifeCycleEventOnTaskStateChange": False,
        "Tasks": {"@odata.id": "/redfish/v1/TaskService/Tasks"},
    }


def _map_task_state(status: str, exitstatus: Optional[str]) -> Tuple[str, str, int]:
    """Map a Proxmox task (status/exitstatus) to (TaskState, TaskStatus, PercentComplete)."""
    if status == "running":
        return "Running", "OK", 50
    # stopped
    if exitstatus in (None, "OK") or (isinstance(exitstatus, str) and exitstatus.upper() == "OK"):
        return "Completed", "OK", 100
    return "Exception", "Critical", 100


def build_task(proxmox: Any, task_id: str) -> Tuple[Dict[str, Any], int]:
    """
    Resolve a Proxmox UPID to a Redfish Task. The UPID is what power/bios/config
    actions return and embed in their 202 ``Location``.
    """
    try:
        status = proxmox.nodes(PROXMOX_NODE).tasks(task_id).status.get()
    except Exception as exc:  # noqa: BLE001 - normalize to a Redfish 404/500
        return (
            {"error": {"code": "Base.1.0.ResourceMissingAtURI", "message": f"Task {task_id} not found: {exc}"}},
            404,
        )
    if not status:
        return (
            {"error": {"code": "Base.1.0.ResourceMissingAtURI", "message": f"Task {task_id} not found."}},
            404,
        )
    state, task_status, percent = _map_task_state(status.get("status", ""), status.get("exitstatus"))
    body: Dict[str, Any] = {
        "@odata.id": f"/redfish/v1/TaskService/Tasks/{task_id}",
        "@odata.type": ODATA_TYPES["Task"],
        "Id": task_id,
        "Name": status.get("type", "Proxmox Task"),
        "TaskState": state,
        "TaskStatus": task_status,
        "PercentComplete": percent,
    }
    if state == "Exception":
        body["Messages"] = [
            {
                "@odata.type": "#Message.v1_1_1.Message",
                "MessageId": "Base.1.0.GeneralError",
                "Message": f"Task failed: {status.get('exitstatus')}",
                "MessageSeverity": "Critical",
            }
        ]
    return body, 200


def build_task_collection(proxmox: Any) -> Tuple[Dict[str, Any], int]:
    """List recent node tasks as Redfish Task members."""
    try:
        tasks = proxmox.nodes(PROXMOX_NODE).tasks.get() or []
    except Exception as exc:  # noqa: BLE001
        return (
            {"error": {"code": "Base.1.0.GeneralError", "message": f"Failed to list tasks: {exc}"}},
            500,
        )
    members = [{"@odata.id": f"/redfish/v1/TaskService/Tasks/{t.get('upid')}"} for t in tasks if t.get("upid")]
    return (
        {
            "@odata.id": "/redfish/v1/TaskService/Tasks",
            "@odata.type": ODATA_TYPES["TaskCollection"],
            "Name": "Task Collection",
            "Members@odata.count": len(members),
            "Members": members,
        },
        200,
    )


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #
def _vm_memory_mib(proxmox: Any, vm_id: int) -> int:
    config = proxmox.nodes(PROXMOX_NODE).qemu(vm_id).config.get() or {}
    try:
        return int(config.get("memory", 0))
    except (TypeError, ValueError):
        return 0


def build_memory_collection(proxmox: Any, vm_id: int) -> Tuple[Dict[str, Any], int]:
    base = f"/redfish/v1/Systems/{vm_id}/Memory"
    return (
        {
            "@odata.id": base,
            "@odata.type": ODATA_TYPES["MemoryCollection"],
            "Name": "Memory Collection",
            "Members@odata.count": 1,
            "Members": [{"@odata.id": f"{base}/DRAM"}],
        },
        200,
    )


def build_memory(proxmox: Any, vm_id: int, memory_id: str) -> Tuple[Dict[str, Any], int]:
    if memory_id != "DRAM":
        return (
            {"error": {"code": "Base.1.0.ResourceMissingAtURI", "message": f"Memory {memory_id} not found."}},
            404,
        )
    capacity_mib = _vm_memory_mib(proxmox, vm_id)
    return (
        {
            "@odata.id": f"/redfish/v1/Systems/{vm_id}/Memory/DRAM",
            "@odata.type": ODATA_TYPES["Memory"],
            "Id": "DRAM",
            "Name": "System Memory",
            "MemoryType": "DRAM",
            "CapacityMiB": capacity_mib,
            "Status": {"State": "Enabled", "Health": "OK"},
        },
        200,
    )


# --------------------------------------------------------------------------- #
# ResetType reconciliation -- the canonical set this service supports.
# --------------------------------------------------------------------------- #
# Advertised == handled (see do_POST). Pause/Resume are accepted as Proxmox-specific
# extras but are NOT advertised in AllowableValues (they are not standard ResetType).
RESET_TYPES_SUPPORTED: List[str] = [
    "On",
    "ForceOff",
    "GracefulShutdown",
    "GracefulRestart",
    "ForceRestart",
    "Nmi",
    "PowerCycle",
]
