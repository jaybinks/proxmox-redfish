# Redfish source-of-truth map (AI-facing)

**Rule: never invent Redfish property names, enum values, or `@odata.type` strings.**
Cross-check against the pinned schema in `schemas/` first; if a property is missing or looks
stale, fetch the live schema from `https://redfish.dmtf.org/schemas/v1/<Name>.json` and flag a
mirror refresh (see `REFERENCES.md`). Always record the schema version you relied on.

When reasoning about Redfish here:
1. **Property exists / type / required?** → `schemas/<Name>.v<ver>.json` (the `definitions` block).
2. **What does a real response look like?** → `mockups/<Name>.json` (authentic DMTF examples).
3. **Protocol semantics** (HTTP status, ETag/If-Match, async Task, auth, error envelope) → DSP0266 (URL in `REFERENCES.md`).
4. **Error codes / message IDs** → `schemas/Base.registry.json` (the `Base` message registry).
5. **UEFI key semantics** (PK/KEK/db/dbx, SetupMode vs UserMode) → UEFI spec (URL in `REFERENCES.md`).

## Resource → schema → mockup → upstream

| Resource | Local schema | Local mockup | Upstream schema URL |
|----------|--------------|--------------|---------------------|
| ServiceRoot | `schemas/ServiceRoot.v1_21_0.json` | `mockups/ServiceRoot.json` | https://redfish.dmtf.org/schemas/v1/ServiceRoot.json |
| ComputerSystem | `schemas/ComputerSystem.v1_28_0.json` | `mockups/ComputerSystem.json` | https://redfish.dmtf.org/schemas/v1/ComputerSystem.json |
| SecureBoot | `schemas/SecureBoot.v1_2_0.json` | `mockups/SecureBoot.json` | https://redfish.dmtf.org/schemas/v1/SecureBoot.json |
| SecureBootDatabase | `schemas/SecureBootDatabase.v1_0_3.json` | `mockups/SecureBootDatabase-db.json` | https://redfish.dmtf.org/schemas/v1/SecureBootDatabase.json |
| SecureBootDatabaseCollection | `schemas/SecureBootDatabaseCollection.json` | `mockups/SecureBootDatabaseCollection.json` | https://redfish.dmtf.org/schemas/v1/SecureBootDatabaseCollection.json |
| Certificate | `schemas/Certificate.v1_11_0.json` | `mockups/Certificate-PK-1.json` | https://redfish.dmtf.org/schemas/v1/Certificate.json |
| CertificateCollection | `schemas/CertificateCollection.json` | `mockups/CertificateCollection-db.json` | https://redfish.dmtf.org/schemas/v1/CertificateCollection.json |
| Error envelope | `schemas/redfish-error.v1_0_2.json` + `schemas/Base.registry.json` | — | https://redfish.dmtf.org/schemas/v1/redfish-error.json |

## Key facts verified from the mirror (2026-06-20)

- `SecureBoot` props: `SecureBootEnable` (bool, RW), `SecureBootMode` (RO enum:
  `SetupMode`/`UserMode`/`AuditMode`/`DeployedMode`), `SecureBootCurrentBoot` (RO enum:
  `Enabled`/`Disabled`), `SecureBootDatabases` (link), and action `#SecureBoot.ResetKeys`
  with `ResetKeysType` ∈ {`ResetAllKeysToDefault`, `DeleteAllKeys`, `DeletePK`}.
  Mockup `@odata.type` is `#SecureBoot.v1_1_1.SecureBoot`; latest schema is v1.2.0.
- `SecureBootDatabase` also has its own `#SecureBootDatabase.ResetKeys` action (per-database)
  with `ResetKeysType` ∈ {`ResetAllKeysToDefault`, `DeleteAllKeys`}, plus `Certificates` and
  `Signatures` sub-collections. Standard `DatabaseId`s: `PK`, `KEK`, `db`, `dbx` (+ `dbr`,
  `dbt`, and `*Default` factory variants).
- `Certificate` POST body uses `CertificateString` + `CertificateType` (`PEM`|`DER`). GET adds
  `Subject`, `Issuer`, `ValidNotBefore/After`, `KeyUsage`, `UefiSignatureOwner`. Private keys
  are never returned and **must never be accepted by this daemon** (public certs only).
