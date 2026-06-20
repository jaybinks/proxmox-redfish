#!/usr/bin/env python3
"""
Unit tests for proxmox_redfish.redfish_core -- ServiceRoot, SessionService,
TaskService, Memory (Phase 2/3 spec-hygiene resources). Proxmox API mocked.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))

from proxmox_redfish import redfish_core  # noqa: E402
from proxmox_redfish.proxmox_redfish import power_cycle  # noqa: E402


# --------------------------------------------------------------------------- #
# ServiceRoot
# --------------------------------------------------------------------------- #
class TestServiceRoot:
    def test_advertises_all_implemented_services(self):
        root = redfish_core.build_service_root()
        for key in ("Systems", "Managers", "SessionService", "TaskService"):
            assert root[key]["@odata.id"].startswith("/redfish/v1")
        assert root["Links"]["Sessions"]["@odata.id"] == "/redfish/v1/SessionService/Sessions"

    def test_version_and_uuid(self, monkeypatch):
        monkeypatch.setenv("REDFISH_SERVICE_UUID", "abc-123")
        root = redfish_core.build_service_root()
        assert root["RedfishVersion"] == redfish_core.REDFISH_VERSION
        assert root["UUID"] == "abc-123"
        assert root["@odata.type"].startswith("#ServiceRoot.v1_")


# --------------------------------------------------------------------------- #
# SessionService / Sessions
# --------------------------------------------------------------------------- #
class TestSessions:
    def test_session_service(self):
        svc = redfish_core.build_session_service()
        assert svc["Sessions"]["@odata.id"] == "/redfish/v1/SessionService/Sessions"
        assert svc["ServiceEnabled"] is True

    def test_collection_lists_tokens(self):
        sessions = {"tok1": {"username": "a"}, "tok2": {"username": "b"}}
        coll = redfish_core.build_sessions_collection(sessions)
        assert coll["Members@odata.count"] == 2
        ids = {m["@odata.id"] for m in coll["Members"]}
        assert "/redfish/v1/SessionService/Sessions/tok1" in ids

    def test_get_session_found(self):
        sessions = {"tok1": {"username": "admin@pam"}}
        body, status = redfish_core.build_session("tok1", sessions)
        assert status == 200
        assert body["UserName"] == "admin@pam"
        assert body["Id"] == "tok1"

    def test_get_session_missing(self):
        body, status = redfish_core.build_session("nope", {})
        assert status == 404

    def test_delete_session_removes_token(self):
        sessions = {"tok1": {"username": "a"}}
        body, status = redfish_core.delete_session("tok1", sessions)
        assert status == 204
        assert "tok1" not in sessions

    def test_delete_session_missing(self):
        body, status = redfish_core.delete_session("nope", {})
        assert status == 404


# --------------------------------------------------------------------------- #
# TaskService / Tasks
# --------------------------------------------------------------------------- #
def _proxmox_with_task(status=None, tasks=None):
    proxmox = MagicMock()
    node = proxmox.nodes.return_value
    node.tasks.return_value.status.get.return_value = status
    node.tasks.get.return_value = tasks if tasks is not None else []
    return proxmox


class TestTasks:
    def test_task_service(self):
        svc = redfish_core.build_task_service()
        assert svc["Tasks"]["@odata.id"] == "/redfish/v1/TaskService/Tasks"

    @pytest.mark.parametrize(
        "pstatus,exitstatus,state,percent",
        [
            ("running", None, "Running", 50),
            ("stopped", "OK", "Completed", 100),
            ("stopped", "command failed", "Exception", 100),
        ],
    )
    def test_task_state_mapping(self, pstatus, exitstatus, state, percent):
        proxmox = _proxmox_with_task(status={"status": pstatus, "exitstatus": exitstatus, "type": "qmstart"})
        body, code = redfish_core.build_task(proxmox, "UPID:pve:001")
        assert code == 200
        assert body["TaskState"] == state
        assert body["PercentComplete"] == percent
        if state == "Exception":
            assert body["TaskStatus"] == "Critical"
            assert "Messages" in body

    def test_task_not_found(self):
        proxmox = _proxmox_with_task(status=None)
        body, code = redfish_core.build_task(proxmox, "UPID:pve:bad")
        assert code == 404

    def test_task_api_error(self):
        proxmox = MagicMock()
        proxmox.nodes.return_value.tasks.return_value.status.get.side_effect = Exception("boom")
        body, code = redfish_core.build_task(proxmox, "UPID:x")
        assert code == 404

    def test_task_collection(self):
        proxmox = _proxmox_with_task(tasks=[{"upid": "UPID:a"}, {"upid": "UPID:b"}, {"nope": 1}])
        body, code = redfish_core.build_task_collection(proxmox)
        assert code == 200
        assert body["Members@odata.count"] == 2  # entry without upid skipped


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #
def _proxmox_with_config(config):
    proxmox = MagicMock()
    proxmox.nodes.return_value.qemu.return_value.config.get.return_value = config
    return proxmox


class TestMemory:
    def test_collection(self):
        proxmox = _proxmox_with_config({"memory": 4096})
        body, code = redfish_core.build_memory_collection(proxmox, 100)
        assert code == 200
        assert body["Members"][0]["@odata.id"].endswith("/Memory/DRAM")

    def test_member_reports_capacity(self):
        proxmox = _proxmox_with_config({"memory": 8192})
        body, code = redfish_core.build_memory(proxmox, 100, "DRAM")
        assert code == 200
        assert body["CapacityMiB"] == 8192
        assert body["MemoryType"] == "DRAM"

    def test_member_invalid_id(self):
        proxmox = _proxmox_with_config({"memory": 1024})
        body, code = redfish_core.build_memory(proxmox, 100, "Bogus")
        assert code == 404

    def test_member_handles_bad_memory_value(self):
        proxmox = _proxmox_with_config({"memory": "not-a-number"})
        body, code = redfish_core.build_memory(proxmox, 100, "DRAM")
        assert code == 200
        assert body["CapacityMiB"] == 0


# --------------------------------------------------------------------------- #
# ResetType set + power_cycle
# --------------------------------------------------------------------------- #
class TestResetTypes:
    def test_supported_set(self):
        assert redfish_core.RESET_TYPES_SUPPORTED == [
            "On",
            "ForceOff",
            "GracefulShutdown",
            "GracefulRestart",
            "ForceRestart",
            "Nmi",
            "PowerCycle",
        ]


class TestPowerCycle:
    def test_running_vm_stops_then_starts(self):
        proxmox = MagicMock()
        qemu = proxmox.nodes.return_value.qemu.return_value
        qemu.status.current.get.return_value = {"status": "running"}
        qemu.status.start.post.return_value = "UPID:pve:start"
        body, code = power_cycle(proxmox, 100)
        assert code == 202
        qemu.status.stop.post.assert_called_once()
        qemu.status.start.post.assert_called_once()
        assert body["Id"] == "UPID:pve:start"

    def test_stopped_vm_only_starts(self):
        proxmox = MagicMock()
        qemu = proxmox.nodes.return_value.qemu.return_value
        qemu.status.current.get.return_value = {"status": "stopped"}
        qemu.status.start.post.return_value = "UPID:pve:start"
        body, code = power_cycle(proxmox, 100)
        assert code == 202
        qemu.status.stop.post.assert_not_called()
        qemu.status.start.post.assert_called_once()
