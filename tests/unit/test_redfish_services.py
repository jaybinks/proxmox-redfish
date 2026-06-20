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
        assert body["ChassisType"] == "VirtualMachine"
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
