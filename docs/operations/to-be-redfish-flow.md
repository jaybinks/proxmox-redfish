# TO-BE — Redfish-driven Secure Boot enrollment

The same end state as [baseline-manual-workflow.md](baseline-manual-workflow.md), driven through
the standard Redfish API instead of hand-run `dd`. Each `TO-BE-NN` step cites the AS-IS step it
replaces and the guarding invariant(s).

## Client flow (e.g. curl, sushy, Metal3/Ironic)

```
# Enable Secure Boot with the org's keys on VM 3009:
PATCH https://<host>:8443/redfish/v1/Systems/3009/SecureBoot
Content-Type: application/json
{ "SecureBootEnable": true }

# or, equivalently, the standard action:
POST  https://<host>:8443/redfish/v1/Systems/3009/SecureBoot/Actions/SecureBoot.ResetKeys
{ "ResetKeysType": "ResetAllKeysToDefault" }
```

The client never sees `dd`, the device path, or the host — Redfish abstracts it.

## Daemon-side steps

| ID | Step | Replaces | Guarded by |
|----|------|----------|-----------|
| TO-BE-01 | Validate request body + `vmid`; resolve target profile from the catalog map. | — | INV-01, INV-13 |
| TO-BE-02 | `locate_efidisk(proxmox, vmid)`: read `efidisk0` from VM config, parse, resolve & verify the block device. | AS-IS-02, AS-IS-03 | INV-02, INV-04, INV-05, INV-06, INV-07 |
| TO-BE-03 | `stopped_vm_guard`: confirm VM stopped via API, take per-VM lock, re-check stopped. | AS-IS-01 | INV-08, INV-09, INV-15 |
| TO-BE-04 | `write_varstore_image`: allowlist + sha256 + size checks; idempotent short-circuit; `dd ... conv=fsync,notrunc` as argv. | AS-IS-04 | INV-10, INV-11, INV-12, INV-14, INV-16, INV-19 |
| TO-BE-05 | Post-write read-back verify; audit-log outcome; update sidecar state. | AS-IS-06 | INV-17, INV-18 |
| TO-BE-06 | (optional) restart VM if it was running and autostop was allowed. | AS-IS-05 | — |
| TO-BE-07 | Return refreshed `GET /SecureBoot` (200) or Redfish error envelope. | — | INV-20 |

## Traceability table (closes the loop)

| AS-IS | TO-BE | Invariants | Requirement | Test (planned) |
|-------|-------|-----------|-------------|----------------|
| AS-IS-01 (stop VM) | TO-BE-03 | INV-08, INV-09 | REQ-F-SB-05 | test_secureboot::refuses_running_vm |
| AS-IS-03 (pick disk) | TO-BE-02 | INV-02, INV-05, INV-06, INV-07 | REQ-N-SEC-02 | test_hostops::refuses_wrong_disk |
| AS-IS-04 (dd write) | TO-BE-04 | INV-10..14, INV-16, INV-19 | REQ-F-SB-03 | test_hostops::write_argv, dry_run_no_write |
| AS-IS-06 (verify) | TO-BE-05 | INV-17, INV-18 | REQ-N-REL-01/02 | test_hostops::post_write_verify |

## What the client gains

- No raw device paths, no SSH into the host, no manual stop/start choreography.
- Machine-enforced safety: every AS-IS risk is now a refused-by-default invariant.
- Standard tooling (sushy, Metal3, Ironic, curl) can drive it like any vendor BMC.
