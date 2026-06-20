# Security — Threat model & `dd` write safety invariants

This is the authoritative guard-rail list for the SecureBoot host-ops write path. The write
executor in `hostops.py` **must enforce every invariant below and fail closed** (refuse) if any
cannot be positively verified. Each `INV-NN` has (or will have) a dedicated unit test asserting
the executor *refuses* when violated and *proceeds* only when all hold.

## Asset & trust boundary

- **Asset at risk:** every LVM logical volume on the Proxmox host (`/dev/pve/*`). A wrong target =
  overwriting an unrelated VM disk = catastrophic, unrecoverable data loss.
- **Privilege:** the daemon runs as **root on the PVE host** (required for `dd` to the LV and for
  `virt-fw-vars`). This is a deliberate, documented concentration of risk (see ADR-0001).
- **Untrusted input:** the Redfish caller. **Only `vmid` (integer) and a profile name may be
  caller-influenced.** The block-device path is **derived from Proxmox config inside one trusted
  function and is never accepted from the request body.**
- **Out of scope / forbidden:** private signing keys. The daemon has no code path that accepts,
  stores, or logs a private key. Only public certs (PK/KEK/db/dbx) and pre-baked varstore images.

## Doctrine

1. **Fail closed.** Any exception, unverifiable precondition, or ambiguity aborts *before* the
   block device is opened. Uncertain state never proceeds to a write.
2. **Derive, don't accept.** The device path comes from `qemu(vmid).config` → `pvesm path`, never
   from user input.
3. **argv, never shell.** All host commands use `subprocess.run([...], shell=False)`. No f-string
   shell commands, ever.
4. **Dry-run first.** Default-on outside production; a real write requires explicit opt-in.
5. **Audit everything.** Before and after every attempt.

## Invariants (the write must satisfy ALL)

| ID | Invariant (testable) | Failure → Redfish |
|----|----------------------|-------------------|
| INV-01 | `vmid` parses as a positive integer in the valid PVE range (100–999999999); reject non-int/negative/zero/out-of-range. | 400 PropertyValueFormatError |
| INV-02 | The efidisk volid is read from `qemu(vmid).config.get()['efidisk0']`, **never** from the request body. | (design invariant) |
| INV-03 | The VM config declares an `efidisk0`; absence → refuse. | 400 ActionNotSupported |
| INV-04 | `efitype=4m`; reject `2m` or unspecified (image is a 4m varstore). | 409 ActionNotSupported |
| INV-05 | Resolved path matches the anchored regex `^/dev/pve/vm-<vmid>-disk-\d+$` with `<vmid>` substituted from the validated integer. No globbing, no `..`. | 500 GeneralError |
| INV-06 | `realpath` resolves to a block device under `/dev/` (LVM maps `/dev/pve/vm-*` → `/dev/dm-*`) (`stat.S_ISBLK`); reject regular files, dirs, or symlinks pointing elsewhere. The logical path is already pinned to `/dev/pve/...` by INV-05. | 500 GeneralError |
| INV-07 | The LV name's `vm-<vmid>-` prefix equals the validated `vmid` (cross-check). Mismatch → refuse. | 500 GeneralError |
| INV-08 | VM confirmed `stopped` via the Proxmox API (`status.current.qmpstatus == "stopped"`) immediately before the write; fresh read, not cached. | 409 ResourceInStandby |
| INV-09 | After acquiring the write lock, status is re-read and still `stopped`; changed → abort. | 409 ResourceInStandby |
| INV-10 | The source image `realpath` is inside an allowlisted varstore directory (`REDFISH_SB_VARSTORE_DIR`). Reject arbitrary paths/URLs. | 400 ActionParameterValueError |
| INV-11 | The source is a regular file matching a profile-catalog entry **and** its recorded sha256. | 409 PropertyValueConflict |
| INV-12 | Size: `image_size == LV_size` preferred (540,672 for 4m), at minimum `image_size <= LV_size`; never write an image larger than the LV. | 409 PropertyValueConflict |
| INV-13 | Inputs contain **only public certs** (PK/KEK/db). No API accepts private-key material; cert inputs are validated as certificates, not keys. | 400 ActionParameterValueError |
| INV-14 | The write executes via an **argv array** through `subprocess.run(..., shell=False)`; no user value is interpolated into a shell string. | (design invariant) |
| INV-15 | An **exclusive per-VM/device write lock** is held for the whole operation (reuse the existing file-lock helper) to block concurrent writers and concurrent power-on. | 409 ResourceInUse |
| INV-16 | **Dry-run mode** evaluates all invariants and logs the exact argv but does not write; a real write requires explicit opt-in (`REDFISH_SB_ALLOW_WRITE=1` / per-request flag). | n/a |
| INV-17 | **Audit log** before and after: timestamp, caller identity, vmid, resolved device, source image + sha256, sizes, dry-run flag, outcome. | n/a |
| INV-18 | **Post-write verify:** `dd ... conv=fsync`, then read back and compare a hash/size of the written region against the source; mismatch → flagged failure surfaced to the caller. | 500 GeneralError |
| INV-19 | **Idempotent short-circuit:** if the target already equals the desired image (hash match), skip the write and report a no-op. | 200 no-op |
| INV-20 | **Fail closed:** any exception, any unverifiable precondition, or any ambiguity (e.g. multiple candidate devices) aborts before opening the device. | 500 GeneralError |

## Defense in depth (non-precondition)

- **Privilege minimization (future):** ADR to move the actual `dd` into a tiny `CAP_DAC_OVERRIDE`
  helper invoked by an otherwise-unprivileged daemon. Tracked in ROADMAP.
- **Single-flight per VM:** rate-limit risky writes; reject overlapping apply requests.
- **One trusted resolver:** the device path is produced by a single function taking only
  `(proxmox, vmid)` and returning a verified device path or raising — callers cannot pass a path.
- **TLS + auth:** the Redfish surface stays behind the daemon's existing TLS + Basic/Session auth;
  SecureBoot mutation should require an authenticated, authorized principal.

## Reporting

Security issues in this feature: do not file in the public BUGLOG with exploit detail; contact the
maintainer directly. Track remediation as `BUG-NNN` with a `security` tag once mitigated.
