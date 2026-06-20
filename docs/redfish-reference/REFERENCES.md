# Redfish Reference Replica — Provenance & Authoritative Sources

This folder is a **pinned local mirror** of the DMTF Redfish standards relevant to this project.
It exists so contributors and AI assistants never guess property names, types, or required
fields — the answer is always in `schemas/` (machine-validatable) or upstream at the URLs below.

**Retrieved:** 2026-06-20. **Mirror is a snapshot — upstream is the source of truth.**

## Authoritative upstream sources

| What | URL |
|------|-----|
| Redfish standard landing | https://www.dmtf.org/standards/redfish |
| JSON Schema index (per-schema, latest) | https://redfish.dmtf.org/schemas/v1/ |
| Message registry index | https://redfish.dmtf.org/registries/ |
| DSP0266 — Redfish Specification (protocol: methods, ETags, errors, async tasks, auth) | https://www.dmtf.org/dsp/DSP0266 |
| DSP2046 — Redfish Resource & Schema Guide (human-readable property reference) | https://www.dmtf.org/dsp/DSP2046 |
| DSP8010 — Redfish Schema Bundle (zip of all JSON Schema + CSDL) | https://www.dmtf.org/dsp/DSP8010 |
| DSP8011 — Redfish Standard Registries | https://www.dmtf.org/dsp/DSP8011 |
| DSP2043 — Redfish Mockup Bundle | https://www.dmtf.org/dsp/DSP2043 |
| Public mockup source (used for `mockups/` here) | https://github.com/DMTF/Redfish-Mockup-Server (`public-rackmount1`) |
| UEFI Specification (SecureBoot variable semantics: PK/KEK/db/dbx, SetupMode/UserMode) | https://uefi.org/specifications |

DSP PDFs are not mirrored (large, versioned) — fetch from the URLs above when needed. The
machine-readable JSON Schema is mirrored in `schemas/` because that is what code/tests validate against.

## Mirrored JSON Schemas (`schemas/`)

Source base: `https://redfish.dmtf.org/schemas/v1/<file>` (retrieved 2026-06-20).

| File | Pinned version | sha256 |
|------|----------------|--------|
| SecureBoot.v1_2_0.json | v1.2.0 | `176531db90f4c07a2d373cb286237e6fb64491bcf21425e4cd96b4aaaf8eb542` |
| SecureBoot.json | unversioned ptr | `b581fd552115f91dde8c3c15560dde8f224556a939d66e12ba8e8da0e19d2356` |
| SecureBootDatabase.v1_0_3.json | v1.0.3 | `8133c7200e2302ab3301faf5fb8387c068d7f1f456a7d793b5234777bdf5b1ec` |
| SecureBootDatabase.json | unversioned ptr | `c21dd794178050db683bb9d1dca1bd2354cb9cd63064d60711673869d90caa1d` |
| SecureBootDatabaseCollection.json | (collection) | `d862817e20a8d6a5cb89e7748b5d940ad1fa64dc08983d298c4dbd42edbb9093` |
| Certificate.v1_11_0.json | v1.11.0 | `7fa29e64e7379f063134bb159a4e7cc74fa965ab7cd2242ffb26ec02a9b65867` |
| Certificate.json | unversioned ptr | `0c87171aedbda6afce14176de88a5d8d614d1ea22d38614970047838e0e627ec` |
| CertificateCollection.json | (collection) | `f54fd6b759dff2744a0270adaa7cc103c6eadd0152adace38d270383b818e0bf` |
| ServiceRoot.v1_21_0.json | v1.21.0 | `fa9221383fa5a05dbe2dfe3b63bb5715be10ec386e249556a6eacde44aee0fa0` |
| ServiceRoot.json | unversioned ptr | `31c0daaa14f866e5f577df7a9dff11b91fffea6dac1df18477eb814248875bae` |
| ComputerSystem.v1_28_0.json | v1.28.0 | `11bd25f2f60daff8e8ee90986c30c1dcdb64f6b77274a78390b45d572d80cc36` |
| ComputerSystem.json | unversioned ptr | `8df03de13e06e4e5b16fd3bfaa806567b364a2f26f63492130023078c83e0c23` |
| ComputerSystemCollection.json | (collection) | `c17c1b287bc2320dfbd3bb3d7ec644ab2726d31f33cbfeac96d6109c3324bc4e` |
| Resource.v1_24_0.json | v1.24.0 | `fa645731255d624b3ddb08a9be870316099c9d346e7d36bcd34cb9122c5e2c93` |
| Resource.json | unversioned ptr | `a600172f7b9090c95efad59e3329cbed5d7140d758ab22645785d16ef243bd1c` |
| Message.v1_3_0.json | v1.3.0 | `292a5459502b2d9e9cc627604ba8bcf559c12a93f1142396172af50e9a4b4dc2` |
| Message.json | unversioned ptr | `8797f4471fcb0ce3799f2caa9afdef87378dcb4c30a807055c46dd8cb70b0dfb` |
| redfish-error.v1_0_2.json | v1.0.2 | `6ba0f876b30d7c118ee0645c72f3cb3df865a139a5eea757dd66cb4e16aebd24` |
| redfish-schema-v1.json | meta-schema | `f5aa8d12ec3ec4e86512ae27eb614beb8c0d2b12455bfd58babc038972ca4502` |
| odata-v4.json | OData defs | `aa4af8059f88aedca1e5b78b45b09cba54f1e26155493a796c3ec59a155c8350` |
| Base.registry.json (from registries/Base.1.23.0.json) | Base 1.23.0 | `72c1d901d7fad9af9d7b8f2070fd5fc09091436654234c9f19123ba4b446e514` |

## Mirrored mockups (`mockups/`)

Source: `DMTF/Redfish-Mockup-Server` `public-rackmount1` system `437XR1138R2` (retrieved 2026-06-20).
Used as golden fixtures for response-shape tests.

| File | sha256 |
|------|--------|
| ServiceRoot.json | `3cdbdf2c7bc87d35b4fe7124be221da3e846f1815ebb963178f039114a55383e` |
| ComputerSystem.json | `af03202a1bcd4f16ee8dab92364fc7b1f78cb3088f74d3b79294e9c22ee2957b` |
| SecureBoot.json | `56932277665f54716e3e4057c5e22804b909be19d424c06ab0e867a01810de89` |
| SecureBootDatabaseCollection.json | `85fbc255df91124da5f2f9f5561f6bfa15cedadb608ab1bbfc0489ca5ae6b707` |
| SecureBootDatabase-db.json | `66ab0c0948dbea4a3f64026b34d3c420aba4e94396c514487eb84290d2137195` |
| CertificateCollection-db.json | `d41c124f71ef7e98f95f6b28b9369bcde7daa7f3b6422716df33b58f9fcd6961` |
| Certificate-PK-1.json | `90910c33b874327c242c00af603943b8fa4686ce58e8f3751a6ae8a8e88a54e3` |

## How to refresh

1. Re-run the index probe: `curl -sS https://redfish.dmtf.org/schemas/v1/ | grep -oE '<Base>\.v[0-9_]+\.json'`
   to find newer versions.
2. Download into `schemas/`, re-compute `shasum -a 256 schemas/*.json`, update the tables above.
3. Re-fetch mockups from `public-rackmount1` if the upstream changed.
4. Re-validate: `for f in schemas/*.json mockups/*.json; do python3 -m json.tool "$f" >/dev/null; done`.
5. Update `docs/spec/conformance-matrix.md` if property sets changed.
