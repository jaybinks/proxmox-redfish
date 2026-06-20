# Redfish Spec Compliance

How `proxmox-redfish` maps onto the DMTF Redfish standard: what is covered, how
compliant each piece is, and every known variance from the spec. This is a living
document — update it whenever an endpoint is added or changed.

- **Standard:** DMTF Redfish (DSP0266 protocol, DSP2046 resource guide).
- **Schema mirror (source of truth):** [`docs/redfish-reference/`](docs/redfish-reference/)
  (SecureBoot v1.2.0, ComputerSystem v1.28.0, ServiceRoot v1.21.0, Certificate v1.11.0, Base 1.23.0, …).
- **Property-level matrix (SecureBoot):** [`docs/spec/conformance-matrix.md`](docs/spec/conformance-matrix.md).
- **Error model:** [`docs/spec/error-model.md`](docs/spec/error-model.md).

## Compliance legend

| Mark | Meaning |
|------|---------|
| ✅ Compliant | Implemented and shaped per the schema (modulo `@odata.type` version, see variances). |
| 🟡 Partial | Implemented but incomplete (missing properties, sub-resources, or methods). |
| ⚠️ Variance | Deviates from the standard in a way a strict client could notice. See Variances. |
| ❌ Missing | Not implemented. |

## Intended scope

This is a **Proxmox-VM-as-BMC** facade aimed at bare-metal provisioning tools
(Metal3, OpenStack Ironic, OpenShift ZTP, sushy). It deliberately covers the
**provisioning critical path** (discover → power → boot device → virtual media →
Secure Boot) and not the full enterprise BMC surface (Chassis sensors, eventing,
account management, firmware update). Coverage should be read against that goal.

## Coverage summary

| Audience | Approx. coverage |
|----------|------------------|
| Metal3 / Ironic / sushy provisioning workflow | ~80% of what those clients actually call |
| Full DMTF "spec-compliant BMC" surface | ~15–20% of the resource model |

## Endpoint coverage

### Service & session

| Resource / URI | Methods | Status | Notes |
|----------------|---------|--------|-------|
| `GET /redfish/v1` (ServiceRoot) | GET | 🟡 ⚠️ | Advertises only `Systems`. Missing links to Managers, SessionService, Chassis, TaskService, etc. `RedfishVersion` hard-coded `1.0.0`. |
| `/redfish/v1/SessionService/Sessions` | POST | 🟡 | Session create only when `AUTH=Session`. **No DELETE (logout), no GET/list.** |
| `/redfish/v1/SessionService` | GET | ❌ | Service resource not exposed. |
| HTTP Basic auth | — | ✅ | Supported on all authenticated endpoints over TLS. |

### Systems

| Resource / URI | Methods | Status | Notes |
|----------------|---------|--------|-------|
| `/redfish/v1/Systems` | GET | ✅ | Collection of VMs. |
| `/redfish/v1/Systems/{id}` | GET, PATCH | ✅ ⚠️ | GET inventory + power; PATCH sets boot order / mode. `@odata.type` `v1_0_0`. |
| `…/Actions/ComputerSystem.Reset` | POST | 🟡 ⚠️ | See ResetType variance below. |
| `…/Actions/ComputerSystem.UpdateConfig` | POST | ⚠️ | **Non-standard OEM action** (not in the schema). |
| `…/Bios` | GET, PATCH | 🟡 | PATCH only `FirmwareMode` (seabios/ovmf). No BIOS attribute registry / settings object / ETag. |
| `…/Bios/SMBIOS` | GET | ⚠️ | Non-standard sub-resource. |
| `…/Processors` (+ `/{id}`) | GET | 🟡 | Read-only inventory. |
| `…/Storage` (+ `/{id}`, `/Drives/{id}`, `/Volumes`, `/Controllers`) | GET | 🟡 | Read-only inventory. |
| `…/EthernetInterfaces` (+ `/{id}`) | GET | 🟡 | Read-only inventory. |
| `…/Memory` | GET | ❌ | Linked from the System body but no resource handler; only inline `TotalSystemMemoryGiB`. |
| `…/SecureBoot` | GET, PATCH | ✅ | See SecureBoot section. |
| `…/VirtualMedia/CDROM/Actions/VirtualMedia.{Insert,Eject}Media` | POST | ⚠️ | Works, but `CDROM` device id and Systems-side placement are non-standard (spec uses `Cd` under Managers). |
| `…/BootOptions` | GET | ❌ | Boot options collection not implemented (only `BootSourceOverride*`). |

### Managers

| Resource / URI | Methods | Status | Notes |
|----------------|---------|--------|-------|
| `/redfish/v1/Managers/{id}` | GET | 🟡 | Minimal Manager. No `Reset`, `NetworkProtocol`, `EthernetInterfaces`, `LogServices`. |
| `…/VirtualMedia` (+ `/Cd`) | GET | ✅ | Collection + Cd status. |
| `…/VirtualMedia/Cd/Actions/VirtualMedia.{Insert,Eject}Media` | POST | ✅ | sushy/Metal3 default path. |

### SecureBoot (this project's feature)

| Resource / URI | Methods | Status | Notes |
|----------------|---------|--------|-------|
| `…/SecureBoot` | GET, PATCH | ✅ | `SecureBootEnable` (RW), `SecureBootMode`, `SecureBootCurrentBoot`. `@odata.type` `#SecureBoot.v1_1_1.SecureBoot`. |
| `…/SecureBoot/Actions/SecureBoot.ResetKeys` | POST | ✅ | `ResetAllKeysToDefault` / `DeleteAllKeys` / `DeletePK` → varstore profiles. |
| `…/SecureBoot/SecureBootDatabases` | GET | ✅ | Collection PK / KEK / db / dbx. |
| `…/SecureBootDatabases/{dbid}` | GET | ✅ | `@odata.type` `#SecureBootDatabase.v1_0_2.SecureBootDatabase`. |
| `…/SecureBootDatabases/{dbid}/Certificates` | GET, POST, DELETE | ❌ → P3/P4 | Only the link today; cert CRUD is roadmapped. |
| `…/SecureBootDatabases/{dbid}/Signatures` | GET | ❌ | Hash signatures (dbx) not implemented. |
| `SecureBootDesiredMode` (v1.2.0) | PATCH | ❌ → P2 | |
| `dbr` / `dbt` / `*Default` databases | GET | ❌ | Only the four core databases exposed. |

Implementation note: SecureBoot maps the declarative Redfish model onto a host-side
OVMF varstore swap (`dd` of a pre-enrolled image onto the efidisk LV). The Redfish
*intent* is honored; the mechanism is Proxmox-specific. See
[`docs/decisions/0001-secureboot-via-varstore-swap.md`](docs/decisions/0001-secureboot-via-varstore-swap.md).

### Entirely missing services

| Service | Status | Impact |
|---------|--------|--------|
| TaskService / Tasks | ❌ | Power/Bios ops return Task-shaped bodies with a `Location`, but **`GET /redfish/v1/TaskService/Tasks/{id}` does not exist** — async polling 404s. |
| Chassis | ❌ | No thermal / power / sensors / fans. |
| AccountService / Accounts / Roles | ❌ | No user/role management. |
| EventService | ❌ | No event subscriptions / SSE. |
| UpdateService | ❌ | No firmware update. |
| CertificateService | ❌ | No service-level certificate management. |
| TelemetryService, LogServices | ❌ | No metrics / logs. |
| Registries, JsonSchemas, `$metadata` | ❌ | No schema/registry discovery; clients cannot introspect the service. |

## Variances from the standard

Behaviours a conformant client could observe as non-standard:

1. **`@odata.type` versions are stale.** Emitted types are `v1_0_0` for ServiceRoot,
   ComputerSystem, Manager, Bios, Processor, Storage, Drive, EthernetInterface, Task,
   VirtualMedia. The real current schema versions are far higher (e.g. ComputerSystem
   v1.22+). SecureBoot is the exception (`v1_1_1`, matching the mirrored mockup).
   Most clients tolerate this; strict schema validators will flag it.

2. **`ComputerSystem.Reset` ResetType mismatch.**
   - Advertised `ResetType@Redfish.AllowableValues`: `On, ForceOff, GracefulShutdown,
     GracefulRestart, ForceRestart, Nmi, PowerCycle`.
   - Actually handled: `On, ForceOff, GracefulShutdown, GracefulRestart, ForceRestart,
     Pause, Resume`.
   - Variance: `Nmi` and `PowerCycle` are **advertised but return 400**; `Pause` and
     `Resume` are **handled but not advertised** (and `Pause`/`Resume` are non-standard
     ResetType values — the spec uses `Suspend`/`On`).

3. **`ComputerSystem.UpdateConfig` is an OEM action** with no schema backing. Standard
   config changes should be PATCH on the resource. Should live under an `Oem` namespace.

4. **VirtualMedia naming/placement.** The Systems-side path uses device id `CDROM`
   (`/Systems/{id}/VirtualMedia/CDROM/...`); the spec uses `Cd` and historically places
   VirtualMedia under Managers. The Managers-side `Cd` path is compliant; the Systems
   `CDROM` path is a convenience variance.

5. **`Bios/SMBIOS`** is a non-standard sub-resource (SMBIOS data isn't a Redfish Bios
   child in the schema).

6. **Error registry version.** Errors use `Base.1.0.*` message IDs; the current Base
   registry is `Base.1.23.0` (mirrored locally). The envelope shape is correct; the
   version prefix is dated. (SecureBoot errors use the same `Base.1.0.*` prefix for
   consistency — see `docs/spec/error-model.md`.)

7. **ServiceRoot is not a complete map.** It advertises only `Systems`, so a client that
   discovers the tree strictly from the root will not find Managers, SessionService, or
   SecureBoot via traversal (direct URIs work).

8. **`RedfishVersion` reports `1.0.0`** regardless of the actual protocol features.

## Protocol conformance (DSP0266)

| Capability | Status | Notes |
|------------|--------|-------|
| JSON over HTTPS, TLS | ✅ | TLS required for authenticated endpoints. |
| HTTP Basic auth | ✅ | |
| Session auth (`X-Auth-Token`) | 🟡 | Create only; no DELETE/list. |
| Redfish error envelope (`error` + `@Message.ExtendedInfo`) | ✅ ⚠️ | Correct shape; `Base.1.0` version prefix is dated. |
| Status codes (200/201/202/4xx/5xx) | 🟡 | 202 used for "tasks" but no real Task to poll. |
| Async Task lifecycle (`Location` → `GET Task`) | ❌ | Task bodies returned, no Task service. |
| ETag / If-Match concurrency | ❌ | No conditional requests on PATCH. |
| OData query (`$expand`, `$select`, `$filter`) | ❌ | |
| Collection pagination (`Members@odata.nextLink`) | ❌ | Collections returned whole. |
| `$metadata` / JsonSchema / Registry discovery | ❌ | |
| Response schema validation against DMTF schemas | ❌ | Mirror present in repo but not enforced at runtime/test. |

## Suitability by use case

- **Metal3 / Ironic / sushy bare-metal provisioning:** the critical path (System
  discovery, `ComputerSystem.Reset`, boot-device override, VirtualMedia insert/eject,
  SecureBoot enroll) is covered. Main risks: clients that poll the async Task `Location`,
  or that leak sessions without DELETE.
- **General Redfish client / CMDB / monitoring:** limited — no Chassis, sensors, events,
  accounts, or schema discovery.

## Highest-value gaps to close next

Prioritized for the provisioning use case (also tracked in
[`docs/ROADMAP.md`](docs/ROADMAP.md)):

1. **Real TaskService** (`GET /TaskService/Tasks/{id}`) so async `Location` polling works.
2. **ServiceRoot link completeness** + accurate `RedfishVersion`.
3. **Session DELETE** (logout) and session listing.
4. **ResetType reconciliation** — advertise exactly what is handled (drop Nmi/PowerCycle
   or implement them; map Pause/Resume to standard values).
5. **`@odata.type` version bump** to current schema versions.
6. **Runtime/test schema validation** against `docs/redfish-reference/schemas/`.
7. **SecureBoot Certificate CRUD** (P3/P4) + dynamic varstore build.

## How to re-audit

```bash
# Endpoints handled by the dispatchers:
grep -nE 'path ?== ?"|path\.startswith|parts\[[0-9]\] ?==|in path' \
  src/proxmox_redfish/proxmox_redfish.py
# @odata.type versions emitted:
grep -oE '#[A-Za-z]+\.v[0-9_]+\.[A-Za-z]+' src/proxmox_redfish/proxmox_redfish.py | sort -u
# Compare against the mirrored schemas:
ls docs/redfish-reference/schemas/
```

Update this document and `docs/spec/conformance-matrix.md` together whenever endpoints
or emitted types change.
