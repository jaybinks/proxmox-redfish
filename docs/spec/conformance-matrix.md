# Conformance matrix

DMTF schema property vs. this daemon's implementation. Status: ✅ implemented · 🟡 partial ·
⬜ not yet · ➖ N/A for a varstore-swap backend. Schema versions are the pinned mirror
(`docs/redfish-reference/schemas/`).

## SecureBoot (`SecureBoot.v1_2_0`, mockup type `v1_1_1`)

| Property | Type | Access | Status | Notes |
|----------|------|--------|--------|-------|
| `@odata.id` / `@odata.type` / `Id` / `Name` | — | RO | ✅ | type `#SecureBoot.v1_1_1.SecureBoot`. |
| `SecureBootEnable` | bool | RW | ✅ | PATCH applies a profile. |
| `SecureBootMode` | enum | RO | ✅ | Derived from key presence (no PK → SetupMode). |
| `SecureBootCurrentBoot` | enum | RO | 🟡 | MVP approximates from last-applied state. |
| `SecureBootDesiredMode` (v1.2.0) | enum | RW | ⬜→P2 | |
| `SecureBootDatabases` | link | RO | ✅ | |
| `Actions #SecureBoot.ResetKeys` | action | — | ✅ | 3 ResetKeysType values → profiles. |

## SecureBootDatabase (`SecureBootDatabase.v1_0_3`, mockup type `v1_0_2`)

| Property | Status | Notes |
|----------|--------|-------|
| `Id` / `Name` / `DatabaseId` | ✅ | PK/KEK/db/dbx. |
| `Certificates` link | ✅ | collection link (GET of certs is P3). |
| `Signatures` link | ⬜→P4 | hash-based signatures (dbx). |
| `Actions #SecureBootDatabase.ResetKeys` | ⬜→P4 | per-database reset. |

## Certificate (`Certificate.v1_11_0`, mockup type `v1_8_1`)

| Property | Status | Notes |
|----------|--------|-------|
| `CertificateString` / `CertificateType` | ⬜→P3 | POST input; PEM. |
| `Subject` / `Issuer` / `ValidNotBefore` / `ValidNotAfter` / `KeyUsage` / `UefiSignatureOwner` | ⬜→P3 | parsed for GET. |
| private key fields | ➖ | never accepted or emitted (INV-13). |

## Protocol (DSP0266)

| Capability | Status | Notes |
|------------|--------|-------|
| Error envelope + Base registry IDs | ⬜→P1 | see error-model.md. |
| Schema validation of responses | ⬜→P2 | validate against mirrored schemas in tests. |
| ETag / If-Match on mutations | ⬜→P2 | |
| Async Task for apply | ⬜→P2 | optional; MVP is synchronous. |
| ServiceRoot advertises SecureBoot via ComputerSystem link | ⬜→P1 | add link in `get_vm_status`. |

Update this file whenever a property's status changes or the mirror is refreshed.
