#!/usr/bin/env python3
"""
Unit tests for ensure_efidisk -- UEFI efidisk auto-provisioning.

Switching a VM to UEFI must give it an OVMF varstore (efidisk0, efitype=4m) so
firmware has persistent NVRAM and SecureBoot has a target. These tests mock the
Proxmox API only.
"""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))

from proxmox_redfish.proxmox_redfish import ensure_efidisk  # noqa: E402


def _proxmox(config):
    proxmox = MagicMock()
    qemu = proxmox.nodes.return_value.qemu.return_value
    qemu.config.get.return_value = config
    return proxmox, qemu


def test_creates_4m_efidisk_when_absent(monkeypatch):
    monkeypatch.setenv("REDFISH_AUTO_EFIDISK", "1")
    monkeypatch.setenv("REDFISH_EFIDISK_STORAGE", "local-lvm")
    proxmox, qemu = _proxmox({"name": "vm", "bios": "ovmf"})
    result = ensure_efidisk(proxmox, 100)
    assert result["action"] == "created"
    assert result["efitype"] == "4m"
    assert result["secureboot_ready"] is True
    # config.set called with the allocation spec
    qemu.config.set.assert_called_once()
    kwargs = qemu.config.set.call_args.kwargs
    assert kwargs["efidisk0"] == "local-lvm:1,efitype=4m,pre-enrolled-keys=0"


def test_keeps_existing_4m_efidisk(monkeypatch):
    monkeypatch.setenv("REDFISH_AUTO_EFIDISK", "1")
    proxmox, qemu = _proxmox({"efidisk0": "local-lvm:vm-100-disk-0,efitype=4m,pre-enrolled-keys=1,size=528K"})
    result = ensure_efidisk(proxmox, 100)
    assert result["action"] == "existing"
    assert result["efitype"] == "4m"
    assert result["secureboot_ready"] is True
    qemu.config.set.assert_not_called()


def test_reports_existing_2m_not_secureboot_ready(monkeypatch):
    monkeypatch.setenv("REDFISH_AUTO_EFIDISK", "1")
    proxmox, qemu = _proxmox({"efidisk0": "local-lvm:vm-100-disk-0,efitype=2m,size=128K"})
    result = ensure_efidisk(proxmox, 100)
    assert result["action"] == "existing"
    assert result["efitype"] == "2m"
    assert result["secureboot_ready"] is False
    qemu.config.set.assert_not_called()


def test_respects_disabled_autocreate(monkeypatch):
    monkeypatch.setenv("REDFISH_AUTO_EFIDISK", "0")
    proxmox, qemu = _proxmox({"name": "vm"})
    result = ensure_efidisk(proxmox, 100)
    assert result["action"] == "skipped"
    assert result["secureboot_ready"] is False
    qemu.config.set.assert_not_called()


def test_uses_custom_storage(monkeypatch):
    monkeypatch.setenv("REDFISH_AUTO_EFIDISK", "1")
    proxmox, qemu = _proxmox({})
    ensure_efidisk(proxmox, 100, storage="ceph-pool")
    assert qemu.config.set.call_args.kwargs["efidisk0"].startswith("ceph-pool:1,efitype=4m")
