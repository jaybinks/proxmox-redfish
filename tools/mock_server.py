#!/usr/bin/env python3
"""
Mock-backed Redfish daemon launcher for conformance validation.

Starts the real RedfishRequestHandler with a canned, in-memory Proxmox backend so
the DMTF Redfish-Service-Validator (and our own structural harness) can crawl the
full resource tree over plain HTTP with no real Proxmox host. Auth is stubbed.

Usage:
    python tools/mock_server.py [port]        # default 8000
"""

import os
import sys
from unittest.mock import MagicMock

os.environ.setdefault("REDFISH_LOGGING_ENABLED", "false")
os.environ.setdefault("PROXMOX_NODE", "pve")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import proxmox_redfish.proxmox_redfish as mod  # noqa: E402

# ---- Canned Proxmox backend ------------------------------------------------
VM_CONFIG = {
    "name": "mock-vm",
    "memory": 4096,
    "cores": 2,
    "sockets": 1,
    "bios": "ovmf",
    "boot": "order=scsi0;ide2;net0",
    "scsi0": "local-lvm:vm-100-disk-1,size=32G",
    "ide2": "none,media=cdrom",
    "net0": "virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0",
    "efidisk0": "local-lvm:vm-100-disk-0,efitype=4m,pre-enrolled-keys=1,size=528K",
}


def _make_proxmox():
    proxmox = MagicMock()
    node = proxmox.nodes.return_value
    node.qemu.get.return_value = [{"vmid": 100, "name": "mock-vm", "status": "running"}]
    vm = node.qemu.return_value
    vm.config.get.return_value = dict(VM_CONFIG)
    vm.status.current.get.return_value = {"status": "running", "qmpstatus": "running"}
    node.tasks.get.return_value = []
    node.tasks.return_value.status.get.return_value = {"status": "stopped", "exitstatus": "OK", "type": "qmstart"}
    proxmox.access.users.get.return_value = [{"userid": "root@pam", "enable": 1}]
    return proxmox


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    proxmox = _make_proxmox()

    # Enforce auth like the real daemon (so security assertions are exercised):
    # any request without a Basic/Token credential is rejected.
    def _validate(headers):  # noqa: ANN001
        if headers.get("Authorization") or headers.get("X-Auth-Token"):
            return (True, "mock@pam")
        return (False, "Authentication required")

    mod.validate_token = _validate  # type: ignore[assignment]
    mod.get_proxmox_api = lambda headers: proxmox  # type: ignore[assignment]
    mod.ProxmoxAPI = lambda *a, **k: proxmox  # type: ignore[assignment]
    # Enable Redfish session login (POST /SessionService/Sessions).
    mod.AUTH = "Session"
    # SecureBoot locate_efidisk would shell out; return a canned EfiDisk.
    mod.secureboot.hostops.locate_efidisk = lambda p, v: mod.secureboot.hostops.EfiDisk(  # type: ignore[assignment]
        "local-lvm:vm-100-disk-0", "local-lvm", "/dev/pve/vm-100-disk-0", "4m", True, 540672
    )

    print(f"Mock Redfish daemon on http://0.0.0.0:{port} (Proxmox backend stubbed)")
    mod.run_server(port)


if __name__ == "__main__":
    main()
