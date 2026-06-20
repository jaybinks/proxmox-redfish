#!/usr/bin/env python3
"""Regression tests for live-reported bridge bugs (#1 VirtualMedia readback,
#4 ServiceRoot UUID, #5 BootOrder blank entries)."""

from unittest.mock import MagicMock, patch

from proxmox_redfish import proxmox_redfish as m
from proxmox_redfish import redfish_core


def _proxmox(config):
    px = MagicMock()
    px.nodes.return_value.qemu.return_value.config.get.return_value = config
    return px


class TestVirtualMediaReadback:
    def test_image_and_name_populated_when_inserted(self):
        px = _proxmox({"ide2": "local:iso/cidata-selftest.iso,media=cdrom"})
        r = m.get_virtual_media(px, 4000)
        assert r["Inserted"] is True
        assert r["Image"] == "local:iso/cidata-selftest.iso"
        assert r["ImageName"] == "cidata-selftest.iso"

    def test_image_null_when_empty(self):
        px = _proxmox({"ide2": "none,media=cdrom"})
        r = m.get_virtual_media(px, 4000)
        assert r["Inserted"] is False
        assert r["Image"] is None and r["ImageName"] is None


class TestServiceRootUuid:
    def test_env_override_wins(self):
        with patch.dict("os.environ", {"REDFISH_SERVICE_UUID": "abcdef01-2345-6789-abcd-ef0123456789"}):
            assert redfish_core.service_root_uuid() == "abcdef01-2345-6789-abcd-ef0123456789"

    def test_derives_from_machine_id(self):
        with patch.dict("os.environ", {}, clear=False) as env:
            env.pop("REDFISH_SERVICE_UUID", None)
            with patch("builtins.open", new=_fake_open("0123456789abcdef0123456789abcdef")):
                assert redfish_core.service_root_uuid() == "01234567-89ab-cdef-0123-456789abcdef"


def _fake_open(content):
    from io import StringIO

    def _open(*a, **k):
        return StringIO(content)

    return _open


class TestBootOrderBlanks:
    def test_blank_and_space_entries_dropped(self):
        cfg = {"boot": "order=scsi0; ;net0", "memory": 1024, "bios": "ovmf"}
        px = _proxmox(cfg)
        px.nodes.return_value.qemu.return_value.status.current.get.return_value = {"status": "stopped"}
        r = m.get_vm_status(px, 200)
        body = r[0] if isinstance(r, tuple) else r
        assert body["Boot"]["BootOrder"] == ["scsi0", "net0"]

    def test_only_space_yields_empty(self):
        cfg = {"boot": "order= ", "memory": 1024, "bios": "ovmf"}
        px = _proxmox(cfg)
        px.nodes.return_value.qemu.return_value.status.current.get.return_value = {"status": "stopped"}
        r = m.get_vm_status(px, 200)
        body = r[0] if isinstance(r, tuple) else r
        assert body["Boot"]["BootOrder"] == []
