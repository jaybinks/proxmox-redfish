#!/usr/bin/env python3
"""
Unit tests for proxmox_redfish.secureboot -- the Redfish SecureBoot surface.

The host layer is the only seam mocked: hostops.locate_efidisk,
hostops.stopped_vm_guard, hostops.write_varstore_image. Real hostops exception
classes and validate_vmid are used. Response shapes are checked against the
spec in docs/spec/redfish-secureboot-api.md.
"""

import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from proxmox_redfish import hostops, secureboot


@pytest.fixture
def profiles_file(tmp_path, monkeypatch):
    data = {
        "profiles": {
            "ngv-sb-on": {
                "description": "custom keys, SB on",
                "image_path": str(tmp_path / "ngv.img"),
                "efitype": "4m",
                "secure_boot": True,
                "databases": {"PK": True, "KEK": True, "db": True, "dbx": False},
                "size_bytes": 540672,
                "image_sha256": "abc",
            },
            "sb-off-blank": {
                "description": "blank, SB off",
                "image_path": str(tmp_path / "blank.img"),
                "efitype": "4m",
                "secure_boot": False,
                "databases": {"PK": False, "KEK": False, "db": False, "dbx": False},
                "size_bytes": 540672,
                "image_sha256": "def",
            },
        },
        "default_profile": "ngv-sb-on",
        "map": {
            "SecureBootEnable:true": "ngv-sb-on",
            "SecureBootEnable:false": "sb-off-blank",
            "ResetAllKeysToDefault": "ngv-sb-on",
            "DeleteAllKeys": "sb-off-blank",
            "DeletePK": "sb-off-blank",
        },
    }
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("REDFISH_SB_PROFILES", str(path))
    monkeypatch.setenv("REDFISH_SB_STATE_DIR", str(tmp_path / "state"))
    return path


def _efidisk():
    return hostops.EfiDisk(
        volid="local-lvm:vm-100-disk-0",
        storage="local-lvm",
        device_path="/dev/pve/vm-100-disk-0",
        efitype="4m",
        pre_enrolled=True,
        size_bytes=540672,
    )


def _write_result(dry_run=True, wrote=False):
    return hostops.WriteResult(
        wrote=wrote,
        verified=not dry_run,
        image_path="/x/ngv.img",
        image_sha256="abc",
        device_path="/dev/pve/vm-100-disk-0",
        bytes_considered=540672,
        dry_run=dry_run,
        message="dry-run" if dry_run else "written",
    )


@contextmanager
def _fake_guard(proxmox, vmid, allow_autostop=False):
    yield False


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #
class TestRouting:
    def test_is_secureboot_path(self):
        assert secureboot.is_secureboot_path(["", "redfish", "v1", "Systems", "100", "SecureBoot"])
        assert not secureboot.is_secureboot_path(["", "redfish", "v1", "Systems", "100", "Bios"])

    def test_route_get_non_sb_returns_not_handled(self):
        parts = ["", "redfish", "v1", "Systems", "100", "Bios"]
        assert secureboot.route_get(None, parts) is secureboot.NOT_HANDLED

    def test_route_get_unknown_subpath_not_handled(self):
        parts = ["", "redfish", "v1", "Systems", "100", "SecureBoot", "Bogus", "x", "y"]
        assert secureboot.route_get(None, parts) is secureboot.NOT_HANDLED


# --------------------------------------------------------------------------- #
# GET /SecureBoot
# --------------------------------------------------------------------------- #
class TestGetSecureBoot:
    def test_returns_spec_shape(self, profiles_file):
        with patch.object(secureboot.hostops, "locate_efidisk", return_value=_efidisk()):
            body, status = secureboot.get_secureboot(object(), 100)
        assert status == 200
        assert body["@odata.type"] == "#SecureBoot.v1_1_1.SecureBoot"
        assert body["@odata.id"] == "/redfish/v1/Systems/100/SecureBoot"
        assert body["SecureBootEnable"] is False  # no sidecar yet
        assert body["SecureBootMode"] == "SetupMode"
        assert body["SecureBootDatabases"]["@odata.id"].endswith("/SecureBootDatabases")
        allowed = body["Actions"]["#SecureBoot.ResetKeys"]["ResetKeysType@Redfish.AllowableValues"]
        assert allowed == ["ResetAllKeysToDefault", "DeleteAllKeys", "DeletePK"]

    def test_reflects_sidecar_state(self, profiles_file):
        secureboot.write_state(100, {"enabled": True, "mode": "UserMode", "profile": "ngv-sb-on"})
        with patch.object(secureboot.hostops, "locate_efidisk", return_value=_efidisk()):
            body, status = secureboot.get_secureboot(object(), 100)
        assert body["SecureBootEnable"] is True
        assert body["SecureBootMode"] == "UserMode"
        assert body["SecureBootCurrentBoot"] == "Enabled"
        assert body["Oem"]["Proxmox"]["ActiveProfile"] == "ngv-sb-on"

    def test_no_efidisk_returns_error(self, profiles_file):
        with patch.object(secureboot.hostops, "locate_efidisk", side_effect=hostops.NoEfiDiskError("no efi")):
            body, status = secureboot.get_secureboot(object(), 100)
        assert status == 400
        assert body["error"]["code"] == "Base.1.0.ActionNotSupported"


# --------------------------------------------------------------------------- #
# PATCH /SecureBoot
# --------------------------------------------------------------------------- #
class TestPatchSecureBoot:
    def test_enable_applies_profile_and_updates_state(self, profiles_file):
        with patch.object(secureboot.hostops, "stopped_vm_guard", _fake_guard), patch.object(
            secureboot.hostops, "locate_efidisk", return_value=_efidisk()
        ), patch.object(secureboot.hostops, "write_varstore_image", return_value=_write_result(dry_run=True)):
            body, status = secureboot.patch_secureboot(object(), 100, {"SecureBootEnable": True})
        assert status == 200
        assert body["SecureBootEnable"] is True  # ngv-sb-on has secure_boot=true
        assert body["SecureBootMode"] == "UserMode"  # PK present
        state = secureboot.read_state(100)
        assert state["profile"] == "ngv-sb-on"
        assert state["has_pk"] is True

    def test_disable_applies_blank_profile(self, profiles_file):
        with patch.object(secureboot.hostops, "stopped_vm_guard", _fake_guard), patch.object(
            secureboot.hostops, "locate_efidisk", return_value=_efidisk()
        ), patch.object(secureboot.hostops, "write_varstore_image", return_value=_write_result(dry_run=True)):
            body, status = secureboot.patch_secureboot(object(), 100, {"SecureBootEnable": False})
        assert status == 200
        assert body["SecureBootEnable"] is False
        assert secureboot.read_state(100)["profile"] == "sb-off-blank"

    def test_missing_property_rejected(self, profiles_file):
        body, status = secureboot.patch_secureboot(object(), 100, {})
        assert status == 400
        assert body["error"]["code"] == "Base.1.0.PropertyValueNotInList"

    def test_non_boolean_rejected(self, profiles_file):
        body, status = secureboot.patch_secureboot(object(), 100, {"SecureBootEnable": "yes"})
        assert status == 400

    def test_running_vm_surfaces_redfish_error(self, profiles_file):
        def raising_guard(proxmox, vmid, allow_autostop=False):
            raise hostops.VmRunningError("vm running")

        with patch.object(secureboot.hostops, "stopped_vm_guard", raising_guard):
            body, status = secureboot.patch_secureboot(object(), 100, {"SecureBootEnable": True})
        assert status == 409
        assert body["error"]["code"] == "Base.1.0.ResourceInStandby"


# --------------------------------------------------------------------------- #
# POST ResetKeys
# --------------------------------------------------------------------------- #
class TestResetKeys:
    @pytest.mark.parametrize(
        "reset_type,expected_profile",
        [
            ("ResetAllKeysToDefault", "ngv-sb-on"),
            ("DeleteAllKeys", "sb-off-blank"),
            ("DeletePK", "sb-off-blank"),
        ],
    )
    def test_maps_type_to_profile(self, profiles_file, reset_type, expected_profile):
        with patch.object(secureboot.hostops, "stopped_vm_guard", _fake_guard), patch.object(
            secureboot.hostops, "locate_efidisk", return_value=_efidisk()
        ), patch.object(secureboot.hostops, "write_varstore_image", return_value=_write_result(dry_run=True)):
            body, status = secureboot.action_reset_keys(object(), 100, {"ResetKeysType": reset_type})
        assert status == 200
        assert body["Oem"]["Proxmox"]["Profile"] == expected_profile

    def test_invalid_type_rejected(self, profiles_file):
        body, status = secureboot.action_reset_keys(object(), 100, {"ResetKeysType": "Nope"})
        assert status == 400
        assert body["error"]["code"] == "Base.1.0.PropertyValueNotInList"


# --------------------------------------------------------------------------- #
# Databases
# --------------------------------------------------------------------------- #
class TestDatabases:
    def test_collection_has_four_members(self):
        body, status = secureboot.get_db_collection(100)
        assert status == 200
        assert body["Members@odata.count"] == 4
        ids = [m["@odata.id"].split("/")[-1] for m in body["Members"]]
        assert ids == ["PK", "KEK", "db", "dbx"]

    def test_get_db_valid(self):
        body, status = secureboot.get_db(100, "db")
        assert status == 200
        assert body["DatabaseId"] == "db"
        assert body["@odata.type"] == "#SecureBootDatabase.v1_0_2.SecureBootDatabase"

    def test_get_db_invalid_404(self):
        body, status = secureboot.get_db(100, "bogus")
        assert status == 404


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #
class TestErrorMapping:
    @pytest.mark.parametrize(
        "exc,status,code",
        [
            (hostops.NoEfiDiskError("x"), 400, "Base.1.0.ActionNotSupported"),
            (hostops.UnsupportedEfiTypeError("x"), 409, "Base.1.0.ActionNotSupported"),
            (hostops.VmRunningError("x"), 409, "Base.1.0.ResourceInStandby"),
            (hostops.ImageSizeMismatchError("x"), 409, "Base.1.0.PropertyValueConflict"),
            (hostops.ToolMissingError("x"), 501, "Base.1.0.ActionNotSupported"),
        ],
    )
    def test_sb_error(self, exc, status, code):
        body, st = secureboot.sb_error(exc)
        assert st == status
        assert body["error"]["code"] == code
        assert body["error"]["@Message.ExtendedInfo"][0]["Resolution"]


# --------------------------------------------------------------------------- #
# Router dispatch (the seam the monolith calls)
# --------------------------------------------------------------------------- #
class TestRouterDispatch:
    def test_route_get_secureboot(self, profiles_file):
        parts = ["", "redfish", "v1", "Systems", "100", "SecureBoot"]
        with patch.object(secureboot.hostops, "locate_efidisk", return_value=_efidisk()):
            result = secureboot.route_get(object(), parts)
        body, status = result
        assert status == 200 and body["Id"] == "SecureBoot"

    def test_route_get_db_collection(self):
        parts = ["", "redfish", "v1", "Systems", "100", "SecureBoot", "SecureBootDatabases"]
        body, status = secureboot.route_get(object(), parts)
        assert status == 200 and body["Members@odata.count"] == 4

    def test_route_get_db_member(self):
        parts = ["", "redfish", "v1", "Systems", "100", "SecureBoot", "SecureBootDatabases", "PK"]
        body, status = secureboot.route_get(object(), parts)
        assert status == 200 and body["DatabaseId"] == "PK"

    def test_route_get_bad_vmid(self):
        parts = ["", "redfish", "v1", "Systems", "abc", "SecureBoot"]
        body, status = secureboot.route_get(object(), parts)
        assert status == 400

    def test_route_patch_dispatch(self, profiles_file):
        parts = ["", "redfish", "v1", "Systems", "100", "SecureBoot"]
        with patch.object(secureboot.hostops, "stopped_vm_guard", _fake_guard), patch.object(
            secureboot.hostops, "locate_efidisk", return_value=_efidisk()
        ), patch.object(secureboot.hostops, "write_varstore_image", return_value=_write_result()):
            body, status = secureboot.route_patch(object(), parts, {"SecureBootEnable": True})
        assert status == 200

    def test_route_patch_wrong_depth_not_handled(self):
        parts = ["", "redfish", "v1", "Systems", "100", "SecureBoot", "SecureBootDatabases"]
        assert secureboot.route_patch(object(), parts, {}) is secureboot.NOT_HANDLED

    def test_route_post_reset_keys(self, profiles_file):
        parts = ["", "redfish", "v1", "Systems", "100", "SecureBoot", "Actions", "SecureBoot.ResetKeys"]
        with patch.object(secureboot.hostops, "stopped_vm_guard", _fake_guard), patch.object(
            secureboot.hostops, "locate_efidisk", return_value=_efidisk()
        ), patch.object(secureboot.hostops, "write_varstore_image", return_value=_write_result()):
            body, status = secureboot.route_post(object(), parts, {"ResetKeysType": "DeleteAllKeys"})
        assert status == 200

    def test_route_post_unknown_action_not_handled(self):
        parts = ["", "redfish", "v1", "Systems", "100", "SecureBoot", "Actions", "Bogus"]
        assert secureboot.route_post(object(), parts, {}) is secureboot.NOT_HANDLED


# --------------------------------------------------------------------------- #
# Profile / config edge cases
# --------------------------------------------------------------------------- #
class TestProfilesAndState:
    def test_load_profiles_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REDFISH_SB_PROFILES", str(tmp_path / "absent.json"))
        profiles = secureboot.load_profiles()
        assert profiles == {"profiles": {}, "map": {}, "default_profile": None}

    def test_unknown_profile_surfaces_template_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REDFISH_SB_PROFILES", str(tmp_path / "absent.json"))
        monkeypatch.setenv("REDFISH_SB_STATE_DIR", str(tmp_path / "state"))
        # no profiles, no default -> apply resolves to None -> TemplateMissingError -> 500
        body, status = secureboot.patch_secureboot(object(), 100, {"SecureBootEnable": True})
        assert status == 500
        assert body["error"]["code"] == "Base.1.0.GeneralError"

    def test_resolve_profile_name_falls_back_to_default(self):
        profiles = {"map": {}, "default_profile": "ngv-sb-on", "profiles": {}}
        assert secureboot.resolve_profile_name(profiles, "SecureBootEnable:true") == "ngv-sb-on"
