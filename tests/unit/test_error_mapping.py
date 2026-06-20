#!/usr/bin/env python3
"""handle_proxmox_error: a missing VM (Proxmox 500 'does not exist') -> Redfish 404."""

from proxmoxer.core import ResourceException

from proxmox_redfish.proxmox_redfish import handle_proxmox_error


def _exc(status, message):
    try:
        return ResourceException(status, "Internal Server Error", message)
    except TypeError:
        e = ResourceException(message)
        e.status_code = status
        return e


def test_missing_vm_config_maps_to_404():
    exc = _exc(500, "Configuration file 'nodes/pve/qemu-server/4000.conf' does not exist")
    body, code = handle_proxmox_error("VM status retrieval", exc, 4000)
    assert code == 404
    assert body["error"]["code"] == "Base.1.0.ResourceMissingAtURI"


def test_real_404_still_404():
    exc = _exc(404, "not found")
    _, code = handle_proxmox_error("op", exc, 1)
    assert code == 404


def test_403_still_privilege():
    exc = _exc(403, "Permission check failed")
    body, code = handle_proxmox_error("op", exc, 1)
    assert code == 403 and body["error"]["code"] == "Base.1.0.InsufficientPrivilege"
