# proxmox-redfish documentation

A Redfish API daemon for Proxmox VE VMs. This `docs/` set covers the **SecureBoot management**
work — exposing Proxmox VM Secure Boot key enrollment through the standard DMTF Redfish API,
replacing the current manual host-side `dd` of an OVMF varstore image.

## Reading order

1. [REQUIREMENTS.md](REQUIREMENTS.md) — what we must build (functional + non-functional, with IDs).
2. [ARCHITECTURE.md](ARCHITECTURE.md) — how the daemon is structured and where SecureBoot fits.
3. [SECURITY.md](SECURITY.md) — **threat model + the `INV-*` guard rails for the `dd` write. Read before touching host-ops code.**
4. [ROADMAP.md](ROADMAP.md) — phased delivery and acceptance criteria.
5. Specs — [spec/redfish-secureboot-api.md](spec/redfish-secureboot-api.md),
   [spec/error-model.md](spec/error-model.md), [spec/conformance-matrix.md](spec/conformance-matrix.md).
6. Design — [design/secureboot-dd-write.md](design/secureboot-dd-write.md),
   [design/ovmf-varstore.md](design/ovmf-varstore.md).
7. Operations — [operations/baseline-manual-workflow.md](operations/baseline-manual-workflow.md) (AS-IS),
   [operations/to-be-redfish-flow.md](operations/to-be-redfish-flow.md) (TO-BE).
8. [bugs/BUGLOG.md](bugs/BUGLOG.md) — bug tracking. [decisions/](decisions/) — ADRs.

## Standards source of truth

`redfish-reference/` is a **pinned local mirror** of the DMTF Redfish JSON Schemas, message
registry, and authentic mockups. See
[redfish-reference/CLAUDE.md](redfish-reference/CLAUDE.md) for the resource→schema→mockup map and
[redfish-reference/REFERENCES.md](redfish-reference/REFERENCES.md) for upstream URLs + provenance.
**Never invent Redfish property names — check the mirror.**

## Status

Docs + reference replica: **done**. Phase 1 MVP (static varstore-swap): **implemented** —
`src/proxmox_redfish/hostops.py` + `secureboot.py`, wired into the daemon, 76 unit tests, 91%
coverage on the new modules. Writes are dry-run by default. See
[operations/install-and-test.md](operations/install-and-test.md) to install and validate on
Proxmox. Next: Phase 2 (schema-validated responses, ETag) — see ROADMAP.
