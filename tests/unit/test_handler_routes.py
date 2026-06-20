#!/usr/bin/env python3
"""
Handler-level wiring tests for Phase 2/3 routes: ServiceRoot completeness,
SessionService, TaskService, Memory, session DELETE, ResetType Nmi/PowerCycle,
and the 202 Location header. Exercises the real dispatchers via BytesIO.
"""

import json
import os
import sys
import unittest
from io import BytesIO
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))

from proxmox_redfish import proxmox_redfish as mod  # noqa: E402
from proxmox_redfish.proxmox_redfish import RedfishRequestHandler  # noqa: E402


def make_handler(method="GET", path="/redfish/v1", body=b""):
    request = Mock()
    request.makefile.return_value = BytesIO(b"")
    handler = RedfishRequestHandler(request, ("127.0.0.1", 8000), None)
    handler.wfile = BytesIO()
    handler.command = method
    handler.path = path
    handler.requestline = f"{method} {path} HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    handler.headers = {"X-Auth-Token": "tok", "Content-Length": str(len(body))}
    handler.rfile = BytesIO(body)
    return handler


def raw(handler):
    return handler.wfile.getvalue().decode()


def body_json(handler):
    text = raw(handler)
    return json.loads(text[text.find("\r\n\r\n") + 4 :])


def status_line(handler):
    return raw(handler).splitlines()[0]


def header_present(handler, name):
    return any(line.lower().startswith(name.lower() + ":") for line in raw(handler).splitlines())


class HandlerRouteTests(unittest.TestCase):
    def setUp(self):
        self.vp = patch("proxmox_redfish.proxmox_redfish.validate_token", return_value=(True, "admin@pam"))
        self.ap = patch("proxmox_redfish.proxmox_redfish.get_proxmox_api")
        self.mock_validate = self.vp.start()
        self.mock_api = self.ap.start()
        self.proxmox = Mock()
        self.mock_api.return_value = self.proxmox
        self.addCleanup(self.vp.stop)
        self.addCleanup(self.ap.stop)

    # ServiceRoot -----------------------------------------------------------
    def test_service_root_complete(self):
        h = make_handler(path="/redfish/v1")
        h.do_GET()
        data = body_json(h)
        self.assertEqual(data["RedfishVersion"], "1.18.0")
        for key in ("Systems", "Managers", "SessionService", "TaskService"):
            self.assertIn(key, data)

    # SessionService --------------------------------------------------------
    def test_session_service(self):
        h = make_handler(path="/redfish/v1/SessionService")
        h.do_GET()
        self.assertEqual(body_json(h)["Id"], "SessionService")

    def test_sessions_collection(self):
        mod.sessions.clear()
        mod.sessions["abc"] = {"username": "admin@pam"}
        h = make_handler(path="/redfish/v1/SessionService/Sessions")
        h.do_GET()
        self.assertEqual(body_json(h)["Members@odata.count"], 1)

    def test_session_delete(self):
        mod.sessions.clear()
        mod.sessions["abc"] = {"username": "admin@pam"}
        h = make_handler(method="DELETE", path="/redfish/v1/SessionService/Sessions/abc")
        h.do_DELETE()
        self.assertIn("204", status_line(h))
        self.assertNotIn("abc", mod.sessions)

    def test_session_delete_missing(self):
        mod.sessions.clear()
        h = make_handler(method="DELETE", path="/redfish/v1/SessionService/Sessions/nope")
        h.do_DELETE()
        self.assertIn("404", status_line(h))

    # TaskService -----------------------------------------------------------
    def test_task_service(self):
        h = make_handler(path="/redfish/v1/TaskService")
        h.do_GET()
        self.assertEqual(body_json(h)["Id"], "TaskService")

    def test_task_resolves_proxmox_upid(self):
        self.proxmox.nodes.return_value.tasks.return_value.status.get.return_value = {
            "status": "stopped",
            "exitstatus": "OK",
            "type": "qmstart",
        }
        h = make_handler(path="/redfish/v1/TaskService/Tasks/UPID:pve:0001")
        h.do_GET()
        data = body_json(h)
        self.assertEqual(data["TaskState"], "Completed")

    # Memory ----------------------------------------------------------------
    def test_memory_collection(self):
        self.proxmox.nodes.return_value.qemu.return_value.config.get.return_value = {"memory": 2048}
        h = make_handler(path="/redfish/v1/Systems/100/Memory")
        h.do_GET()
        self.assertEqual(body_json(h)["Members"][0]["@odata.id"], "/redfish/v1/Systems/100/Memory/DRAM")

    def test_memory_member(self):
        self.proxmox.nodes.return_value.qemu.return_value.config.get.return_value = {"memory": 2048}
        h = make_handler(path="/redfish/v1/Systems/100/Memory/DRAM")
        h.do_GET()
        self.assertEqual(body_json(h)["CapacityMiB"], 2048)

    # ResetType Nmi / PowerCycle + Location header --------------------------
    def test_reset_nmi_calls_reset_vm(self):
        body = json.dumps({"ResetType": "Nmi"}).encode()
        h = make_handler(method="POST", path="/redfish/v1/Systems/100/Actions/ComputerSystem.Reset", body=body)
        with patch("proxmox_redfish.proxmox_redfish.reset_vm", return_value=({"@odata.id": "/x"}, 202)) as m:
            h.do_POST()
        m.assert_called_once_with(self.proxmox, 100)

    def test_reset_powercycle_calls_power_cycle(self):
        body = json.dumps({"ResetType": "PowerCycle"}).encode()
        h = make_handler(method="POST", path="/redfish/v1/Systems/100/Actions/ComputerSystem.Reset", body=body)
        with patch("proxmox_redfish.proxmox_redfish.power_cycle", return_value=({"@odata.id": "/x"}, 202)) as m:
            h.do_POST()
        m.assert_called_once_with(self.proxmox, 100)

    def test_202_sets_location_header(self):
        body = json.dumps({"ResetType": "On"}).encode()
        h = make_handler(method="POST", path="/redfish/v1/Systems/100/Actions/ComputerSystem.Reset", body=body)
        task = {"@odata.id": "/redfish/v1/TaskService/Tasks/UPID:pve:1", "TaskState": "Running"}
        with patch("proxmox_redfish.proxmox_redfish.power_on", return_value=(task, 202)):
            h.do_POST()
        self.assertIn("202", status_line(h))
        self.assertTrue(header_present(h, "Location"))


if __name__ == "__main__":
    unittest.main()
