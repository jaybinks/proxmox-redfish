# Security Review — Adversarial Hardening Pass

Scope: `src/proxmox_redfish/*.py`, `packaging/*`, `config/*`, dependencies.
The daemon runs as **root** on a Proxmox host, exposes a Redfish HTTP API,
authenticates callers against Proxmox, and performs host-side block-device writes
(`dd` onto VM efidisk LVM volumes) for SecureBoot enrollment.

All findings below were fixed in this pass. Gates after the work:

- `pytest tests/unit` — **302 passed** (282 pre-existing + 20 new regression tests).
- `black --check`, `isort`, `flake8`, `mypy` — clean.
- `bandit -r src/` — **No issues identified** (3 documented `# nosec` best-effort swallows + 1 intentional bind-all).
- `pip-audit` — **No known vulnerabilities** after the setuptools upgrade.
- DMTF conformance unaffected: all new behaviour is gated behind safe-by-default env
  toggles; no response schema changed; the `INV-01..20` SecureBoot invariants are
  untouched (and the SSRF/limit work strengthens the surrounding surface).

---

## Findings & fixes

### F1 — Credential leakage in request/response DEBUG logs — HIGH
**Files:** `proxmox_redfish.py` `do_GET`/`do_POST`/`do_PATCH` (request + response logging).
**Issue:** Handlers logged the full header block at DEBUG, including
`Authorization: Basic <base64(user:password)>` and `X-Auth-Token`, plus full request
bodies (the session-login body carries `Password`) and full response bodies (the
session-create response and the Sessions collection contain bearer-token URIs).
**Exploit:** With `REDFISH_LOG_LEVEL=DEBUG` (and especially with Loki remote push
enabled) every caller's Proxmox password and every live session token are written to
syslog/Loki, where a log reader (lower-privileged than root) harvests them.
**Fix:** Added `_redact_headers()` (masks `authorization`/`x-auth-token`/`cookie`/
`password`) and `_redact_payload_for_log()` (masks `password`/`passwd`/`secret`/
`token` recursively). All three request logs now redact headers and bodies. The
GET/POST response logs no longer emit the body (status only), so session-token URIs
are never persisted.

### F2 — Plaintext password used as the session key (Basic auth) — HIGH
**File:** `proxmox_redfish.py` `validate_token` (Basic branch).
**Issue:** On Basic auth the code did `token = f"{username}-{password}"` and stored a
session under that key. The Sessions collection (`GET /redfish/v1/SessionService/
Sessions`) renders each key as a member URI `/Sessions/<username>-<password>`,
**leaking the plaintext password** to any authenticated caller, and the store kept
the password in memory indefinitely.
**Exploit:** Any authenticated user lists the Sessions collection and reads other
users' (including `root@pam`'s) plaintext passwords out of the member URIs.
**Fix:** Basic auth no longer creates a server-side session at all (credentials are
re-sent every request, so none is needed). No password is ever used as a key or
stored for Basic auth.

### F3 — Unbounded session store / no Basic expiry — MEDIUM
**File:** `proxmox_redfish.py` session store + `validate_token`.
**Issue:** The `sessions` dict grew without bound and held plaintext passwords for
the full lifetime; Session-auth expiry used a hard-coded `3600`.
**Fix:** Added `SESSION_TTL_SECONDS` / `MAX_SESSIONS` and `_prune_sessions()` (drops
expired entries, evicts oldest beyond the cap), called on session creation.
Session-token length increased to `secrets.token_hex(32)`.

### F4 — SSRF via event subscription delivery — HIGH
**File:** `redfish_services.py` `create_subscription` / `emit_event`.
**Issue:** An authenticated caller could register any `http(s)` Destination; the root
daemon then POSTed to it (including on `SubmitTestEvent`). Worse, delivery used
`verify=False`.
**Exploit:** `Destination: http://169.254.169.254/latest/meta-data/...` or
`http://127.0.0.1:8006/...` turns the daemon into an SSRF proxy reaching the cloud
metadata service, the local Proxmox API, or RFC1918 hosts; the receiver's TLS was
also unverified.
**Fix:** Added `validate_event_destination()` — http(s) only, and (unless
`REDFISH_EVENT_ALLOW_INTERNAL=1`) rejects literal/loopback/link-local/private/
reserved/multicast targets. It is enforced at **subscribe** time and re-checked at
**delivery** time (defends DNS rebinding). `verify` now defaults true
(`REDFISH_EVENT_VERIFY`). Unresolvable public hostnames remain allowed (they cannot
reach an internal service). Subscription store bounded by `MAX_SUBSCRIPTIONS` (507
when full).

### F5 — SSRF via ISO / virtual-media URL fetch — HIGH
**File:** `proxmox_redfish.py` `_ensure_iso_available`.
**Issue:** `InsertMedia` `Image` URLs were fetched by the root daemon with no target
restriction beyond an `http(s)` prefix check.
**Exploit:** Same class as F4 — point `Image` at `http://169.254.169.254/...` or an
internal service and exfiltrate/transit via the download path.
**Fix:** Added `_validate_fetch_url()` with the same internal-address guard
(`REDFISH_ISO_ALLOW_INTERNAL` opt-out), called before any `requests.get` of a remote
ISO. `:iso/` storage references (no fetch) are unaffected.

### F6 — Unbounded request body read — MEDIUM (DoS)
**File:** `proxmox_redfish.py` `do_POST`/`do_PATCH`.
**Issue:** `self.rfile.read(content_length)` read an attacker-chosen size into memory.
**Fix:** `MAX_REQUEST_BODY_BYTES` (8 MiB default) checked against `Content-Length`;
oversized requests get a `413` via `_reject_oversized_body()` before any read.

### F7 — File permissions on the secrets file — MEDIUM
**File:** `packaging/postinst`.
**Issue:** `params.env` (holds `PROXMOX_PASSWORD` and Loki credentials) was installed
`0640` (group-readable). State/varstore dirs were not consistently owned/locked.
**Fix:** `params.env` is now `root:root 0600`; `/var/lib/proxmox-redfish` and its
`secureboot`/`varstores` subdirs are `root:root 0750`; `$ETC` is `0750`; any
`*.key` under `$ETC` is forced to `0600`.

---

## Reviewed and confirmed SAFE (no change needed)

- **Command injection / subprocess (`hostops._run`)** — every host command (`dd`,
  `pvesm`, `blockdev`, `virt-fw-vars`) runs as an argv list through the single `_run`
  chokepoint with `shell=False`; no user string is interpolated into a shell. INV-14
  holds.
- **Device-path resolution / TOCTOU (INV-05/06/07)** — the efidisk device path is
  derived from `qemu(vmid).config.get()['efidisk0']` (never the request), validated
  against an anchored `^/dev/<allowed-vg>/vm-<vmid>-disk-\d+$` regex, `realpath`-ed,
  and `stat.S_ISBLK`-checked immediately before the write. The vmid is cross-checked
  into the pattern. Fail-closed (INV-20).
- **Varstore allowlist (INV-10/11/12)** — source image `realpath` must be inside
  `REDFISH_SB_VARSTORE_DIR`, a regular file, sha256-matched, and not larger than the
  LV; dry-run is the default (`REDFISH_SB_ALLOW_WRITE` opt-in).
- **Cert staging ids** — `_read_staged` only accepts a 16-hex-char id; cert ids are
  content-derived sha256 (no path traversal).
- **Private-key rejection (INV-13)** — `validate_public_certificate` rejects every
  private-key marker (PKCS#1/8, EC, DSA, OpenSSH, PGP) and fully parses the X.509 cert
  with `cryptography`; size-capped at 64 KiB.
- **Auth bypass / AUTH=None** — `AUTH` is a module constant fixed to `"Basic"` (not
  env-driven); the `None` branch is unreachable remotely. Unauthenticated access is
  limited to `/redfish`, `/redfish/v1`, `/redfish/v1/odata`, and `$metadata`. Caller
  credentials are passed through to Proxmox, so per-VM authorization is enforced by
  Proxmox ACLs (defense-in-depth; the daemon's own `check_user_vm_permission` bypass
  is therefore not an authz hole — Proxmox returns 403 for unauthorized VMs).
- **Deserialization** — only `json.loads` on untrusted input; no `eval`/`pickle`/
  `yaml.load`. Config loader is `json.load` of an admin-supplied file.

---

## Dependency changes

`requirements.txt` (runtime) — split out dev tooling and raised floors to include
security fixes:
- `requests>=2.32.3` (CVE-2024-35195 `Session.verify`, CVE-2024-47081 `.netrc` leak;
  pulls patched `urllib3>=2`).
- `cryptography>=42.0.4` (CVE-2023-50782, CVE-2024-26130, NULL-deref/PKCS7 fixes).
- `requests-toolbelt>=1.0.0`, `proxmoxer>=2.0.0` retained.

`requirements-dev.txt` — added `pip-audit>=2.7.0`, raised `bandit>=1.7.7`, pinned
`setuptools>=78.1.1` (CVE-2024-6345 RCE, PYSEC-2025-49 path traversal).

`pip-audit` on the project venv reported 5 findings, all in **setuptools 65.5.0**
(CVE-2024-6345, PYSEC-2022-43012, PYSEC-2025-49). Upgrading setuptools to ≥78.1.1
cleared them — **0 remaining**. The runtime deps (requests 2.34.2, cryptography
49.0.0, proxmoxer 2.3.0, urllib3 2.7.0, certifi 2026.6.17) were already current.

## Bandit summary

After the pass: **No issues identified.** Documented annotations:
- `# nosec B104` on the `0.0.0.0` default — a BMC-style endpoint must be
  network-reachable; TLS + Proxmox auth gate every request.
- `# nosec B110` on four best-effort `except: pass` swallows (logging setup, group
  lookup that fails closed, temp-file cleanup, optional cert metadata) — each already
  carried an explanatory comment.

## New configuration toggles (safe defaults)

| Env var | Default | Purpose |
|---|---|---|
| `REDFISH_MAX_BODY_BYTES` | `8388608` | Max request body (DoS) |
| `REDFISH_SESSION_TIMEOUT` | `3600` | Session lifetime |
| `REDFISH_MAX_SESSIONS` | `256` | Session store cap |
| `REDFISH_MAX_SUBSCRIPTIONS` | `128` | Subscription store cap |
| `REDFISH_ISO_ALLOW_INTERNAL` | `0` | Allow ISO fetch to internal IPs (SSRF opt-out) |
| `REDFISH_EVENT_ALLOW_INTERNAL` | `0` | Allow event delivery to internal IPs (SSRF opt-out) |
| `REDFISH_EVENT_VERIFY` | `true` | Verify TLS for event delivery |
