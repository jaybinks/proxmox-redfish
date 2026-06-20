# Redfish SecureBoot API (as exposed by this daemon)

Spec-compliant surface for Proxmox VMs. `{vmid}` is the Proxmox VM ID (Redfish ComputerSystem Id).
All bodies below are verified against `docs/redfish-reference/schemas/` and the authentic mockups
in `docs/redfish-reference/mockups/`. Auth: existing daemon Basic/Session over TLS.

## Resource tree

```
/redfish/v1/Systems/{vmid}/SecureBoot                         GET, PATCH
/redfish/v1/Systems/{vmid}/SecureBoot/Actions/SecureBoot.ResetKeys   POST
/redfish/v1/Systems/{vmid}/SecureBoot/SecureBootDatabases            GET
/redfish/v1/Systems/{vmid}/SecureBoot/SecureBootDatabases/{dbid}     GET        (dbid ∈ PK|KEK|db|dbx)
/redfish/v1/Systems/{vmid}/SecureBoot/SecureBootDatabases/{dbid}/Certificates  GET, POST(P3), DELETE(P4)
```

## GET /SecureBoot

`@odata.type`: `#SecureBoot.v1_1_1.SecureBoot` (mockup-compatible; v1.2.0 schema is mirrored).

```json
{
  "@odata.id": "/redfish/v1/Systems/3009/SecureBoot",
  "@odata.type": "#SecureBoot.v1_1_1.SecureBoot",
  "Id": "SecureBoot",
  "Name": "UEFI Secure Boot",
  "SecureBootEnable": true,
  "SecureBootMode": "UserMode",
  "SecureBootCurrentBoot": "Enabled",
  "SecureBootDatabases": {
    "@odata.id": "/redfish/v1/Systems/3009/SecureBoot/SecureBootDatabases"
  },
  "Actions": {
    "#SecureBoot.ResetKeys": {
      "target": "/redfish/v1/Systems/3009/SecureBoot/Actions/SecureBoot.ResetKeys",
      "ResetKeysType@Redfish.AllowableValues": ["ResetAllKeysToDefault", "DeleteAllKeys", "DeletePK"]
    }
  },
  "Oem": {
    "Proxmox": {
      "ActiveProfile": "ngv-sb-on",
      "EfiType": "4m",
      "@Redfish.AllowableProfiles": ["ngv-sb-on", "sb-off-blank"]
    }
  }
}
```

- `SecureBootEnable` — bool, RW. PATCH target.
- `SecureBootMode` — RO enum `SetupMode`/`UserMode`/`AuditMode`/`DeployedMode`. Derived: no PK →
  `SetupMode`; PK present + enabled → `UserMode`.
- `SecureBootCurrentBoot` — RO enum `Enabled`/`Disabled`. MVP maps to last-applied state (documented
  approximation; true value requires guest boot telemetry).
- `Oem.Proxmox` — non-standard discovery of available profiles (allowed by Redfish `Oem`).

## PATCH /SecureBoot

Request:
```json
{ "SecureBootEnable": true }
```
Semantics: `true` → apply the configured "enable" profile (custom PK/KEK/db + SB on); `false` →
apply the "blank/setup-mode" profile. Pipeline: resolve profile → `stopped_vm_guard` →
`locate_efidisk` → `write_varstore_image` (all INV-*) → reconcile sidecar. Returns the refreshed
`GET /SecureBoot` body, **200** (the 528 KB `dd` is sub-second; synchronous is simpler for sushy).
On a long op a **202** + `Location: /redfish/v1/TaskService/Tasks/{id}` is permitted.

## POST /SecureBoot/Actions/SecureBoot.ResetKeys

Request:
```json
{ "ResetKeysType": "ResetAllKeysToDefault" }
```
Mapping (configurable in the profile catalog):

| ResetKeysType | Effect | Profile |
|---------------|--------|---------|
| `ResetAllKeysToDefault` | restore the org's default key set (your PK/KEK/db), SB on | `ngv-sb-on` |
| `DeleteAllKeys` | wipe all keys → SetupMode, SB off | `sb-off-blank` |
| `DeletePK` | drop PK → SetupMode (KEK/db retained) | `sb-off-setupmode` |

Returns 200 with a result message (or 202+Task). Invalid value → 400 `PropertyValueNotInList`.

## GET /SecureBoot/SecureBootDatabases

```json
{
  "@odata.id": "/redfish/v1/Systems/3009/SecureBoot/SecureBootDatabases",
  "@odata.type": "#SecureBootDatabaseCollection.SecureBootDatabaseCollection",
  "Name": "UEFI SecureBoot Database Collection",
  "Members@odata.count": 4,
  "Members": [
    {"@odata.id": "/redfish/v1/Systems/3009/SecureBoot/SecureBootDatabases/PK"},
    {"@odata.id": "/redfish/v1/Systems/3009/SecureBoot/SecureBootDatabases/KEK"},
    {"@odata.id": "/redfish/v1/Systems/3009/SecureBoot/SecureBootDatabases/db"},
    {"@odata.id": "/redfish/v1/Systems/3009/SecureBoot/SecureBootDatabases/dbx"}
  ]
}
```

## GET /SecureBoot/SecureBootDatabases/{dbid}

```json
{
  "@odata.id": "/redfish/v1/Systems/3009/SecureBoot/SecureBootDatabases/db",
  "@odata.type": "#SecureBootDatabase.v1_0_2.SecureBootDatabase",
  "Id": "db",
  "Name": "db - Authorized Signature Database",
  "DatabaseId": "db",
  "Certificates": { "@odata.id": ".../SecureBootDatabases/db/Certificates" }
}
```
Phase 4 adds the per-database `#SecureBootDatabase.ResetKeys` action
(`ResetKeysType` ∈ {`ResetAllKeysToDefault`, `DeleteAllKeys`}).

## POST /SecureBoot/SecureBootDatabases/{dbid}/Certificates  (Phase 3)

Request — **public certificate only**:
```json
{
  "CertificateString": "-----BEGIN CERTIFICATE-----\nMIID...==\n-----END CERTIFICATE-----",
  "CertificateType": "PEM"
}
```
Accumulated certs are built into a varstore via `virt-fw-vars`, then applied. Private-key input is
rejected (INV-13). DELETE (Phase 4) removes a cert by Id.
