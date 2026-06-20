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
        for key in ("Systems", "Managers", "SessionService", "Tasks"):
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

    # SecureBoot certificate POST/DELETE wiring -----------------------------
    def test_cert_post_routes_to_add_cert(self):
        body = json.dumps({"CertificateString": "x", "CertificateType": "PEM"}).encode()
        path = "/redfish/v1/Systems/100/SecureBoot/SecureBootDatabases/PK/Certificates"
        h = make_handler(method="POST", path=path, body=body)
        with patch("proxmox_redfish.secureboot.add_cert", return_value=({"Id": "abc"}, 201)) as m:
            h.do_POST()
        m.assert_called_once()
        self.assertEqual(m.call_args[0][1], "PK")

    def test_cert_delete_routes_to_delete_cert(self):
        path = "/redfish/v1/Systems/100/SecureBoot/SecureBootDatabases/db/Certificates/0123456789abcdef"
        h = make_handler(method="DELETE", path=path)
        with patch("proxmox_redfish.secureboot.delete_cert", return_value=({}, 204)) as m:
            h.do_DELETE()
        m.assert_called_once_with(100, "db", "0123456789abcdef")
        self.assertIn("204", status_line(h))

    def test_private_key_rejected_end_to_end(self):
        # Real wiring (no leaf patch): a private key POST must be refused with 400.
        body = json.dumps(
            {"CertificateString": "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----"}
        ).encode()
        path = "/redfish/v1/Systems/100/SecureBoot/SecureBootDatabases/PK/Certificates"
        h = make_handler(method="POST", path=path, body=body)
        h.do_POST()
        self.assertIn("400", status_line(h))
        self.assertEqual(body_json(h)["error"]["code"], "Base.1.0.ActionParameterValueError")

    # New top-level services (Phases 6-9) -----------------------------------
    def test_service_root_advertises_all_services(self):
        h = make_handler(path="/redfish/v1")
        h.do_GET()
        data = body_json(h)
        for key in ("Chassis", "AccountService", "EventService", "UpdateService", "Registries", "JsonSchemas"):
            self.assertIn(key, data)

    def test_chassis_member(self):
        self.proxmox.nodes.return_value.qemu.get.return_value = [{"vmid": 100}]
        h = make_handler(path="/redfish/v1/Chassis/100")
        h.do_GET()
        self.assertEqual(body_json(h)["ChassisType"], "Other")

    def test_account_service(self):
        h = make_handler(path="/redfish/v1/AccountService")
        h.do_GET()
        self.assertEqual(body_json(h)["Id"], "AccountService")

    def test_update_service_absent(self):
        h = make_handler(path="/redfish/v1/UpdateService")
        h.do_GET()
        self.assertEqual(body_json(h)["Status"]["State"], "Absent")

    def test_event_subscription_create_and_delete(self):
        from proxmox_redfish import redfish_services as rs

        rs.subscriptions.clear()
        body = json.dumps({"Destination": "https://listener.example/ev"}).encode()
        h = make_handler(method="POST", path="/redfish/v1/EventService/Subscriptions", body=body)
        h.do_POST()
        self.assertIn("201", status_line(h))
        sid = body_json(h)["Id"]

        h2 = make_handler(method="DELETE", path=f"/redfish/v1/EventService/Subscriptions/{sid}")
        h2.do_DELETE()
        self.assertIn("204", status_line(h2))
        self.assertNotIn(sid, rs.subscriptions)

    # HTTP method conformance ----------------------------------------------
    def test_options_advertises_allow(self):
        h = make_handler(method="OPTIONS", path="/redfish/v1/Systems/100")
        h.do_OPTIONS()
        self.assertIn("204", status_line(h))
        self.assertTrue(header_present(h, "Allow"))

    def test_put_is_405(self):
        h = make_handler(method="PUT", path="/redfish/v1/Systems/100")
        h.do_PUT()
        self.assertIn("405", status_line(h))
        self.assertTrue(header_present(h, "Allow"))
        self.assertEqual(body_json(h)["error"]["code"], "Base.1.0.ActionNotSupported")

    def test_head_has_headers_no_body(self):
        h = make_handler(method="HEAD", path="/redfish/v1")
        h.do_HEAD()
        self.assertIn("200", status_line(h))
        self.assertTrue(header_present(h, "OData-Version"))
        # no JSON body on HEAD
        self.assertEqual(raw(h).split("\r\n\r\n", 1)[1], "")

    # If-Match optimistic concurrency (tested on the SecureBoot PATCH target) -
    def _patch_secureboot(self, if_match):
        from proxmox_redfish.proxmox_redfish import compute_etag

        current_body = {"@odata.id": "/redfish/v1/Systems/100/SecureBoot", "Id": "SecureBoot"}
        body = json.dumps({"SecureBootEnable": True}).encode()
        h = make_handler(method="PATCH", path="/redfish/v1/Systems/100/SecureBoot", body=body)
        h.headers["If-Match"] = if_match
        with patch("proxmox_redfish.secureboot.get_secureboot", return_value=(current_body, 200)), patch(
            "proxmox_redfish.secureboot.route_patch", return_value=({"ok": 1}, 200)
        ) as rp:
            h.do_PATCH()
        return h, rp, compute_etag(current_body)

    def test_if_match_mismatch_412(self):
        h, rp, _ = self._patch_secureboot('W/"deadbeefdeadbeef"')
        self.assertIn("412", status_line(h))
        rp.assert_not_called()  # stale update never dispatched

    def test_if_match_correct_proceeds(self):
        # First get the real current ETag, then send it back.
        _, _, etag = self._patch_secureboot("*")
        h, rp, _ = self._patch_secureboot(etag)
        self.assertNotIn("412", status_line(h))
        rp.assert_called_once()

    def test_if_match_star_proceeds(self):
        h, rp, _ = self._patch_secureboot("*")
        self.assertNotIn("412", status_line(h))
        rp.assert_called_once()

    # Lenient vs strict protocol mode --------------------------------------
    def test_lenient_accepts_bad_odata_version(self):
        with patch("proxmox_redfish.proxmox_redfish.STRICT_PROTOCOL", False):
            h = make_handler(path="/redfish/v1")
            h.headers["OData-Version"] = "4.1"
            h.do_GET()
            self.assertIn("200", status_line(h))

    def test_strict_rejects_bad_odata_version(self):
        with patch("proxmox_redfish.proxmox_redfish.STRICT_PROTOCOL", True):
            h = make_handler(path="/redfish/v1")
            h.headers["OData-Version"] = "4.1"
            h.do_GET()
            self.assertIn("412", status_line(h))

    def test_lenient_ignores_unknown_dollar_param(self):
        with patch("proxmox_redfish.proxmox_redfish.STRICT_PROTOCOL", False):
            h = make_handler(path="/redfish/v1?$bogus=1")
            h.do_GET()
            self.assertIn("200", status_line(h))

    def test_strict_501_on_unknown_dollar_param(self):
        with patch("proxmox_redfish.proxmox_redfish.STRICT_PROTOCOL", True):
            h = make_handler(path="/redfish/v1?$bogus=1")
            h.do_GET()
            self.assertIn("501", status_line(h))

    # Unsupported method on existing resource -> 405 ------------------------
    def test_patch_unsupported_on_existing_is_405(self):
        h = make_handler(method="PATCH", path="/redfish/v1/Chassis/100", body=b"{}")
        h.do_PATCH()
        self.assertIn("405", status_line(h))
        self.assertTrue(header_present(h, "Allow"))

    def test_patch_nonexistent_is_404(self):
        h = make_handler(method="PATCH", path="/redfish/v1/Nope/123", body=b"{}")
        h.do_PATCH()
        self.assertIn("404", status_line(h))


if __name__ == "__main__":
    unittest.main()
