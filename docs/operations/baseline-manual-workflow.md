# AS-IS — manual Secure Boot enrollment (the baseline we are automating)

This is the current hand-run procedure, captured verbatim so the Redfish automation
([to-be-redfish-flow.md](to-be-redfish-flow.md)) is traceable to it. Each step has an `AS-IS-NN` ID
and a risk annotation; the risks directly motivate the `INV-*` guard rails in
[../SECURITY.md](../SECURITY.md).

## Context

- Host: a Proxmox VE node. Example VM: `vmid=3009`.
- Image: `ngv-ovmf-vars-3009.img` — a copy of `tools/secureboot/OVMF_VARS_4M.dev.fd`, a 528 KB
  (540,672-byte) OVMF varstore with custom NGV `PK`/`KEK`/`db` enrolled (`--no-microsoft`, so
  Microsoft keys are deliberately absent) and `SecureBootEnable=ON`. `dbx` minimal/empty.
- Public certs only; the private signing keys never leave the build host.
- Why host-side: the varstore is a QEMU pflash device, not visible inside the guest; it is
  reachable from the host as the efidisk LVM LV. QEMU holds the pflash while the VM runs, so the
  VM must be stopped.

## Procedure

| ID | Step | Command (as run) | Risk |
|----|------|------------------|------|
| AS-IS-01 | Stop the VM | `qm stop 3009` | If skipped, QEMU holds the pflash → corrupt/incoherent write. |
| AS-IS-02 | Confirm efidisk in config | `qm config 3009 \| grep efidisk0` | Operator may misread which disk is the efidisk. |
| AS-IS-03 | Identify the LV path | (read from config) `local-lvm:vm-3009-disk-0` → `/dev/pve/vm-3009-disk-0` | **Highest risk: picking the wrong disk number overwrites an unrelated disk = data loss.** |
| AS-IS-04 | Write the varstore | `dd if=/var/lib/vz/template/iso/ngv-ovmf-vars-3009.img of=/dev/pve/vm-3009-disk-0 bs=1M conv=notrunc` | Wrong `of=` target, oversized image, or truncation = catastrophic. |
| AS-IS-05 | Start the VM | `qm start 3009` | — |
| AS-IS-06 | Verify | VM boots into enforcing Secure Boot under NGV keys; a UKI not signed by `db` is refused ("Access denied"). | No automated post-write verification today. |

`conv=notrunc` = write in place, don't shrink the LV. `bs=1M` = 1 MB blocks. The 528 KB image
overwrites the varstore region; the rest of the LV is left intact.

## Why this is risky to do by hand

- The destination is a raw block device path typed by a human (AS-IS-03/04). One wrong digit =
  irreversible loss.
- No machine check that the VM is actually stopped, that the target is really the efidisk, that the
  image size fits, or that the write landed correctly.
- These exact gaps become the automated invariants: AS-IS-01 → INV-08/09; AS-IS-03 → INV-02/05/06/07;
  AS-IS-04 → INV-10/11/12/14/18.

## On-bare-metal variant (for completeness)

On real hardware or a VM in genuine UEFI Setup Mode, `ngv-sb-enroll` writes the same PK/KEK/db
through `efivarfs` instead of `dd` — same end state, different write path. The Redfish daemon
targets the Proxmox/efidisk case (AS-IS above).
