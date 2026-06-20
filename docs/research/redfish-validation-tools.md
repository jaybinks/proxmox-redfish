# DMTF Redfish Validation Tooling — Research & Strategy

Research into the DMTF Redfish conformance/validation ecosystem and how to apply it to
`proxmox-redfish` (a Python daemon exposing a Redfish API over Proxmox VMs, listening on
`https://<host>:8443` with HTTP Basic auth, aiming for full DMTF spec parity).

Companion docs: [`../../RedFishSpecCompliance.md`](../../RedFishSpecCompliance.md) (current
coverage + variances), [`../PARITY-PLAN.md`](../PARITY-PLAN.md) (phased plan),
[`../redfish-reference/REFERENCES.md`](../redfish-reference/REFERENCES.md) (pinned schema mirror).

> Accuracy note: flag names, repo names, profile filenames, and report filenames below were
> taken from the upstream DMTF/OpenStack/OCP repos (see **Sources**). Where a flag or behaviour
> could not be confirmed against an authoritative source, it is explicitly marked
> *(unverified)*. Validators evolve their CLI between releases — run `--help` against the
> version you install to confirm exact spelling (`--report-dir` vs `--logdir`, `-r` vs `-i`,
> etc.).

---

## Overview — the four-layer validation model

DMTF ships four complementary validators. They test **different layers**; passing one says
nothing about the others. Think of them as a stack:

| Layer | Tool | What it guarantees | Question it answers |
|-------|------|--------------------|---------------------|
| 1. Schema / payload | **Redfish-Service-Validator** | Every resource body conforms to its CSDL/JSON Schema: property names, types, required props, enum values, `@odata.type` resolves to a real schema version, links resolve. | "Is each JSON document well-formed per the schema bundle (DSP8010)?" |
| 2. Protocol / transport | **Redfish-Protocol-Validator** | HTTP behaviour per DSP0266: status codes, `Allow`/`OData-Version`/`ETag`/`Link` headers, Basic vs Session auth, conditional requests, query-param handling, redirects. | "Does the service speak HTTP/Redfish correctly at the wire level?" |
| 3. Interoperability | **Redfish-Interop-Validator** | The service satisfies a **profile** — a JSON contract that says "for use case X you MUST expose these resources/properties/actions with these values". | "Does this service meet the requirements a *specific consumer* (e.g. Ironic) needs?" |
| 4. Use-case / workflow | **Redfish-Usecase-Checkers** | End-to-end multi-step operations actually work: power on/off, boot override + reboot, account add/delete, etc. | "Can a client *do the job* (drive provisioning), not just read pretty JSON?" |

Supporting cast: **Redfish-Mockup-Server** / **Redfish-Mockup-Creator** (capture/serve static
trees), **Redfishtool** and **python-redfish-library** (`redfish` on PyPI) for manual probing,
and the **DSP8010 schema bundle** that layers 1 and 3 consume.

Key implication for us: **schema-green (layer 1) is necessary but not sufficient** for the
provisioning goal. Ironic/Metal3 success is really a layer-3 (interop profile) + layer-4
(use-case) property. The PARITY-PLAN already implicitly orders these — this doc maps each
validator onto the phases.

---

## Tool-by-tool reference

All four core validators share a near-identical auth surface (`-r/--rhost` URL with scheme,
`-u/--user`, `-p/--password`, an auth-type selector, and a `--no-cert-check`/SSL flag).
`proxmox-redfish` uses **HTTP Basic over TLS with a self-signed cert on :8443**, so for every
tool you will pass roughly:

```
-r https://localhost:8443 -u USER -p PASS   # plus the tool's "Basic auth" + "skip cert check" flags
```

### 1. Redfish-Service-Validator (schema / CSDL conformance) — layer 1

- **Repo:** github.com/DMTF/Redfish-Service-Validator · **PyPI:** `redfish_service_validator`
- **What it does:** crawls the service starting at `/redfish/v1`, following links (`@odata.id`),
  and validates every payload against the Redfish schema. It reads each resource's
  `@odata.type`, resolves the matching CSDL/JSON Schema, and checks property presence, types,
  required/`Mandatory` fields, enum membership, read-only violations, action definitions, and
  link resolvability.
- **How it gets schemas:** by default it downloads the current **DSP8010** schema bundle from
  the DMTF publication site; it can also read the service's own `$metadata` to discover
  versions and OEM schema locations. You can force **offline** validation against a local copy
  with `--schema_directory <dir>` (default dir name `SchemaFiles`). *This matters for us: we
  don't serve `$metadata`, so point it at a local schema dir.* Our pinned mirror lives at
  [`../redfish-reference/schemas/`](../redfish-reference/schemas/) (per-schema JSON), though the
  validator generally wants the **full DSP8010 unzipped bundle**, not a hand-picked subset — see
  Quick-start.
- **Install:**
  ```bash
  pip install redfish_service_validator
  # or: git clone … && cd Redfish-Service-Validator && python setup.py sdist && pip install dist/*.tar.gz
  ```
- **Run against this daemon:**
  ```bash
  python -m redfish_service_validator.RedfishServiceValidator \
    -r https://localhost:8443 -u USER -p PASS \
    --authtype Basic \
    --payload Tree /redfish/v1 \
    --nooemcheck \
    --logdir ./validator-logs
  ```
  (Console entry point name varies by version — `rf_service_validator` / `RedfishServiceValidator`;
  `python -m redfish_service_validator.RedfishServiceValidator` is the safe form.)
- **Notable flags:** `--authtype {Basic,Session}`; `--payload {Single|Tree} <URI>` (single
  resource vs full crawl); `--schema_directory <dir>` (offline schemas); `--nooemcheck` (skip
  OEM-namespace validation — relevant to our `ComputerSystem.UpdateConfig`/`Bios/SMBIOS`
  extensions); `--collectionlimit <Type N>` (cap members tested per collection — useful so a
  big Systems collection doesn't take forever); `--mockup <dir>`; `--timeout`; `--debugging`.
  TLS-skip flag spelling is version-dependent *(confirm with `--help`; commonly there is a
  no-cert-check option)*.
- **Output:** an HTML report plus logs in the report dir (default `logs/`), summarising
  pass/fail/warn counts and a per-resource breakdown of every property checked, with the failing
  property path and reason. Exit code is non-zero on failures (CI-gateable).

### 2. Redfish-Protocol-Validator (DSP0266 HTTP behaviour) — layer 2

- **Repo:** github.com/DMTF/Redfish-Protocol-Validator · **PyPI:** `redfish_protocol_validator`
- **What it asserts (per DSP0266):** HTTP-level conformance, including —
  - **Headers:** `OData-Version: 4.0` present; `Allow` on responses and especially on `405`
    responses listing supported methods; `ETag` on resources that support concurrency; `Link`
    header pointing at the JSON Schema; `Cache-Control`; `Content-Type` correctness;
    `WWW-Authenticate` on `401`.
  - **Auth:** HTTP Basic *and* Redfish Session login (`POST /SessionService/Sessions` →
    `X-Auth-Token`), session logout (`DELETE`), unauthenticated access to the service root,
    rejection of bad credentials.
  - **Status codes:** `405 Method Not Allowed` for unsupported methods (with `Allow`),
    `501 Not Implemented`, `4xx` error-envelope shape, `200/201/202/204` on the right verbs,
    conditional-request codes (`304`, `412 Precondition Failed`) for `If-Match`/`If-None-Match`.
  - **Query params:** behaviour of `$top`/`$skip`/`$filter`/`$expand`/`$select` and correct
    rejection/ignoring of unsupported ones.
  - **Service details:** `/redfish` redirect, `/redfish/v1/odata`, `$metadata` document,
    `Registries`, protocol-version semantics.
- **Install:**
  ```bash
  pip install redfish_protocol_validator
  ```
- **Run against this daemon:**
  ```bash
  rf_protocol_validator \
    -r https://localhost:8443 -u USER -p PASS \
    --no-cert-check \
    --report-dir ./protocol-reports --report-type both
  ```
- **Notable flags:** `--report-dir`; `--report-type {html,tsv,both}`; `--log-level
  {DEBUG,INFO,WARNING,ERROR,CRITICAL}`; `--avoid-http-redirect`; `--no-cert-check`;
  `--ca-bundle <file>`. *(This tool is the most likely to actively mutate/probe — it will
  attempt logins, conditional PATCHes, and bad requests. Run it against a disposable test
  VM/instance.)*
- **Output:** HTML and/or TSV report in `reports/` with a pass/fail line per assertion, grouped
  by DSP0266 requirement.

### 3. Redfish-Interop-Validator (interoperability profiles) — layer 3

- **Repo:** github.com/DMTF/Redfish-Interop-Validator · **PyPI:** `redfish_interop_validator`
- **What it does:** takes a **profile** (a JSON document per **DSP0272**, the Redfish
  Interoperability Profiles spec) that declares, per resource type, which resources/properties/
  actions are `Mandatory`/`Recommended`, minimum versions, and required enum values — then
  crawls the live service and reports pass/fail/skip/warn against that contract. A profile is
  the machine-readable answer to "what does *consumer X* actually require?".
- **Where profiles come from:** the DMTF **Redfish-Interop-Profiles** repo (standard profiles),
  the **opencomputeproject/HWMgmt-OCP-Profiles** repo (OCP profiles), or a vendor/consumer repo
  (e.g. OpenStack Ironic ships its own — see *Interop profiles for our use case*).
- **Install:**
  ```bash
  pip install redfish_interop_validator
  ```
- **Run against this daemon:**
  ```bash
  python -m redfish_interop_validator.RedfishInteropValidator \
    OpenStackIronicProfile \
    -r https://localhost:8443 -u USER -p PASS \
    --authtype Basic \
    --nooemcheck \
    --logdir ./interop-logs
  ```
  The first positional arg is the **profile name** (the tool can auto-download known profiles
  unless `--no_online_profiles` is set; otherwise point it at a local profile dir).
- **Notable flags:** positional `profile`; `--schema <file>` (validate the profile itself);
  `--no_online_profiles` (don't fetch profiles from the network — pair with a local profile
  dir); `--required_profiles_dir <dir>`; `--nooemcheck`; `--payload {Single|SingleFile|Tree|
  TreeFile} <res/file>`; `--collectionlimit`; `--logdir`.
- **Output:** `InteropHtmlLog_MM_DD_YYYY_HHMMSS.html` (+ text log) in `logs/`, with
  Pass/Fail/Skip/Warning per required property/resource.

### 4. Redfish-Usecase-Checkers (workflow checks) — layer 4

- **Repo:** github.com/DMTF/Redfish-Usecase-Checkers · **PyPI:** `redfish_use_case_checkers`
- **What it does:** drives real multi-step operations through the service and asserts the
  observable outcome. The bundled checkers (test groups) are:
  - **PowerControl** — `ComputerSystem.Reset` (power on/off/restart) and verify `PowerState`
    transitions. → **directly exercises our `ComputerSystem.Reset`.**
  - **BootOverride** — set `Boot.BootSourceOverrideTarget` + (one-time) override, reboot, verify.
    → **exercises our boot-device PATCH path.**
  - **AccountManagement** — create/modify/delete an account via AccountService. → we don't
    implement AccountService (Phase 8), so this is expected to skip/fail today.
  - **ManagerEthernetInterfaces** — read/validate Manager NICs. → partial (minimal Manager).
  - **QueryParameters** — exercises OData query params. → we don't implement these (Phase 9).
  - *(Note: this suite does not include a dedicated standalone "virtual media" or "one-time
    boot" checker by those exact names in the current release; one-time boot is covered inside
    **BootOverride**. Virtual-media workflow coverage is thinner here — interop profiles +
    use-case scripts in Redfish-Usecase-Checkers/scripts may cover insert/eject; confirm against
    the installed version. Treat VirtualMedia end-to-end as something we may need a hand-written
    check or `redfishtool` script for.)*
- **Install:**
  ```bash
  pip install redfish_use_case_checkers
  ```
- **Run against this daemon:**
  ```bash
  rf_use_case_checkers \
    -r https://localhost:8443 -u USER -p PASS \
    --report-dir ./usecase-reports \
    --test-list PowerControl BootOverride
  ```
- **Notable flags:** `-r/--rhost`, `-u/--user`, `-p/--password`; `--report-dir`;
  `--test-list <names…>` (run a subset — start with `PowerControl BootOverride`);
  `--relaxed` (downgrade some failures to warnings); `--debugging`. SSL-skip flag spelling
  *(confirm with `--help`)*.
- **Output:** report files in `reports/` (per-checker pass/fail + step log).

### Supporting tools

- **Redfish-Mockup-Server** (github.com/DMTF/Redfish-Mockup-Server): serves a static Redfish
  tree from a directory. `python redfishMockupServer.py -D <mockup-dir> [-H host] [-p port]
  [-s --cert … --key …]`. Useful to (a) run validators in CI **without** a live Proxmox by
  serving a captured tree, and (b) compare our responses to the canonical `public-rackmount1`
  mockup (already mirrored in [`../redfish-reference/mockups/`](../redfish-reference/mockups/)).
- **Redfish-Mockup-Creator** (same org): captures a live service into a mockup directory
  (`-r/-u/-p` to point at the service). Run it against our daemon to snapshot a golden tree for
  regression fixtures and offline validator runs.
- **Redfishtool** (github.com/DMTF/Redfishtool, PyPI `redfishtool`): CLI client that handles
  Redfish hypermedia/auth — handy for scripted use-case checks (virtual media insert/eject,
  one-time boot) the bundled checkers don't cover. `redfishtool -r host:8443 -u USER -p PASS
  -S Always raw GET /redfish/v1` etc.
- **python-redfish-library** (github.com/DMTF/python-redfish-library, PyPI **`redfish`**):
  the Python client (GET/POST/PATCH/DELETE, Basic + Session). It is a **dependency of the
  use-case checkers** and of `redfish_utilities`. Note: **sushy** (the OpenStack client used by
  Ironic/Metal3) is a *separate* library, not DMTF's — but our real consumer is sushy, so
  passing the Ironic interop profile (below) is the most representative test.
- **Redfish-Terminal-UI / Redfish Tableau-style browsers:** a TUI for eyeballing a tree; not a
  validator, low priority for CI. *(Not load-bearing for conformance.)*
- **DSP8010 schema bundle:** the zip of all JSON Schema + CSDL that the Service- and Interop-
  validators consume. For offline/pinned CI, download the DSP8010 zip, unzip it, and pass the
  directory via `--schema_directory`. Our [`REFERENCES.md`](../redfish-reference/REFERENCES.md)
  already pins individual schemas; for validator runs prefer the **full unzipped DSP8010
  bundle** (the validator resolves `$ref`s across the whole set).

---

## What each tool would catch in our current implementation

Cross-referenced against the variances enumerated in `RedFishSpecCompliance.md`.

### Redfish-Service-Validator (schema) would flag

- **Stale `@odata.type` versions** — historically our biggest schema risk. If types are still
  emitted as `v1_0_0` for ComputerSystem/Manager/Bios/Processor/Storage/etc., the validator
  resolves them against the *old* schema (often tolerated) but flags mismatches where a
  property we emit doesn't exist in the claimed old version, or where the claimed version can't
  be resolved. **Per the latest spec-compliance notes these have been bumped to current
  versions (ComputerSystem v1.28.0, ServiceRoot v1.21.0, etc.)** — once correct, this class of
  finding disappears. SecureBoot is already `v1_1_1`/v1.2.0-aligned.
- **`ComputerSystem.Reset` ResetType set** — the validator checks
  `ResetType@Redfish.AllowableValues` against the schema's `ResetType` enum. Non-standard values
  **`Pause`/`Resume`** (we handle them) are not in the schema enum → flagged; `Nmi`/`PowerCycle`
  are valid enum values but advertised-without-working is a layer-4 (use-case) problem, not
  layer-1. (The compliance doc notes ResetType is now reconciled to the standard set — verify.)
- **Non-standard OEM resources:** `ComputerSystem.UpdateConfig` action and `Bios/SMBIOS`
  sub-resource have no schema backing → flagged **unless** moved under an `Oem` namespace or run
  with `--nooemcheck`. The clean fix is the `Oem` move (PARITY Phase 2).
- **`Base.1.0.*` error-registry prefix:** the validator checks `@Message.ExtendedInfo[*].MessageId`
  resolves to a known registry version. `Base.1.0.*` is dated vs the mirrored `Base.1.23.0`; the
  envelope shape is correct but the version prefix may warn. (Compliance doc flags this for the
  Phase 2 bump.)
- **No `$metadata` / JsonSchemas / Registries:** the validator wants to read `$metadata` to
  discover versions/OEM schema URIs. Without it, run **offline** (`--schema_directory`) — but the
  *absence itself* is reported, and any OEM type with no resolvable `$metadata` schema location
  fails OEM validation.
- **ServiceRoot incompleteness:** the validator crawls from the root; resources not linked from
  ServiceRoot (Managers, SessionService, SecureBoot, TaskService) **won't be visited at all** in
  a `--payload Tree /redfish/v1` run, so they're silently untested rather than failed. This is
  why ServiceRoot link completeness (Phase 2) is a *prerequisite* for meaningful crawl coverage —
  otherwise the validator passes by skipping everything.
- **Missing `Memory` handler** while the System body links it → broken link / unresolvable
  `@odata.id` finding.

### Redfish-Protocol-Validator (HTTP) would flag

- **No ETag / If-Match:** missing `@odata.etag` on resources and no `412 Precondition Failed`
  handling on conditional PATCH → multiple assertion failures. (Phase 5 item.)
- **Session lifecycle gaps:** session **DELETE (logout)** and session **GET/list** are missing →
  the auth/session assertions fail. (Phase 2 item.)
- **Status-code / `Allow` header gaps:** if unsupported methods don't return `405` with an
  `Allow` header listing supported verbs → flagged. `501` handling likewise.
- **Header gaps:** missing/incorrect `OData-Version`, `Link` (schema), `Cache-Control`,
  `WWW-Authenticate` on `401`.
- **`$metadata` / `/redfish/v1/odata` absence:** protocol-level service-detail assertions fail.
- **Async/202:** we return `202` + `Location` but (pre-TaskService) the Task didn't resolve;
  protocol validator may flag the `Location` target. **TaskService is now implemented**, so the
  `Location` → `GET Task` path should now satisfy this (verify the Task body validates too).
- **Query params:** unsupported `$expand`/`$select`/`$filter` — depending on whether we reject or
  ignore them, may warn. (Phase 9.)

### Redfish-Interop-Validator (profile) would flag

Against a provisioning profile (Ironic, below), it requires whole resource trees we only
partially expose: full **Chassis + Power + Thermal**, **TaskService** (now present),
**UpdateService**, **SessionService** (as a GET resource, not just POST), **Storage/Volume**
sub-trees, **EthernetInterface** completeness. Expect **many "Mandatory resource missing /
property missing" fails** until Phases 5–6 land. This validator is the truest measure of
"can Ironic actually use this" and will be red for a while — that's expected and useful as a
burndown list.

### Redfish-Usecase-Checkers (workflow) would flag

- **PowerControl:** should largely pass (our `Reset` works) — but will catch any
  advertised-but-unhandled ResetType (e.g. `Nmi`/`PowerCycle` returning 400) as a workflow
  failure, the exact mirror of variance #2.
- **BootOverride:** should pass for supported targets; catches one-time-boot semantics and
  whether the override actually takes effect on next boot.
- **AccountManagement:** fails/skips — no AccountService (Phase 8).
- **QueryParameters / ManagerEthernetInterfaces:** skip/partial — minimal Manager, no OData query.

---

## Interop profiles for our use case

**Our real consumers are sushy / OpenStack Ironic / Metal3 / OpenShift ZTP**, not a generic
enterprise BMC. The most representative interop contract is therefore the **OpenStack Ironic
Redfish profile**, which Metal3 and OpenShift baremetal effectively inherit (Metal3 drives
Ironic; sushy is Ironic's Redfish client).

- **Is there a Metal3 / OpenShift-specific Redfish interop profile?** Not as a separately
  published JSON, per current research. **Metal3 and OpenShift do not publish their own Redfish
  interop profile** — they rely on Ironic + sushy. So the **closest authoritative profile is
  Ironic's**.
- **Ironic profile (recommended target):**
  - File: **`OpenStackIronicProfile.v1_2_0.json`** (also a `v1_1_0`), ProfileName
    `OpenStackIronicProfile`, ProfileVersion `1.2.0`.
  - Location: the **`redfish-interop-profiles/`** folder at the root of the OpenStack Ironic
    repo (`github.com/openstack/ironic`). Confirmed present via the GitHub API.
  - Required resources (top-level `Resources` keys, confirmed):
    `Bios, Chassis, ComputerSystem, ComputerSystemCollection, Drive, EthernetInterface, Manager,
    Power, Processor, SecureBoot, ServiceRoot, SessionService, SimpleStorage, Storage,
    StorageController, StorageControllerCollection, TaskService, Thermal, UpdateService,
    VirtualMedia, Volume, VolumeCollection`.
  - **This is an excellent fit:** it requires exactly our critical path (ComputerSystem,
    SecureBoot, VirtualMedia, TaskService, Manager, SessionService) **plus** the Chassis/Power/
    Thermal/UpdateService/Storage breadth that maps onto PARITY Phases 5–6/9. Running it now
    yields a precise, prioritised burndown of what Ironic-conformance still needs.
  - **How to obtain & run:**
    ```bash
    # fetch the profile into a local dir
    mkdir -p profiles && \
      gh api repos/openstack/ironic/contents/redfish-interop-profiles/OpenStackIronicProfile.v1_2_0.json \
        --jq .content | base64 -d > profiles/OpenStackIronicProfile.v1_2_0.json

    python -m redfish_interop_validator.RedfishInteropValidator \
      OpenStackIronicProfile \
      -r https://localhost:8443 -u USER -p PASS \
      --authtype Basic --nooemcheck \
      --no_online_profiles --required_profiles_dir ./profiles \
      --logdir ./interop-logs
    ```
- **Standard fallback profiles** (if you want a graded ladder rather than the full Ironic bar):
  - **DMTF Redfish-Interop-Profiles** repo — generic baseline profiles.
  - **OCP profiles** (`opencomputeproject/HWMgmt-OCP-Profiles`):
    `OCPBaselineHardwareManagement.v1_1_1.json` (lightest, good first target),
    `OCPServerHardwareManagement.v1_0_0.json`, `OCPRackManagerController`, `OCPServiceBaseline`,
    `OCP_NIC`. *(Note: there is no profile literally named `OCPManagedDevice` in that repo — the
    baseline/server-management profiles are the right OCP equivalents.)*
  - **Recommendation:** gate CI on a **hand-written minimal subset profile** first (just the
    resources we claim to support, marked Mandatory), then track the **full
    `OpenStackIronicProfile.v1_2_0`** as the *aspirational* exit bar for provisioning parity.
    The Ironic profile run will be red until Chassis/Thermal/UpdateService land — keep it as a
    non-gating "burndown" report and graduate resources into the gating minimal profile as they
    pass.

---

## Recommended validation strategy + CI integration

### Which validators, in what order

Run cheapest/most-fundamental first; stop gating on the layers we haven't built yet (track them
as non-gating reports):

1. **Service-Validator** (`--payload Tree /redfish/v1`, offline schemas) — **gating.** This is
   the daily driver and the cheapest signal. Requires ServiceRoot to link everything (Phase 2)
   to actually crawl the tree.
2. **Protocol-Validator** — **gating on a curated assertion subset** (auth, status codes,
   headers we claim to support); the rest (ETag, query params) tracked as non-gating until those
   features land.
3. **Interop-Validator** with a **hand-written minimal profile** — **gating**; the full
   `OpenStackIronicProfile` — **non-gating burndown**.
4. **Usecase-Checkers** `--test-list PowerControl BootOverride` — **gating** (these map to
   shipped features); `AccountManagement`/`QueryParameters` — non-gating until Phases 8/9.

### Standing up the target for a run (local + CI)

The validators need a *live* service. Two modes:

- **Mock mode (fast, deterministic, CI default):** start the daemon against a **mocked Proxmox**
  (the repo already mocks `proxmoxer`/`hostops` in unit tests). Stand up the daemon with that
  mock backend on `:8443`, run validators against it. No real Proxmox needed. Alternatively,
  capture a tree once with **Redfish-Mockup-Creator** and serve it with **Redfish-Mockup-Server**
  for the *read-only* Service-/Interop-validator passes (won't cover mutating workflows).
- **Integration mode (nightly / pre-release):** a real or VM-nested Proxmox with a disposable
  test VM, so Protocol- and Usecase-validators can actually power-cycle and mutate. Gate these
  on a label/nightly schedule, not every PR (slow, stateful).

### GitHub Actions sketch

```yaml
# .github/workflows/redfish-conformance.yml
jobs:
  conformance:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -e . redfish_service_validator redfish_protocol_validator \
                          redfish_interop_validator redfish_use_case_checkers
      # download + unzip DSP8010 once, cache it
      - name: Fetch DSP8010 schema bundle
        run: curl -sSL -o dsp8010.zip https://www.dmtf.org/dsp/DSP8010 && unzip -q dsp8010.zip -d SchemaFiles
      - name: Start daemon (mock Proxmox backend) on :8443
        run: ./scripts/run-mock-daemon.sh &   # backgrounded; wait-for :8443
      - name: Service-Validator (gating)
        run: |
          python -m redfish_service_validator.RedfishServiceValidator \
            -r https://localhost:8443 -u "$RF_USER" -p "$RF_PASS" --authtype Basic \
            --payload Tree /redfish/v1 --schema_directory SchemaFiles \
            --nooemcheck --logdir reports/service
      - name: Protocol-Validator (gating subset)
        run: rf_protocol_validator -r https://localhost:8443 -u "$RF_USER" -p "$RF_PASS" \
               --no-cert-check --report-dir reports/protocol --report-type both
      - name: Interop (minimal profile, gating)
        run: python -m redfish_interop_validator.RedfishInteropValidator MinimalProxmoxProfile \
               -r https://localhost:8443 -u "$RF_USER" -p "$RF_PASS" --authtype Basic \
               --no_online_profiles --required_profiles_dir profiles --logdir reports/interop
      - name: Usecase (power/boot)
        run: rf_use_case_checkers -r https://localhost:8443 -u "$RF_USER" -p "$RF_PASS" \
               --test-list PowerControl BootOverride --report-dir reports/usecase
      - uses: actions/upload-artifact@v4
        if: always()
        with: { name: redfish-conformance-reports, path: reports/ }
```

### Gating policy

- **Block merge on:** Service-Validator schema errors on *implemented* resources; Protocol
  assertions in the curated subset; minimal-profile interop fails; PowerControl/BootOverride
  use-case fails.
- **Tracked-variance allowlist:** maintain a small allowlist (a JSON/YAML keyed by
  resource+property+rule) for *known, documented* variances from `RedFishSpecCompliance.md`, so
  the gate fails only on **new** regressions. A wrapper script post-processes each validator's
  report, subtracts allowlisted findings, and sets exit code. This keeps the build green while
  the variance is openly tracked, and forces new variances to be explicitly added (review
  pressure).
- **Don't gate on** the full Ironic interop profile, ETag/query-param protocol assertions, or
  Account/Query use-cases until their phases land — keep them as uploaded artifacts.

### Snapshotting reports

Upload all `reports/` as a build artifact (always, even on failure). Optionally commit a
trimmed summary (counts per validator) into `docs/spec/` per release tag so conformance
progress is diffable over time — pairs naturally with the existing
`docs/spec/conformance-matrix.md`.

---

## Roadmap recommendation — map validators to PARITY-PLAN phases

Each phase should gain a **machine-checkable exit criterion** = "validator X green on subset Y".
Proposed additions to `PARITY-PLAN.md` (described here; not edited there):

| PARITY Phase | Validator = exit criterion | Concrete acceptance to add |
|--------------|----------------------------|----------------------------|
| **Phase 2 — Spec hygiene** | **Service-Validator** crawls the *whole* tree (ServiceRoot links complete) with **0 schema errors on implemented resources**; **Protocol-Validator** passes the **Session create+DELETE** and **`@odata.type` resolves** assertions. | "ServiceRoot links every implemented resource; `--payload Tree /redfish/v1` visits Managers/SessionService/SecureBoot/TaskService; Service-Validator 0 errors after `Oem`-namespacing UpdateConfig/SMBIOS and bumping `Base.1.x`; session DELETE passes Protocol-Validator." |
| **Phase 3 — UEFI + async** | **Usecase-Checkers `PowerControl`/`BootOverride`** green; the **202 `Location` → `GET Task`** Task body **validates** under Service-Validator and satisfies Protocol-Validator's async assertion. | "TaskService Task bodies schema-valid; PowerControl/BootOverride pass; advertised ResetType == handled (no use-case 400s)." |
| **Phase 4 — SecureBoot completion** | **Service-Validator** green on `SecureBoot`/`SecureBootDatabase`/`Certificate` trees (Certificate v1.11.0). | "POSTed-cert varstore path produces schema-valid Certificate resources; private-key input rejected (test, not validator)." |
| **Phase 5 — Compliance proof + VirtualMedia + ETag** | **Service-Validator green tree-wide** (this *is* the phase's machine proof); **Protocol-Validator ETag/If-Match + `$metadata`** assertions pass; VirtualMedia under `Cd`. | "All emitted bodies validate in CI against DSP8010; `$metadata`/Registries served; ETag/If-Match honoured → Protocol-Validator concurrency assertions green." |
| **Phase 6 — Chassis** | **Interop-Validator** advances on the **Ironic profile** Chassis/Power/Thermal requirements. | "OpenStackIronicProfile Chassis/Power/Thermal requirements move from FAIL→PASS/SKIP-with-justification." |
| **Phase 7 — EventService** | **Interop-Validator** EventService requirements; (no dedicated DMTF event use-case checker — add a `redfishtool`/`redfish`-scripted subscribe+receive check). | "EventService present per Ironic profile; scripted subscribe→event-received check passes." |
| **Phase 8 — AccountService** | **Usecase-Checkers `AccountManagement`** green (or intentionally skipped with documented VM-exception). | "AccountManagement checker passes against read-mostly mapping, or is allowlisted as a VM-exception." |
| **Phase 9 — Remaining + OData query** | **Usecase-Checkers `QueryParameters`** + **Protocol-Validator** query-param assertions green. | "QueryParameters checker + Protocol query assertions pass; `$expand`/pagination validated." |

**Definition-of-full-parity update to propose:** the plan already names "the DMTF Redfish
Service Validator" as the proof. Strengthen it to the **four-layer** bar: *full parity = (1)
Service-Validator 0-error tree-wide, (2) Protocol-Validator green minus documented variances,
(3) `OpenStackIronicProfile.v1_2_0` Interop-Validator green minus documented VM-exceptions, (4)
Usecase-Checkers PowerControl/BootOverride/AccountManagement green* — with the variance/exception
allowlist tracked in `RedFishSpecCompliance.md`.

Also propose adding a **"Validation tooling" section to PARITY-PLAN.md** pointing at this doc and
listing the four pip packages + the Ironic profile fetch command, so the plan is self-contained.

---

## Quick-start — run the Service-Validator against a local instance today

This is the highest-value first run. Because **we do not serve `$metadata`**, run the validator
**offline against a local schema bundle** so it doesn't try to read `$metadata` for version
discovery.

```bash
# 1. Install
pip install redfish_service_validator

# 2. Get the DSP8010 schema bundle (the full set, so $refs resolve).
#    Our pinned mirror in docs/redfish-reference/schemas/ is a hand-picked SUBSET — the
#    validator wants the whole bundle. Download + unzip DSP8010:
curl -sSL -o dsp8010.zip https://www.dmtf.org/dsp/DSP8010
unzip -q dsp8010.zip -d ./SchemaFiles      # produces the schema/ tree the validator reads

# 3. Make sure the daemon is up on :8443 (mock or real Proxmox backend), Basic auth creds known.

# 4. Crawl + validate the whole tree, offline schemas, skip OEM extensions:
python -m redfish_service_validator.RedfishServiceValidator \
  -r https://localhost:8443 -u "$RF_USER" -p "$RF_PASS" \
  --authtype Basic \
  --payload Tree /redfish/v1 \
  --schema_directory ./SchemaFiles \
  --nooemcheck \
  --logdir ./validator-logs

# 5. Open the HTML report:
open ./validator-logs/*.html     # (Linux: xdg-open)
```

Notes:
- **`--nooemcheck`** suppresses noise from `ComputerSystem.UpdateConfig` / `Bios/SMBIOS` until
  they're moved under `Oem`.
- If the crawl only visits ServiceRoot + Systems, that's variance #7 (incomplete ServiceRoot) —
  the validator can't test what isn't linked. Add a temporary `--payload Single
  /redfish/v1/Managers/<id>` etc. to spot-check unlinked resources until Phase 2 completes the
  links.
- TLS: our cert is self-signed — pass the version's no-cert-check flag (confirm via `--help`).
- The validator exit code is non-zero on failures, so the same command drops straight into CI.

---

## Sources

- Redfish-Service-Validator — https://github.com/DMTF/Redfish-Service-Validator
- Redfish-Protocol-Validator — https://github.com/DMTF/Redfish-Protocol-Validator
- Redfish-Interop-Validator — https://github.com/DMTF/Redfish-Interop-Validator
- Redfish-Usecase-Checkers — https://github.com/DMTF/Redfish-Usecase-Checkers
- Redfish-Mockup-Server — https://github.com/DMTF/Redfish-Mockup-Server
- Redfishtool — https://github.com/DMTF/Redfishtool
- python-redfish-library (PyPI `redfish`) — https://github.com/DMTF/python-redfish-library · https://pypi.org/project/redfish/
- DMTF Redfish standard landing — https://www.dmtf.org/standards/redfish
- DSP0266 (Redfish Specification / protocol) — https://www.dmtf.org/dsp/DSP0266
- DSP0272 (Redfish Interoperability Profiles spec) — https://www.dmtf.org/sites/default/files/standards/documents/DSP0272_1.7.0.pdf
- DSP8010 (Redfish Schema Bundle) — https://www.dmtf.org/dsp/DSP8010
- Redfish JSON Schema index — https://redfish.dmtf.org/schemas/v1/
- Redfish registries index — https://redfish.dmtf.org/registries/
- OpenStack Ironic — Redfish Interoperability Profile — https://docs.openstack.org/ironic/latest/admin/drivers/redfish/interop.html
- Ironic profile file (`OpenStackIronicProfile.v1_2_0.json`) — https://github.com/openstack/ironic/tree/master/redfish-interop-profiles
- OpenStack Ironic — Redfish driver — https://docs.openstack.org/ironic/latest/admin/drivers/redfish.html
- OCP Hardware Management profiles — https://github.com/opencomputeproject/HWMgmt-OCP-Profiles
- Redfish Forum (Interop Validator + profiles discussion) — https://redfishforum.com/thread/557/interop-validator-server-management-profiles
