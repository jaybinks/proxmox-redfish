#!/usr/bin/env python3
"""
Unit tests for proxmox_redfish.hostops -- the sole shell-out boundary.

Each test maps to a safety invariant in docs/SECURITY.md (INV-01..INV-20). The
subprocess chokepoint (hostops._run) and device I/O are mocked, so ``dd`` never
runs. Tests assert that the executor REFUSES when an invariant is violated and
PROCEEDS only when all hold, and that the write uses an argv array (never a shell).
"""

import os
import stat as stat_mod
from unittest.mock import MagicMock, patch

import pytest

from proxmox_redfish import hostops
from proxmox_redfish.hostops import (
    DeviceResolveError,
    EfiDisk,
    ImageHashMismatchError,
    ImageSizeMismatchError,
    InvalidVmidError,
    NoEfiDiskError,
    SourceNotAllowedError,
    TemplateMissingError,
    UnsupportedEfiTypeError,
    VmRunningError,
    WriteVerifyError,
)


def _completed(returncode=0, stdout=b"", stderr=b""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def _mock_proxmox(config=None, status=None):
    """Build a proxmoxer-like mock: proxmox.nodes(NODE).qemu(vmid).config.get() etc."""
    proxmox = MagicMock()
    qemu = proxmox.nodes.return_value.qemu.return_value
    qemu.config.get.return_value = config if config is not None else {}
    qemu.status.current.get.return_value = status if status is not None else {"status": "stopped"}
    return proxmox


def _efidisk(tmp_path, size_bytes=hostops.EFI_VARSTORE_SIZE_4M):
    return EfiDisk(
        volid="local-lvm:vm-100-disk-0",
        storage="local-lvm",
        device_path="/dev/pve/vm-100-disk-0",
        efitype="4m",
        pre_enrolled=True,
        size_bytes=size_bytes,
    )


# --------------------------------------------------------------------------- #
# INV-01: vmid validation
# --------------------------------------------------------------------------- #
class TestValidateVmid:
    def test_accepts_int_and_numeric_string(self):
        assert hostops.validate_vmid(100) == 100
        assert hostops.validate_vmid("3009") == 3009

    @pytest.mark.parametrize("bad", ["abc", None, 1.5, "10.0", "", "100; rm -rf /"])
    def test_rejects_non_integer(self, bad):
        with pytest.raises(InvalidVmidError):
            hostops.validate_vmid(bad)

    @pytest.mark.parametrize("bad", [0, -1, 99, hostops.VMID_MAX + 1])
    def test_rejects_out_of_range(self, bad):
        with pytest.raises(InvalidVmidError):
            hostops.validate_vmid(bad)

    def test_rejects_bool(self):
        with pytest.raises(InvalidVmidError):
            hostops.validate_vmid(True)


# --------------------------------------------------------------------------- #
# efidisk parsing + INV-02/03/04 locate
# --------------------------------------------------------------------------- #
class TestParseEfidisk:
    def test_parses_volid_and_options(self):
        parsed = hostops.parse_efidisk_config("local-lvm:vm-3009-disk-0,efitype=4m,pre-enrolled-keys=1,size=528K")
        assert parsed["volid"] == "local-lvm:vm-3009-disk-0"
        assert parsed["storage"] == "local-lvm"
        assert parsed["efitype"] == "4m"
        assert parsed["pre-enrolled-keys"] == "1"

    def test_malformed_raises(self):
        with pytest.raises(NoEfiDiskError):
            hostops.parse_efidisk_config("garbage-no-colon")


class TestLocateEfidisk:
    def test_no_efidisk_refuses(self):
        proxmox = _mock_proxmox(config={"name": "vm"})  # INV-03
        with pytest.raises(NoEfiDiskError):
            hostops.locate_efidisk(proxmox, 100)

    def test_efitype_2m_refused(self):
        proxmox = _mock_proxmox(config={"efidisk0": "local-lvm:vm-100-disk-0,efitype=2m,size=128K"})
        with pytest.raises(UnsupportedEfiTypeError):  # INV-04
            hostops.locate_efidisk(proxmox, 100)

    def test_happy_path_resolves_device(self):
        proxmox = _mock_proxmox(config={"efidisk0": "local-lvm:vm-100-disk-0,efitype=4m,pre-enrolled-keys=1,size=528K"})
        with patch.object(hostops, "_verify_device_path"), patch.object(hostops, "_run") as run:
            run.side_effect = [
                _completed(stdout=b"/dev/pve/vm-100-disk-0\n"),  # pvesm path
                _completed(stdout=str(hostops.EFI_VARSTORE_SIZE_4M).encode()),  # blockdev
            ]
            efi = hostops.locate_efidisk(proxmox, 100)
        assert efi.device_path == "/dev/pve/vm-100-disk-0"
        assert efi.efitype == "4m"
        assert efi.size_bytes == hostops.EFI_VARSTORE_SIZE_4M


# --------------------------------------------------------------------------- #
# INV-05/06/07: device path verification
# --------------------------------------------------------------------------- #
class TestVerifyDevicePath:
    def test_rejects_path_not_matching_pattern(self, monkeypatch):
        monkeypatch.setenv("REDFISH_SB_VG_ALLOWLIST", "pve")
        # device belongs to a different vmid -> INV-05/07
        with pytest.raises(DeviceResolveError):
            hostops._verify_device_path("/dev/pve/vm-999-disk-0", 100)

    def test_rejects_outside_allowlisted_vg(self, monkeypatch):
        monkeypatch.setenv("REDFISH_SB_VG_ALLOWLIST", "pve")
        with pytest.raises(DeviceResolveError):
            hostops._verify_device_path("/dev/otherevg/vm-100-disk-0", 100)

    def test_rejects_non_block_device(self, monkeypatch):
        monkeypatch.setenv("REDFISH_SB_VG_ALLOWLIST", "pve")
        with patch.object(hostops.os.path, "realpath", return_value="/dev/dm-0"), patch.object(
            hostops.os, "stat", return_value=MagicMock(st_mode=0o100644)  # regular file
        ):
            with pytest.raises(DeviceResolveError):  # INV-06
                hostops._verify_device_path("/dev/pve/vm-100-disk-0", 100)

    def test_accepts_block_device(self, monkeypatch):
        monkeypatch.setenv("REDFISH_SB_VG_ALLOWLIST", "pve")
        blk_mode = stat_mod.S_IFBLK | 0o660
        with patch.object(hostops.os.path, "realpath", return_value="/dev/dm-0"), patch.object(
            hostops.os, "stat", return_value=MagicMock(st_mode=blk_mode)
        ):
            hostops._verify_device_path("/dev/pve/vm-100-disk-0", 100)  # no raise


# --------------------------------------------------------------------------- #
# INV-08/09/15: stopped VM guard
# --------------------------------------------------------------------------- #
class TestStoppedVmGuard:
    def test_running_vm_without_autostop_refuses(self):
        proxmox = _mock_proxmox(status={"status": "running"})
        with pytest.raises(VmRunningError):  # INV-08
            with hostops.stopped_vm_guard(proxmox, 100, allow_autostop=False):
                pass

    def test_stopped_vm_yields(self):
        proxmox = _mock_proxmox(status={"status": "stopped"})
        with hostops.stopped_vm_guard(proxmox, 100) as was_running:
            assert was_running is False

    def test_vm_starts_during_lock_aborts(self):
        proxmox = _mock_proxmox(status={"status": "stopped"})
        # entry check stopped, re-check after lock shows running -> INV-09
        with patch.object(hostops, "vm_is_running", side_effect=[False, True]):
            with pytest.raises(VmRunningError):
                with hostops.stopped_vm_guard(proxmox, 100):
                    pass


# --------------------------------------------------------------------------- #
# INV-10..20: the write
# --------------------------------------------------------------------------- #
class TestWriteVarstoreImage:
    def _make_image(self, tmp_path, content=b"X" * 1024):
        img = tmp_path / "ngv-ovmf-vars.img"
        img.write_bytes(content)
        return str(img)

    def test_source_outside_allowlist_refuses(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REDFISH_SB_VARSTORE_DIR", str(tmp_path / "allowed"))
        os.makedirs(tmp_path / "allowed")
        outside = tmp_path / "evil.img"
        outside.write_bytes(b"X" * 1024)
        with pytest.raises(SourceNotAllowedError):  # INV-10
            hostops.write_varstore_image(_efidisk(tmp_path), str(outside))

    def test_missing_file_refuses(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REDFISH_SB_VARSTORE_DIR", str(tmp_path))
        with pytest.raises(TemplateMissingError):  # INV-11
            hostops.write_varstore_image(_efidisk(tmp_path), str(tmp_path / "nope.img"))

    def test_sha_mismatch_refuses(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REDFISH_SB_VARSTORE_DIR", str(tmp_path))
        img = self._make_image(tmp_path)
        with pytest.raises(ImageHashMismatchError):  # INV-11
            hostops.write_varstore_image(_efidisk(tmp_path), img, expected_sha256="deadbeef")

    def test_oversized_image_refuses(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REDFISH_SB_VARSTORE_DIR", str(tmp_path))
        img = self._make_image(tmp_path, content=b"X" * 4096)
        efi = _efidisk(tmp_path, size_bytes=2048)  # LV smaller than image
        with pytest.raises(ImageSizeMismatchError):  # INV-12
            hostops.write_varstore_image(efi, img)

    def test_idempotent_short_circuit(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REDFISH_SB_VARSTORE_DIR", str(tmp_path))
        img = self._make_image(tmp_path)
        sha = hostops._sha256_file(img)
        with patch.object(hostops, "_region_sha256", return_value=sha), patch.object(hostops, "_run") as run:
            result = hostops.write_varstore_image(_efidisk(tmp_path), img)
        assert result.wrote is False and result.verified is True  # INV-19
        run.assert_not_called()

    def test_dry_run_default_does_not_write(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REDFISH_SB_VARSTORE_DIR", str(tmp_path))
        monkeypatch.delenv("REDFISH_SB_ALLOW_WRITE", raising=False)  # default => dry-run
        img = self._make_image(tmp_path)
        with patch.object(hostops, "_region_sha256", return_value=None), patch.object(hostops, "_run") as run:
            result = hostops.write_varstore_image(_efidisk(tmp_path), img)
        assert result.dry_run is True and result.wrote is False  # INV-16
        run.assert_not_called()

    def test_real_write_uses_argv_and_verifies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REDFISH_SB_VARSTORE_DIR", str(tmp_path))
        monkeypatch.setenv("REDFISH_SB_ALLOW_WRITE", "1")
        img = self._make_image(tmp_path)
        sha = hostops._sha256_file(img)
        # before-write region differs; after-write region matches (verify passes)
        with patch.object(hostops, "_region_sha256", side_effect=["different", sha]), patch.object(
            hostops, "_run", return_value=_completed(returncode=0)
        ) as run:
            result = hostops.write_varstore_image(_efidisk(tmp_path), img)
        assert result.wrote is True and result.verified is True  # INV-18
        argv = run.call_args[0][0]
        assert isinstance(argv, list)  # INV-14: argv array, not a shell string
        assert argv[0] == "dd"
        assert "conv=fsync,notrunc" in argv
        assert argv[2] == f"of={_efidisk(tmp_path).device_path}"

    def test_post_write_verify_failure_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REDFISH_SB_VARSTORE_DIR", str(tmp_path))
        monkeypatch.setenv("REDFISH_SB_ALLOW_WRITE", "1")
        img = self._make_image(tmp_path)
        with patch.object(hostops, "_region_sha256", side_effect=["different", "still-wrong"]), patch.object(
            hostops, "_run", return_value=_completed(returncode=0)
        ):
            with pytest.raises(WriteVerifyError):  # INV-18
                hostops.write_varstore_image(_efidisk(tmp_path), img)

    def test_dd_nonzero_exit_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REDFISH_SB_VARSTORE_DIR", str(tmp_path))
        monkeypatch.setenv("REDFISH_SB_ALLOW_WRITE", "1")
        img = self._make_image(tmp_path)
        with patch.object(hostops, "_region_sha256", return_value="different"), patch.object(
            hostops, "_run", return_value=_completed(returncode=1, stderr=b"dd: write error")
        ):
            with pytest.raises(WriteVerifyError):
                hostops.write_varstore_image(_efidisk(tmp_path), img)


# --------------------------------------------------------------------------- #
# Error envelope metadata sanity (used by secureboot.sb_error)
# --------------------------------------------------------------------------- #
def test_exceptions_carry_redfish_mapping():
    err = UnsupportedEfiTypeError("x")
    assert err.status == 409
    assert err.redfish_code == "Base.1.0.ActionNotSupported"
    assert err.resolution


# --------------------------------------------------------------------------- #
# VM status + region hashing helpers
# --------------------------------------------------------------------------- #
class TestVmIsRunning:
    def test_running(self):
        proxmox = _mock_proxmox(status={"qmpstatus": "running"})
        assert hostops.vm_is_running(proxmox, 100) is True

    def test_stopped(self):
        proxmox = _mock_proxmox(status={"status": "stopped"})
        assert hostops.vm_is_running(proxmox, 100) is False

    def test_none_status_treated_not_running(self):
        proxmox = _mock_proxmox(status=None)
        proxmox.nodes.return_value.qemu.return_value.status.current.get.return_value = None
        assert hostops.vm_is_running(proxmox, 100) is False


class TestRegionSha:
    def test_hashes_first_n_bytes(self, tmp_path):
        f = tmp_path / "blob"
        f.write_bytes(b"A" * 100 + b"B" * 100)
        import hashlib

        expected = hashlib.sha256(b"A" * 100).hexdigest()
        assert hostops._region_sha256(str(f), 100) == expected

    def test_returns_none_when_short(self, tmp_path):
        f = tmp_path / "blob"
        f.write_bytes(b"A" * 10)
        assert hostops._region_sha256(str(f), 1000) is None

    def test_returns_none_on_missing(self, tmp_path):
        assert hostops._region_sha256(str(tmp_path / "nope"), 10) is None


# --------------------------------------------------------------------------- #
# read_varstore_state
# --------------------------------------------------------------------------- #
class TestReadVarstoreState:
    def _efi(self):
        return EfiDisk("local-lvm:vm-100-disk-0", "local-lvm", "/dev/pve/vm-100-disk-0", "4m", True, 540672)

    def test_tool_missing_raises(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(hostops.ToolMissingError):
                hostops.read_varstore_state(self._efi())

    def test_parses_print_output(self):
        printout = b"PK: present\nKEK: present\ndb: present\ndbx: present\nSecureBootEnable: 1\n"
        with patch("shutil.which", return_value="/usr/bin/virt-fw-vars"), patch.object(
            hostops, "_run", side_effect=[_completed(returncode=0), _completed(returncode=0, stdout=printout)]
        ):
            state = hostops.read_varstore_state(self._efi())
        assert state.has_pk and state.has_kek and state.has_db
        assert state.mode == "UserMode"
        assert state.enabled is True
