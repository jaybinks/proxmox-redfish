# Redfish Spec Compliance

How `proxmox-redfish` maps onto the DMTF Redfish standard: what is covered, how
compliant each piece is, and every known variance from the spec. This is a living
document — update it whenever an endpoint is added or changed.

- **Standard:** DMTF Redfish (DSP0266 protocol, DSP2046 resource guide).
- **Schema mirror (source of truth):** [`docs/redfish-reference/`](docs/redfish-reference/)
  (SecureBoot v1.2.0, ComputerSystem v1.28.0, ServiceRoot v1.21.0, Certificate v1.11.0, Base 1.23.0, …).
- **Property-level matrix (SecureBoot):** [`docs/spec/conformance-matrix.md`](docs/spec/conformance-matrix.md).
- **Error model:** [`docs/spec/error-model.md`](docs/spec/error-model.md).
- **Plan to full parity:** [`docs/PARITY-PLAN.md`](docs/PARITY-PLAN.md).

## DMTF Service-Validator status

The official **DMTF Redfish-Service-Validator** (`redfish_service_validator` 3.x) runs
against the daemon in CI via a mock-backed launcher (`tools/mock_server.py`, no real
Proxmox needed) and the `.github/workflows/conformance.yml` job. Latest run:

```
Redfish-Service-Validator (schema):   PASS 639 | WARN 8  | FAIL 0   (over HTTPS)
Redfish-Protocol-Validator (DSP0266): PASS 287 | WARN 0  | FAIL 0   (over HTTPS, strict mode)
```

**Both validators report 0 FAIL** across the full crawled tree (the 8 schema warnings are
advisory). The Protocol-Validator runs in strict mode (`REDFISH_STRICT_PROTOCOL=1`, which the
mock launcher sets); in the default **lenient** mode the daemon deliberately accepts sloppy
clients (a wrong `OData-Version` or unknown `$`-params) rather than returning 412/501, for
maximum real-world client compatibility.

### Cross-client compatibility

Beyond the validators, the daemon is tested against three independent Redfish clients
(`tests/integration/test_redfish_clients.py`, CI job `client-compatibility`), all green:

| Client | Result |
|--------|--------|
| OpenStack **sushy** (Ironic) | connect, parse System/Boot/SecureBoot, drive `ComputerSystem.Reset` ✅ |
| DMTF **python-redfish-library** (`redfish`) | session login, GET ServiceRoot/Systems/SecureBoot ✅ |
| DMTF **redfishtool** (CLI) | `Systems list` ✅ |

The server runs **lenient** protocol mode by default (accepts a wrong `OData-Version` or
unknown `$`-params rather than 412/501) for maximum real-world client compatibility;
`REDFISH_STRICT_PROTOCOL=1` enforces full DSP0266 for validator runs.

Both validators run over TLS via the mock launcher (`tools/mock_server.py https`, self-signed
cert) with `--no-cert-check`, exactly as a real deployment would be tested:

```bash
python tools/mock_server.py 8443 https &
rf_service_validator   -u admin -p admin -r https://localhost:8443 --authtype Basic --nooemcheck
rf_protocol_validator  -u admin -p admin -r https://localhost:8443 --no-cert-check
```

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
| `GET /redfish/v1` (ServiceRoot) | GET | ✅ | Advertises Systems, Managers, SessionService, TaskService + `Links.Sessions`; `RedfishVersion` `1.18.0`, `UUID` (`REDFISH_SERVICE_UUID`). |
| `/redfish/v1/SessionService` | GET | ✅ | Service resource. |
| `/redfish/v1/SessionService/Sessions` | POST, GET | ✅ | Create (when `AUTH=Session`) + collection list; `Location` + `X-Auth-Token` on create. |
| `/redfish/v1/SessionService/Sessions/{id}` | GET, DELETE | ✅ | Session detail + **DELETE (logout)** via `do_DELETE`. |
| HTTP Basic auth | — | ✅ | Supported on all authenticated endpoints over TLS. |

### Systems

| Resource / URI | Methods | Status | Notes |
|----------------|---------|--------|-------|
| `/redfish/v1/Systems` | GET | ✅ | Collection of VMs. |
| `/redfish/v1/Systems/{id}` | GET, PATCH | ✅ ⚠️ | GET inventory + power; PATCH sets boot order / mode. `@odata.type` `v1_0_0`. |
| `…/Actions/ComputerSystem.Reset` | POST | 🟡 ⚠️ | See ResetType variance below. |
| `…/Actions/ComputerSystem.UpdateConfig` | POST | ⚠️ | **Non-standard OEM action** (not in the schema). |
| `…/Bios` | GET, PATCH | 🟡 | PATCH `FirmwareMode` (seabios/ovmf). Setting UEFI **auto-provisions a 4m efidisk** if absent (`REDFISH_AUTO_EFIDISK`). No BIOS attribute registry / settings object / ETag. |
| `…/Bios/SMBIOS` | GET | ⚠️ | Non-standard sub-resource. |
| `…/Processors` (+ `/{id}`) | GET | 🟡 | Read-only inventory. |
| `…/Storage` (+ `/{id}`, `/Drives/{id}`, `/Volumes`, `/Controllers`) | GET | 🟡 | Read-only inventory. |
| `…/EthernetInterfaces` (+ `/{id}`) | GET | 🟡 | Read-only inventory. |
| `…/Memory` (+ `/DRAM`) | GET | ✅ | Memory collection + member reporting `CapacityMiB` from VM config. |
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
| `…/SecureBootDatabases/{dbid}/Certificates` | GET, POST, DELETE | ✅ | Public-cert CRUD (PEM/DER); private keys rejected (INV-13); staged per-VM; `PATCH SecureBootEnable=true` builds a varstore from staged certs via `virt-fw-vars`. |
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
| TaskService / Tasks | ✅ | `GET /TaskService`, `/Tasks`, `/Tasks/{upid}` map Proxmox UPID tasks → Redfish `TaskState`/`PercentComplete`; 202 responses set a resolvable `Location`. |
| Chassis (+ Power/Thermal) | ✅ 🟡 | Collection + per-VM member (`ChassisType: VirtualMachine`); Power/Thermal are **synthetic** (empty — VMs have no physical sensors, marked `Oem.Proxmox.Synthetic`). |
| AccountService / Accounts / Roles | ✅ 🟡 | Read-only mapping of Proxmox users + standard roles (Administrator/Operator/ReadOnly). Mutation deferred (Proxmox owns identity). |
| EventService / Subscriptions | ✅ 🟡 | Service + subscription CRUD (in-memory, http(s)-only destinations). Event **delivery** not yet wired. |
| UpdateService | ✅ | Present as `State: Absent` (no firmware surface for VMs) — crawlable, honestly reported. |
| Registries, JsonSchemas | 🟡 | Empty collections present for discovery; `$metadata` document still ❌. |
| CertificateService | ❌ | No service-level (daemon TLS) certificate management. |
| TelemetryService, LogServices | ❌ | No metrics / logs. |
| `$metadata` (CSDL) | ❌ | OData metadata document not served. |

## Variances from the standard

Behaviours a conformant client could observe as non-standard:

1. ~~`@odata.type` versions are stale.~~ **Resolved** — emitted types bumped to current
   schema versions (ServiceRoot v1_16_0, ComputerSystem v1_22_0, Manager v1_16_0, Bios
   v1_2_0, Processor v1_19_0, Storage v1_15_0, Drive v1_17_0, EthernetInterface v1_12_0,
   Task v1_7_3, VirtualMedia v1_6_0). Versions are centralized in
   `redfish_core.ODATA_TYPES`.

2. ~~`ComputerSystem.Reset` ResetType mismatch.~~ **Resolved** — advertised set
   (`On, ForceOff, GracefulShutdown, GracefulRestart, ForceRestart, Nmi, PowerCycle`)
   now equals the handled set (`Nmi`→hard reset, `PowerCycle`→stop+start). `Pause`/`Resume`
   remain accepted as Proxmox extras but are intentionally **not advertised** (non-standard).

3. **`ComputerSystem.UpdateConfig` is an OEM action** with no schema backing. Standard
   config changes should be PATCH on the resource. Should live under an `Oem` namespace.

4. **VirtualMedia naming/placement.** The Systems-side path uses device id `CDROM`
   (`/Systems/{id}/VirtualMedia/CDROM/...`); the spec uses `Cd` and historically places
   VirtualMedia under Managers. The Managers-side `Cd` path is compliant; the Systems
   `CDROM` path is a convenience variance.

5. **`Bios/SMBIOS`** is a non-standard sub-resource (SMBIOS data isn't a Redfish Bios
   child in the schema).

6. **Error registry version (deferred).** Errors use `Base.1.0.*` message IDs; the current
   Base registry is `Base.1.23.0` (mirrored locally). The envelope shape is correct; the
   version prefix is dated. Deliberately deferred: ~12 existing tests assert the `Base.1.0.*`
   strings, so a bump is mechanical churn for a cosmetic gain — scheduled with the Phase 5
   schema-validation work when error IDs get validated against the registry.

7. ~~ServiceRoot is not a complete map.~~ **Resolved** — ServiceRoot advertises Systems,
   Chassis, Managers, SessionService, TaskService, AccountService, EventService,
   UpdateService, Registries, JsonSchemas, and `Links.Sessions`.

8. ~~`RedfishVersion` reports `1.0.0`.~~ **Resolved** — now `1.18.0`.

## Protocol conformance (DSP0266)

| Capability | Status | Notes |
|------------|--------|-------|
| JSON over HTTPS, TLS | ✅ | TLS required for authenticated endpoints. |
| HTTP Basic auth | ✅ | |
| Session auth (`X-Auth-Token`) | ✅ | Create + GET/list + DELETE (logout). |
| Redfish error envelope (`error` + `@Message.ExtendedInfo`) | ✅ ⚠️ | Correct shape; `Base.1.0` version prefix is dated (deferred — see below). |
| Status codes (200/201/202/4xx/5xx) | ✅ | 202 + resolvable `Location` for async ops. |
| Async Task lifecycle (`Location` → `GET Task`) | ✅ | TaskService maps Proxmox UPIDs; 202 `Location` resolves to `GET /TaskService/Tasks/{upid}`. |
| `OData-Version: 4.0` header | ✅ | Emitted on all responses. |
| HTTP method handling | ✅ | `OPTIONS` (204 + `Allow`), `HEAD`, and `405 + Allow` for unsupported methods (e.g. PUT). |
| ETag / If-Match concurrency | ✅ | Weak `ETag` on GET 200; `If-Match` honored on PATCH (Systems + SecureBoot) → `412 PreconditionFailed` on a stale tag; `*` always matches. |
| OData query (`$expand`, `$select`, `$filter`) | ❌ | |
| Collection pagination (`Members@odata.nextLink`) | ❌ | Collections small; returned whole. |
| `$metadata` / odata / Registry / JsonSchema discovery | 🟡 | `$metadata` CSDL + `/redfish/v1/odata` served; Registries/JsonSchemas present (empty). |
| Response schema validation against DMTF schemas | 🟡 | Structural conformance harness (`tests/unit/test_conformance.py`) crawls every resource in CI; full CSDL validation via the DMTF Service-Validator is the Phase-5 exit gate. |

## Suitability by use case

- **Metal3 / Ironic / sushy bare-metal provisioning:** the critical path (System
  discovery, `ComputerSystem.Reset`, boot-device override, VirtualMedia insert/eject,
  SecureBoot enroll) is covered. Main risks: clients that poll the async Task `Location`,
  or that leak sessions without DELETE.
- **General Redfish client / CMDB / monitoring:** limited — no Chassis, sensors, events,
  accounts, or schema discovery.

## UEFI support

Firmware mode is fully handled: `GET /Systems/{id}` reports `FirmwareMode` +
`BootSourceOverrideMode` (UEFI when `bios=ovmf`), and `PATCH /Bios {FirmwareMode:UEFI}`
sets `bios=ovmf`. As of Phase 3a, switching to UEFI also **auto-provisions a 4m OVMF
efidisk** (`ensure_efidisk`) when absent — so the VM gets persistent UEFI NVRAM and
SecureBoot has a target. Controlled by `REDFISH_AUTO_EFIDISK` / `REDFISH_EFIDISK_STORAGE`.
A pre-existing 2m efidisk is reported as not-SecureBoot-ready (4m required).

## Path to full parity

The full plan to close every gap (with documented VM-exceptions) is in
[`docs/PARITY-PLAN.md`](docs/PARITY-PLAN.md). Highest-value items for the provisioning
use case:

1. ✅ **UEFI efidisk auto-provision** (Phase 3a).
2. ✅ **Real TaskService** (`GET /TaskService/Tasks/{upid}`) + resolvable 202 `Location`.
3. ✅ **ServiceRoot link completeness** + `RedfishVersion 1.18.0` + UUID.
4. ✅ **Session DELETE** (logout) + GET/list (`do_DELETE`).
5. ✅ **ResetType reconciliation** — advertised == handled.
6. ✅ **`@odata.type` version bump** to current schema versions.
7. **Runtime/test schema validation** against `docs/redfish-reference/schemas/` (Phase 5).
8. **SecureBoot Certificate CRUD** + dynamic varstore build (Phase 4).
9. **Chassis / EventService / AccountService / UpdateService** (Phases 6-9).

A research doc on the DMTF conformance validators (Service/Protocol/Interop/Usecase) and how
they gate each parity phase is at [`docs/research/redfish-validation-tools.md`](docs/research/redfish-validation-tools.md).

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
