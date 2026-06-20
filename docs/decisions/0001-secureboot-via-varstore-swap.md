# ADR-0001 — SecureBoot via host-side varstore swap

- **Status:** Accepted (2026-06-20)
- **Context phase:** Phase 1 (MVP)

## Context

Redfish models Secure Boot declaratively: `SecureBoot` resource, `SecureBoot.ResetKeys` action,
`SecureBootDatabases`/`Certificates` collections. Proxmox stores UEFI variables in an OVMF varstore
on a QEMU **pflash** device that is **not** exposed inside the guest. Enrollment therefore happens
host-side by writing the efidisk LVM LV (`/dev/pve/vm-<vmid>-disk-N`) while the VM is stopped.

The user's existing, working process is to `dd` a pre-baked varstore image (custom PK/KEK/db,
`--no-microsoft`, SecureBootEnable=ON) onto that LV. No existing Proxmox Redfish implementation
(jorgeventura/pve-redfish, v1k0d3n/proxmox-redfish) exposes Secure Boot at all.

## Decision

Implement the Redfish SecureBoot surface as a **static varstore-swap** backend first: named
**profiles** map to pre-baked varstore images on the host; Redfish `PATCH SecureBootEnable` and
`SecureBoot.ResetKeys` trigger a guarded host-side `dd` of the chosen image onto the efidisk LV.
The dangerous write lives in a dedicated `hostops.py` module enforcing all `INV-*` invariants;
the Redfish surface lives in `secureboot.py`.

## Alternatives considered

1. **In-guest enrollment via efivarfs.** Rejected: the varstore pflash is not reachable from inside
   the guest on Proxmox — physically impossible for the VM case.
2. **Dynamic cert build over the API first** (POST PEM → `virt-fw-vars` → build → `dd`). Deferred to
   Phase 3: more moving parts, a `virt-fw-vars` runtime dependency, and a larger attack surface;
   not needed to match today's workflow. The static swap is a 1:1 automation of the proven manual
   process, so it is the lowest-risk MVP.
3. **sushy-tools as a middleware gateway.** Rejected for this repo's goal: the user wants the
   feature in their own Proxmox-native daemon, and sushy-tools' libvirt-oriented NVRAM swap doesn't
   map cleanly to Proxmox LVM efidisks.

## Consequences

- **Positive:** matches the existing process exactly; small, auditable write path; spec-compliant
  Redfish surface that sushy/Metal3/Ironic can drive; no new heavyweight dependency for MVP.
- **Negative / risk:** the daemon must run as **root on the PVE host** and write a raw block device.
  This concentrates risk — mitigated by the `INV-*` invariants (fail-closed, derive-don't-accept,
  argv-not-shell, dry-run default, post-write verify, audit log). A future ADR will consider
  dropping root via a `CAP_DAC_OVERRIDE` helper (ROADMAP Phase 5).
- Profile images are installed out-of-band; their sha256 is recorded and verified before every write.
- `SecureBootCurrentBoot` is approximated from last-applied state in MVP (documented in the
  conformance matrix); true current-boot reporting needs guest telemetry.
