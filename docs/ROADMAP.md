# Roadmap

Phases are incremental. Each lists the requirements it satisfies and the invariants it must not
regress. `INV-*` are in [SECURITY.md](SECURITY.md); `REQ-*` in [REQUIREMENTS.md](REQUIREMENTS.md).

## Phase 0 — Docs & reference replica  ✅ (this session)

Deliver the docs tree, threat model with INV-01..20, AS-IS baseline, and the pinned DMTF Redfish
reference mirror.

**Acceptance:** all docs present; INV-* enumerated; DMTF schemas + mockups mirrored with sha256 in
REFERENCES.md; conformance matrix seeded; no daemon source changed.

## Phase 1 — MVP static varstore-swap  (next)

New `hostops.py` (sole shell-out boundary) + `secureboot.py` (Redfish surface). Endpoints:
`GET/PATCH /Systems/{id}/SecureBoot`, `POST .../SecureBoot/Actions/SecureBoot.ResetKeys`,
`GET .../SecureBootDatabases[/{dbid}]`. Profiles map ResetKeysType + SecureBootEnable onto pre-baked
images. Dry-run default; audit log; per-VM lock; post-write verify.

Satisfies: REQ-F-SB-01..08, REQ-F-RF-01/03/04, REQ-N-SEC-01..03, REQ-N-REL-01/02, REQ-N-MNT-01/02.

**Acceptance:**
- Every INV-01..20 has a passing refuse/allow unit test.
- Dry-run produces the exact `dd` argv without writing.
- On a scratch VM, a real apply round-trips and post-write verify passes.
- Wrong-vmid / running-VM / non-4m efitype / oversized image / out-of-allowlist source all refuse.
- No shell usage (static check); black/isort/flake8/mypy green; coverage ≥ current bar.

## Phase 2 — Spec-compliant SecureBoot resource

Full schema-validated responses, ServiceRoot advertisement, Base-registry errors, ETag/If-Match on
mutations, async Task option for apply.

Satisfies: REQ-F-RF-02/05.

**Acceptance:** emitted JSON validates against `redfish-reference/schemas/`; shapes match the DMTF
mockups; conformance matrix shows SecureBoot core properties implemented; ETag/error semantics tested.

## Phase 3 — Dynamic cert build

`POST .../SecureBootDatabases/{db}/Certificates` accepts **public** PEM certs; daemon accumulates
them and builds a varstore from a blank `OVMF_VARS_4M.fd` via `virt-fw-vars`
(`--set-pk/--add-kek/--add-db/--secure-boot/--no-microsoft`), then applies it through the Phase-1
executor (all INV-* still enforced).

Satisfies: REQ-F-SB-09.

**Acceptance:** INV-13 proven by tests rejecting private-key-like input; built varstore validated
and applied; reproducible build with recorded hashes; `virt-fw-vars`-absent handled (501).

## Phase 4 — Full database / certificate CRUD

`Certificate` GET/POST/DELETE (adds `do_DELETE` to the handler), per-database
`SecureBootDatabase.ResetKeys`, dbx handling.

Satisfies: REQ-F-SB-10.

**Acceptance:** CRUD validates against schemas; each mutation traces to a write satisfying INV-*;
conformance gaps closed or explicitly documented.

## Phase 5 (optional) — Hardening

Drop root via a `CAP_DAC_OVERRIDE` write helper; AuditMode/DeployedMode full support; live
varstore re-read OEM action; SSH transport option for off-host deployment.
