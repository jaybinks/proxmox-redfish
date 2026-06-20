#!/usr/bin/env python3
"""
Unit tests for proxmox_redfish.redfish_services -- Chassis, AccountService,
EventService, UpdateService, discovery stubs (Phases 6-9). Proxmox API mocked.
"""

from unittest.mock import MagicMock

from proxmox_redfish import redfish_services as rs


def _proxmox(vms=None, users=None):
    proxmox = MagicMock()
    proxmox.nodes.return_value.qemu.get.return_value = vms if vms is not None else []
    proxmox.access.users.get.return_value = users if users is not None else []
    return proxmox


# --------------------------------------------------------------------------- #
# Chassis
# --------------------------------------------------------------------------- #
class TestChassis:
    def test_collection_lists_vms(self):
        body, code = rs.build_chassis_collection(_proxmox(vms=[{"vmid": 100}, {"vmid": 101}]))
        assert code == 200 and body["Members@odata.count"] == 2

    def test_member_is_virtualmachine(self):
        body, code = rs.build_chassis(_proxmox(), "100")
        assert code == 200
        assert body["ChassisType"] == "Other"
        assert body["Power"]["@odata.id"].endswith("/Power")
        assert body["Links"]["ComputerSystems"][0]["@odata.id"] == "/redfish/v1/Systems/100"

    def test_member_bad_id(self):
        body, code = rs.build_chassis(_proxmox(), "notanumber")
        assert code == 404

    def test_power_and_thermal_synthetic(self):
        p, _ = rs.build_chassis_power("100")
        t, _ = rs.build_chassis_thermal("100")
        assert p["PowerControl"] == [] and t["Temperatures"] == [] and t["Fans"] == []
        assert t["Oem"]["Proxmox"]["Synthetic"] is True


# --------------------------------------------------------------------------- #
# AccountService
# --------------------------------------------------------------------------- #
class TestAccountService:
    def test_service_links(self):
        svc = rs.build_account_service()
        assert svc["Accounts"]["@odata.id"] == "/redfish/v1/AccountService/Accounts"
        assert svc["Roles"]["@odata.id"] == "/redfish/v1/AccountService/Roles"

    def test_account_id_sanitized(self):
        assert rs._account_id("root@pam") == "root_pam"
        assert rs._account_id("a/b c") == "a_b_c"

    def test_accounts_collection(self):
        proxmox = _proxmox(users=[{"userid": "root@pam"}, {"userid": "redfish@pve"}])
        body, code = rs.build_accounts_collection(proxmox)
        assert code == 200 and body["Members@odata.count"] == 2
        assert any(m["@odata.id"].endswith("root_pam") for m in body["Members"])

    def test_account_found(self):
        proxmox = _proxmox(users=[{"userid": "root@pam", "enable": 1}])
        body, code = rs.build_account(proxmox, "root_pam")
        assert code == 200
        assert body["UserName"] == "root@pam"
        assert body["RoleId"] == "Administrator"
        assert body["Enabled"] is True

    def test_account_not_found(self):
        body, code = rs.build_account(_proxmox(users=[]), "ghost")
        assert code == 404

    def test_roles_collection_and_member(self):
        coll = rs.build_roles_collection()
        assert coll["Members@odata.count"] == 3
        admin, code = rs.build_role("Administrator")
        assert code == 200 and "ConfigureUsers" in admin["AssignedPrivileges"]
        _, code = rs.build_role("Nope")
        assert code == 404


# --------------------------------------------------------------------------- #
# EventService
# --------------------------------------------------------------------------- #
class TestEventService:
    def setup_method(self):
        rs.subscriptions.clear()

    def test_service_and_empty_collection(self):
        assert rs.build_event_service()["ServiceEnabled"] is True
        assert rs.build_subscriptions_collection()["Members@odata.count"] == 0

    def test_create_get_delete(self):
        body, code = rs.create_subscription({"Destination": "https://listener.example/ev", "Context": "x"})
        assert code == 201
        sid = body["Id"]
        assert body["Destination"] == "https://listener.example/ev"

        got, code = rs.build_subscription(sid)
        assert code == 200 and got["Context"] == "x"

        _, code = rs.delete_subscription(sid)
        assert code == 204
        assert rs.build_subscription(sid)[1] == 404

    def test_create_requires_destination(self):
        _, code = rs.create_subscription({})
        assert code == 400

    def test_create_rejects_non_http_destination(self):
        _, code = rs.create_subscription({"Destination": "file:///etc/passwd"})
        assert code == 400

    def test_create_is_deduplicated(self):
        rs.create_subscription({"Destination": "https://a/ev"})
        rs.create_subscription({"Destination": "https://a/ev"})
        assert rs.build_subscriptions_collection()["Members@odata.count"] == 1

    def test_delete_missing(self):
        _, code = rs.delete_subscription("nope")
        assert code == 404


# --------------------------------------------------------------------------- #
# UpdateService + discovery
# --------------------------------------------------------------------------- #
class TestMisc:
    def test_update_service_absent(self):
        svc = rs.build_update_service()
        assert svc["ServiceEnabled"] is False
        assert svc["Status"]["State"] == "Absent"

    def test_registries_and_jsonschemas(self):
        assert rs.build_registries()["Members@odata.count"] == 0
        assert rs.build_json_schemas()["Members@odata.count"] == 0

    def test_certificate_service(self, monkeypatch):
        monkeypatch.setenv("SSL_CERT_FILE", "/x/server.crt")
        svc = rs.build_certificate_service()
        assert svc["Id"] == "CertificateService"
        loc = rs.build_certificate_locations()
        assert loc["Links"]["Certificates@odata.count"] == 1


class TestAccountMutation:
    def test_create_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("REDFISH_ALLOW_ACCOUNT_MUTATION", raising=False)
        _, code = rs.create_account(MagicMock(), {"UserName": "x", "Password": "y"})
        assert code == 405

    def test_create_enabled(self, monkeypatch):
        monkeypatch.setenv("REDFISH_ALLOW_ACCOUNT_MUTATION", "1")
        proxmox = _proxmox(users=[{"userid": "new@pam", "enable": 1}])
        body, code = rs.create_account(proxmox, {"UserName": "new", "Password": "secret"})
        assert code == 201
        proxmox.access.users.post.assert_called_once()
        assert proxmox.access.users.post.call_args.kwargs["userid"] == "new@pam"

    def test_create_missing_fields(self, monkeypatch):
        monkeypatch.setenv("REDFISH_ALLOW_ACCOUNT_MUTATION", "1")
        _, code = rs.create_account(MagicMock(), {"UserName": "x"})
        assert code == 400

    def test_delete_enabled(self, monkeypatch):
        monkeypatch.setenv("REDFISH_ALLOW_ACCOUNT_MUTATION", "1")
        proxmox = _proxmox(users=[{"userid": "gone@pam"}])
        _, code = rs.delete_account(proxmox, "gone_pam")
        assert code == 204

    def test_delete_disabled(self, monkeypatch):
        monkeypatch.delenv("REDFISH_ALLOW_ACCOUNT_MUTATION", raising=False)
        _, code = rs.delete_account(MagicMock(), "gone_pam")
        assert code == 405


class TestEventDelivery:
    def setup_method(self):
        rs.subscriptions.clear()

    def test_emit_no_subscribers(self):
        assert rs.emit_event("Base.1.0.TestMessage", "hi") == 0

    def test_emit_delivers_to_subscribers(self, monkeypatch):
        rs.subscriptions["s1"] = {"Destination": "https://a/ev", "Context": "ctx"}
        rs.subscriptions["s2"] = {"Destination": "https://b/ev"}
        calls = []

        class FakeResp:
            status_code = 204

        def fake_post(url, json=None, timeout=None, verify=None):
            calls.append((url, json))
            return FakeResp()

        import requests

        monkeypatch.setattr(requests, "post", fake_post)
        delivered = rs.emit_event("Base.1.0.TestMessage", "hello", "Warning")
        assert delivered == 2
        # context echoed to s1
        assert any(j.get("Context") == "ctx" for _, j in calls)

    def test_submit_test_event(self, monkeypatch):
        rs.subscriptions["s1"] = {"Destination": "https://a/ev"}

        class FakeResp:
            status_code = 200

        import requests

        monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp())
        body, code = rs.submit_test_event({"Message": "t"})
        assert code == 200 and "1 subscriber" in body["Message"]

    def test_emit_tolerates_delivery_failure(self, monkeypatch):
        rs.subscriptions["s1"] = {"Destination": "https://a/ev"}

        import requests

        def boom(*a, **k):
            raise requests.RequestException("down")

        monkeypatch.setattr(requests, "post", boom)
        assert rs.emit_event("X", "y") == 0  # no raise
