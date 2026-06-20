# Parity Plan — to full DMTF Redfish compliance

Plan to take `proxmox-redfish` from its current provisioning-focused subset to
**full DMTF Redfish spec parity** (with documented, pragmatic exceptions where a
concept has no meaning for a virtual machine). Companion to
[`../RedFishSpecCompliance.md`](../RedFishSpecCompliance.md) (current state) and
[`ROADMAP.md`](ROADMAP.md) (SecureBoot phases).

Target schema versions = the pinned mirror in
[`redfish-reference/schemas/`](redfish-reference/) (ComputerSystem v1.28.0,
ServiceRoot v1.21.0, SecureBoot v1.2.0, Certificate v1.11.0, Base 1.23.0, …).

Each phase: docs-first delta → implement behind the existing dispatch style → unit
tests per the established pattern (mock proxmoxer / mock `hostops`) → black/isort/
flake8/mypy green → update `RedFishSpecCompliance.md` + `spec/conformance-matrix.md`.

## Effort legend

S = <1 day · M = 1–3 days · L = ~1 week. "VM-exception" = implemented as a
minimal/synthetic resource because the underlying concept is host-level, not per-VM.

---

## Phase 2 — Spec hygiene (S, high value, no new subsystems)  ✅ mostly done

Cheap correctness fixes that remove variances strict clients trip on.

| Item | Change | Status |
|------|--------|--------|
| ServiceRoot completeness | Advertise Managers, SessionService, TaskService (+ Chassis/Account/Event/Update as they land); real `RedfishVersion` `1.18.0` + `UUID`. | ✅ |
| `@odata.type` bump | Current schema versions, centralized in `redfish_core.ODATA_TYPES`. | ✅ |
| ResetType reconcile | Advertised == handled; `Nmi`→reset, `PowerCycle`→stop+start; Pause/Resume kept as unadvertised extras. | ✅ |
| Session DELETE + GET | `DELETE`/`GET /SessionService/Sessions/{id}` + collection; `do_DELETE` added. | ✅ |
| Memory resource | `GET /Systems/{id}/Memory` collection + `/DRAM` member. | ✅ |
| `Bios/SMBIOS` | Move under `Oem` or document as OEM. | ⬜ deferred |
| Error registry version | Bump `Base.1.0` → mirrored `Base.1.x`. | ⬜ deferred (test coupling; with Phase 5) |

**Acceptance:** ServiceRoot traversal reaches every implemented resource ✅; advertised
== handled for ResetType ✅; sessions create + delete ✅; emitted `@odata.type` match
mirrored schema versions ✅. (Two cosmetic items deferred — see status column.)

---

## Phase 3 — UEFI + async (M, directly affects provisioning success)  ✅ done

| Item | Change | Status |
|------|--------|--------|
| **UEFI efidisk auto-provision** | On `PATCH Bios FirmwareMode=UEFI`, create a 4m `efidisk0` if absent (`ensure_efidisk`). Toggle `REDFISH_AUTO_EFIDISK`, storage `REDFISH_EFIDISK_STORAGE`. | ✅ |
| Real TaskService | `GET /TaskService` + `/Tasks` + `/Tasks/{upid}` mapping Proxmox UPID → Redfish `TaskState`/`PercentComplete` (`redfish_core.build_task`). | ✅ |
| Async `Location` headers | 202 responses (do_POST/do_PATCH) set `Location` to the resolvable Task URI. | ✅ |

**Acceptance:** a client that POSTs `ComputerSystem.Reset` and polls the returned
`Location` gets a valid Task that transitions to `Completed` ✅; switching a BIOS VM to
UEFI yields a 4m efidisk and a subsequently-successful SecureBoot enroll ✅.

---

## Phase 4 — SecureBoot completion (L)  ✅ core done

Finish the SecureBoot resource to full schema (extends ROADMAP P3/P4).

| Item | Change | Status |
|------|--------|--------|
| Certificate collection | `GET/POST/DELETE .../SecureBootDatabases/{db}/Certificates` (public PEM/DER only; INV-13). | ✅ |
| Dynamic varstore build | Build from staged PK/KEK/db certs via `virt-fw-vars` (`hostops.build_varstore_from_certs`), apply through the Phase-1 executor. `PATCH SecureBootEnable=true` uses staged certs when present. | ✅ |
| Per-database ResetKeys | `#SecureBootDatabase.ResetKeys`. | ⬜ |
| `SecureBootDesiredMode` (v1.2.0), `Signatures`, `dbr`/`dbt`/`*Default` | Full database/property set. | ⬜ |

**Acceptance:** cert CRUD works with public PEM/DER ✅; varstore built from staged certs
enrolls via the guarded executor ✅; **private-key input rejected** (unit + end-to-end
handler tests) ✅. Remaining: per-database ResetKeys, Signatures, desired-mode, default DBs.

---

## Phase 5 — Compliance proof + VirtualMedia (M)

| Item | Change |
|------|--------|
| Schema validation in tests | Validate every emitted response against the mirrored JSON Schemas (golden tests vs `redfish-reference/mockups/`). |
| `$metadata` / JsonSchemas / Registries | Serve the OData `$metadata` document and a `Registries` index referencing the Base registry. |
| VirtualMedia cleanup | Use `Cd` (not `CDROM`), expose a proper VirtualMedia collection, add `TransferProtocolType`/`WriteProtected`. |
| ETag / If-Match | Emit `@odata.etag`; honor `If-Match` on PATCH of mutable resources. |

**Acceptance:** all emitted bodies validate against the pinned schemas in CI; clients
can fetch `$metadata` and introspect.

---

## Phase 6 — Chassis (M, VM-exception)

VMs have no physical chassis/sensors, but the spec model expects one.

| Item | Change |
|------|--------|
| Chassis collection + member | `GET /Chassis` + `/Chassis/{id}` linked to the System/Manager. |
| Power / Thermal (synthetic) | Report VM vCPU/memory allocation as `PowerControl`; omit or synthesize `Thermal` with `Status: Absent`. Clearly marked OEM/synthetic. |

**Acceptance:** Chassis reachable from ServiceRoot and cross-linked from ComputerSystem;
documented as synthetic for VMs.

---

## Phase 7 — EventService (L)

| Item | Change |
|------|--------|
| EventService + subscriptions | `GET /EventService`, `POST /EventService/Subscriptions` (destination), `GET/DELETE` subscriptions. |
| Event delivery | Emit Redfish events (power state change, SecureBoot applied) to subscribers; optional SSE stream. Source events from Proxmox task/status transitions. |

**Acceptance:** a subscriber receives a power-state-change event; Ironic-style event
flows work as an alternative to polling.

---

## Phase 8 — AccountService / Roles (M/L, VM-exception)

Proxmox owns identity; expose a read-mostly mapping.

| Item | Change |
|------|--------|
| AccountService + Accounts + Roles | `GET` over Proxmox users/roles (read-only first); guarded create/delete mapping to `pveum` if explicitly enabled. |

**Acceptance:** account/role resources reflect Proxmox users; mutations gated behind an
explicit opt-in flag (avoid surprising privilege changes).

---

## Phase 9 — Remaining services + OData query (L)

| Item | Change |
|------|--------|
| UpdateService | Stub/`Absent` for VMs (no firmware), or wire to a guest-agent/host hook if ever needed. |
| CertificateService | Manage the daemon's own TLS certs (`HTTPS` cert) per schema. |
| OData query | `$expand`, `$select`, `$filter`; collection pagination (`Members@odata.nextLink`). |

**Acceptance:** `$expand` on a collection returns embedded members; pagination works on
large collections; UpdateService present (even if `Absent`).

---

## Sequencing & exit criteria

1. **Phase 2 → 3**: reaches **functional parity for Metal3/Ironic UEFI + SecureBoot
   provisioning** (the practical goal). UEFI efidisk (Phase 3a) already landed.
2. **Phase 4 → 5**: SecureBoot complete + machine-proven schema compliance.
3. **Phase 6 → 9**: full surface, with VM-exceptions documented where physical
   concepts don't apply.

**Definition of "full parity"** for this project: every resource a conformance tool
(e.g. the DMTF Redfish Service Validator) expects is present and schema-valid, with a
documented list of intentional VM-exceptions (no real Thermal sensors, UpdateService
`Absent`, AccountService read-mostly). Track exceptions in `RedFishSpecCompliance.md`.

## Tracking

- Mark items done in `RedFishSpecCompliance.md` (move ❌/🟡 → ✅) and
  `spec/conformance-matrix.md` as each ships.
- File regressions/bugs in `bugs/BUGLOG.md` (`BUG-NNN`).
- Validate continuously: run the DMTF Redfish Service Validator against the mirror in
  Phase 5+.
