# Error model

All errors use the Redfish error envelope (schema `redfish-error.v1_0_2.json`) with Base-registry
message IDs (`Base.registry.json`). Shape:

```json
{
  "error": {
    "code": "Base.1.0.GeneralError",
    "message": "A general error has occurred. See ExtendedInfo for details.",
    "@Message.ExtendedInfo": [
      {
        "@odata.type": "#Message.v1_3_0.Message",
        "MessageId": "Base.1.0.PropertyValueNotInList",
        "Message": "The value 'Foo' for the property ResetKeysType is not in the list of acceptable values.",
        "MessageSeverity": "Warning",
        "Resolution": "Choose a value from the enumeration list and resubmit.",
        "RelatedProperties": ["#/ResetKeysType"]
      }
    ]
  }
}
```

Implemented by `secureboot.sb_error(HostOpError) -> (dict, int)`, parallel to the existing
`handle_proxmox_error`. Proxmoxer failures keep flowing through `handle_proxmox_error`.

## SecureBoot host-op failures → Redfish

| Condition | Exception | HTTP | MessageId | Resolution |
|-----------|-----------|------|-----------|------------|
| VM running, no autostop | `VmRunningError` | 409 | `Base.1.0.ResourceInStandby` | "Stop the system before modifying Secure Boot keys." |
| No `efidisk0` in config | `NoEfiDiskError` | 400 | `Base.1.0.ActionNotSupported` | "Add an OVMF EFI disk to the VM before managing Secure Boot." |
| `efitype=2m` not 4m | `UnsupportedEfiTypeError` | 409 | `Base.1.0.ActionNotSupported` | "Recreate the EFI disk with efitype=4m." |
| Profile/image missing | `TemplateMissingError` | 500 | `Base.1.0.GeneralError` | "Verify the varstore image path on the host." |
| `virt-fw-vars` absent (P3) | `ToolMissingError` | 501 | `Base.1.0.ActionNotSupported` | "Install virt-firmware on the Proxmox host." |
| Image size > LV / mismatch | `ImageSizeMismatchError` | 409 | `Base.1.0.PropertyValueConflict` | "The varstore image size does not match the EFI disk; check efitype." |
| Bad ResetKeysType / SB value | (router validation) | 400 | `Base.1.0.PropertyValueNotInList` | "Choose a supported value." |
| Source outside allowlist | `SourceNotAllowedError` | 400 | `Base.1.0.ActionParameterValueError` | "Use a configured varstore image." |
| sha256 mismatch on source | `ImageHashMismatchError` | 409 | `Base.1.0.PropertyValueConflict` | "The varstore image failed integrity check." |
| Private-key-like input (P3) | `PrivateKeyRejectedError` | 400 | `Base.1.0.ActionParameterValueError` | "Provide a public certificate only." |
| Device path / block-dev check fails | `DeviceResolveError` | 500 | `Base.1.0.GeneralError` | "Internal safety check failed; no write performed." |
| Post-write verify mismatch | `WriteVerifyError` | 500 | `Base.1.0.GeneralError` | "Write could not be verified; inspect the audit log." |

Router contract:
```python
try:
    ...
except HostOpError as e:
    return sb_error(e)                       # maps the table above
except ResourceException as e:
    return handle_proxmox_error("SecureBoot op", e, vm_id)
```

Each `HostOpError` subclass carries `redfish_code`, `status`, and `resolution` so `sb_error` is a
single generic mapper. Message IDs use the `Base` registry mirrored in
`docs/redfish-reference/schemas/Base.registry.json` (adjust the registry version prefix to the
one actually advertised at runtime).
