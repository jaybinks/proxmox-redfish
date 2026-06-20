# Design — OVMF varstore facts & profile catalog

## What the varstore is

The OVMF UEFI variable store (`OVMF_VARS_4M.fd`) is the firmware's persistent NVRAM region. For
`efitype=4m` it is **exactly 540,672 bytes (528 KB)**. It holds:

| Variable | Meaning | In a typical enrolled image |
|----------|---------|------------------------------|
| `PK` | Platform Key — root of SB trust | custom cert (~1.4 KB) |
| `KEK` | Key Exchange Key — authorizes db/dbx updates | custom cert |
| `db` | authorized signature DB — what may boot | custom cert |
| `dbx` | forbidden signature DB — revocations | minimal/empty |
| `SecureBootEnable` | firmware SB toggle | ON |
| BootOrder/Boot####, SetupMode/SecureBoot, timeouts, console | UEFI bookkeeping | as built |

**Public certs only.** PK/KEK/db are X.509 public keys. Private signing keys (which sign the
UKI/bootloader) live on the build host and never enter the varstore or this daemon.

## Why host-side write (not in-guest)

The varstore is a QEMU **pflash** device (`-drive if=pflash,unit=1,...`), not exposed as a guest
block device. So nothing inside the guest can reach it. The efidisk is reachable from the Proxmox
host as the LVM LV `/dev/pve/vm-<vmid>-disk-N`, so enrollment is done host-side by overwriting the
LV **while the VM is stopped** (QEMU holds the pflash open while running). On bare metal / a VM in
real UEFI Setup Mode, the same PK/KEK/db can be written through `efivarfs` instead — same end state.

## efitype must be 4m

Secure Boot requires `efitype=4m` (528 KB varstore). `2m` (128 KB) is rejected (INV-04): the image
sizes differ and a 4m image will not fit/align in a 2m store.

Proxmox config line example:
```
efidisk0: local-lvm:vm-3009-disk-0,efitype=4m,pre-enrolled-keys=1,size=528K
```

## Profile catalog (Phase 1 static swap)

Profiles are pre-baked varstore images on the host, referenced by name. Config file
`REDFISH_SB_PROFILES` (default `/opt/proxmox-redfish/config/secureboot_profiles.json`):

```json
{
  "profiles": {
    "ngv-sb-on": {
      "description": "Custom NGV PK/KEK/db, --no-microsoft, SecureBootEnable=ON",
      "image_path": "/opt/proxmox-redfish/varstores/ngv-ovmf-vars.img",
      "efitype": "4m",
      "secure_boot": true,
      "databases": { "PK": true, "KEK": true, "db": true, "dbx": false },
      "size_bytes": 540672,
      "image_sha256": "<fill at install>"
    },
    "sb-off-blank": {
      "description": "Blank varstore, SetupMode, SecureBootEnable=OFF",
      "image_path": "/opt/proxmox-redfish/varstores/OVMF_VARS_4M.blank.fd",
      "efitype": "4m",
      "secure_boot": false,
      "databases": { "PK": false, "KEK": false, "db": false, "dbx": false },
      "size_bytes": 540672,
      "image_sha256": "<fill at install>"
    }
  },
  "default_profile": "ngv-sb-on",
  "map": {
    "SecureBootEnable:true": "ngv-sb-on",
    "SecureBootEnable:false": "sb-off-blank",
    "ResetAllKeysToDefault": "ngv-sb-on",
    "DeleteAllKeys": "sb-off-blank",
    "DeletePK": "sb-off-blank"
  }
}
```

The per-VM image is **vmid-agnostic** for enrollment — the same NGV keys apply to any VM. (The
current manual workflow names files per-VM, e.g. `ngv-ovmf-vars-3009.img`, but the key content is
identical; the daemon can use one shared image. If a deployment genuinely needs per-VM images, add a
`per_vm_image_dir` and resolve `ngv-ovmf-vars-<vmid>.img`, still inside the allowlisted dir.)

## How a profile image is built (reference; out of daemon scope for Phase 1)

`local-sb-vars.sh` equivalent — take a blank `OVMF_VARS_4M.fd`, enroll keys, flip SB on:
```
virt-fw-vars --input OVMF_VARS_4M.fd --output ngv-ovmf-vars.img \
  --set-pk  <guid> tools/secureboot/keys/PK.crt \
  --add-kek <guid> tools/secureboot/keys/KEK.crt \
  --add-db  <guid> tools/secureboot/keys/db.crt \
  --no-microsoft --secure-boot
```
Phase 3 moves this build **into** the daemon (from POSTed public certs). Phase 1 just swaps the
pre-built image.

## Build-host vs runtime

Profile images are installed onto the PVE host out-of-band (config management), their sha256
recorded in the profile catalog. The daemon verifies that sha256 before every write (INV-11) — so a
tampered or wrong image is refused.
