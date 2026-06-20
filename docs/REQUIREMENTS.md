# Requirements — Redfish SecureBoot for Proxmox

## 1. Scope & definitions

- **efidisk** — the Proxmox virtual disk backing the OVMF UEFI variable store (NVRAM). Declared in
  VM config as `efidisk0: <storage>:vm-<vmid>-disk-N,efitype=4m,...`. On LVM storage it resolves
  to the block device `/dev/pve/vm-<vmid>-disk-N`.
- **varstore** — the OVMF `OVMF_VARS_4M.fd` image (540,672 bytes for efitype=4m) holding UEFI
  variables: `PK`, `KEK`, `db`, `dbx`, `SecureBootEnable`, plus boot bookkeeping.
- **PK / KEK / db / dbx** — UEFI Secure Boot key databases (Platform Key / Key Exchange Key /
  authorized signatures / forbidden signatures). We handle **public certificates only**.
- **profile** — a named, pre-baked varstore image on the host (e.g. your `ngv-ovmf-vars-3009.img`)
  with a known key set + SecureBootEnable state.
- **pflash** — QEMU device exposing the varstore to firmware; **not** visible inside the guest,
  so enrollment is done host-side while the VM is stopped.

## 2. Functional requirements — SecureBoot feature

| ID | Requirement |
|----|-------------|
| REQ-F-SB-01 | Expose a Redfish `SecureBoot` resource at `/redfish/v1/Systems/{vmid}/SecureBoot` for each VM. |
| REQ-F-SB-02 | Report current Secure Boot state (`SecureBootEnable`, `SecureBootMode`, `SecureBootCurrentBoot`) for the VM. |
| REQ-F-SB-03 | Apply a pre-baked varstore profile (key set + SB on/off) to a **stopped** VM's efidisk via the host-ops write path. |
| REQ-F-SB-04 | Map `SecureBootEnable` PATCH and `SecureBoot.ResetKeys` action onto profile application (`ResetAllKeysToDefault`/`DeleteAllKeys`/`DeletePK`). |
| REQ-F-SB-05 | Refuse any apply unless the VM is confirmed stopped, returning a spec-compliant Redfish error. |
| REQ-F-SB-06 | Expose the `SecureBootDatabases` collection (`PK`, `KEK`, `db`, `dbx`) and each database resource. |
| REQ-F-SB-07 | Provide a dry-run/preview that runs all safety checks and reports the intended write without writing. |
| REQ-F-SB-08 | Make the available profiles discoverable (OEM block on the SecureBoot resource). |
| REQ-F-SB-09 | (Phase 3) Accept public PEM certs via `Certificates` POST and build a varstore via `virt-fw-vars`. |
| REQ-F-SB-10 | (Phase 4) Support `Certificate` DELETE and per-database `SecureBootDatabase.ResetKeys`. |

## 3. Functional requirements — Redfish compliance

| ID | Requirement |
|----|-------------|
| REQ-F-RF-01 | ServiceRoot advertises supported resources/version; `Systems/{id}` links to `SecureBoot`. |
| REQ-F-RF-02 | All emitted resources validate against the pinned DMTF schemas in `redfish-reference/schemas/`. |
| REQ-F-RF-03 | Errors use the Redfish error envelope with Base-registry `MessageId`s + `@Message.ExtendedInfo`. |
| REQ-F-RF-04 | Correct `@odata.id` / `@odata.type` on every SecureBoot resource (versions per the conformance matrix). |
| REQ-F-RF-05 | HTTP semantics: 200 for sync success, 202+Task for async, 4xx/5xx with error envelope; ETag/If-Match where DSP0266 requires. |

## 4. Non-functional requirements

| ID | Requirement |
|----|-------------|
| REQ-N-SEC-01 | No private key material is ever accepted, stored, or logged (enforced by INV-13). |
| REQ-N-SEC-02 | Every block-device write satisfies **all** invariants INV-01..INV-20 in [SECURITY.md](SECURITY.md). |
| REQ-N-SEC-03 | No shell string interpolation; all host commands run as argv arrays via `subprocess.run(..., shell=False)` (INV-14). |
| REQ-N-REL-01 | Writes are precondition-gated, post-write verified, and **fail closed** on any ambiguity. |
| REQ-N-REL-02 | Every dangerous operation is audit-logged with full provenance (INV-17). |
| REQ-N-MNT-01 | Unit tests accompany each requirement; coverage held at/above the current bar (~85%). |
| REQ-N-MNT-02 | Code passes the existing gates: black (line-length 120), isort, flake8, mypy. |
| REQ-N-PORT-01 | Targets Proxmox VE 7.0+, Python 3.8+ (matches README). |
| REQ-N-OBS-01 | Structured, level-controlled logging consistent with the existing daemon logging. |

## 5. Traceability

| REQ | Spec section | Invariants | Test (planned) | Phase |
|-----|--------------|-----------|----------------|-------|
| REQ-F-SB-01/02 | spec/redfish-secureboot-api.md §SecureBoot | — | test_secureboot::get_* | 1 |
| REQ-F-SB-03/04 | spec §ResetKeys, §PATCH | INV-01..20 | test_secureboot::patch_*, action_reset_keys | 1 |
| REQ-F-SB-05 | spec §errors | INV-08, INV-09 | test_secureboot::refuses_running_vm | 1 |
| REQ-F-SB-06 | spec §Databases | — | test_secureboot::get_db_collection | 1 |
| REQ-F-SB-07 | design/secureboot-dd-write.md | INV-16 | test_hostops::dry_run_no_write | 1 |
| REQ-N-SEC-02 | SECURITY.md | INV-01..20 | test_hostops::invariant_* (one per INV) | 1 |
| REQ-F-RF-02 | spec/conformance-matrix.md | — | test_schema_validation | 2 |
| REQ-F-SB-09 | spec §Certificates | INV-13 | test_hostops::rejects_private_key | 3 |
