# Architecture

## Current daemon (baseline)

Single module `src/proxmox_redfish/proxmox_redfish.py` (~2283 lines):

- `RedfishRequestHandler(BaseHTTPRequestHandler)` — `do_GET` (~L1441), `do_POST` (~L1616),
  `do_PATCH` (~L1811). Routing is a flat `if/elif` ladder matching `path.rstrip("/").split("/")`.
- Resource handlers are **module-level free functions** `(proxmox, vm_id, ...) -> dict | (dict, int)`.
  The dispatcher normalizes: `if isinstance(response, tuple): response, status_code = response`.
- Proxmox access **only** through `proxmoxer.ProxmoxAPI` over HTTPS:8006. Config via env-var globals
  (`PROXMOX_HOST/USER/PASSWORD/NODE`, `PROXMOX_API_PORT`, `VERIFY_SSL`, `PROXMOX_ISO_STORAGE`).
- `handle_proxmox_error(op, exc, vm_id) -> (dict, int)` maps proxmoxer exceptions to Redfish errors.
- **No host shell-out exists today** (no `subprocess`/`dd`/`/dev/pve`). SecureBoot introduces the first.

## SecureBoot additions (Phase 1+)

Two new modules keep the dangerous code isolated and testable; the monolith gains only a thin
delegating branch per verb.

```
                    HTTP request
                         │
              RedfishRequestHandler (monolith)
        do_GET / do_POST / do_PATCH  (existing ladder)
                         │  one delegating elif per verb:
                         │  if secureboot.is_secureboot_path(parts): route_*()
                         ▼
        ┌──────────────────────────────────────────┐
        │ secureboot.py  (Redfish surface, pure)    │
        │  - route_get/route_post/route_patch       │
        │  - handlers: get_secureboot, patch_*,     │
        │    action_reset_keys, get_db_collection   │
        │  - profile loader + per-VM sidecar state  │
        │  - sb_error() → Redfish error envelope    │
        └──────────────────────────────────────────┘
                         │ calls (only seam to the host)
                         ▼
        ┌──────────────────────────────────────────┐
        │ hostops.py  (THE ONLY place that shells   │
        │              out; all INV-* enforced here)│
        │  - locate_efidisk(proxmox, vmid)          │
        │  - vm_is_running(proxmox, vmid)           │
        │  - stopped_vm_guard(...)                  │
        │  - write_varstore_image(efi, image)  ←dd  │
        │  - read_varstore_state(efi) ← virt-fw-vars│
        │  - HostOpError hierarchy                  │
        │  - _run(argv) single subprocess chokepoint│
        └──────────────────────────────────────────┘
              │ proxmoxer (REST)        │ subprocess (local, shell=False)
              ▼                         ▼
         Proxmox API :8006        dd / virt-fw-vars / pvesm  on the PVE host
```

### Why this split
- The 2283-line monolith should not absorb host shell-out + key logic; isolation keeps the
  risky `dd` path in one small, exhaustively-tested module (`hostops.py`).
- Unit tests for `secureboot.py` mock **one seam** (`hostops`); `test_hostops.py` mocks
  `subprocess` and asserts the exact argv per invariant — `dd` never actually runs in tests.
- Routing style is unchanged: one `elif secureboot.is_secureboot_path(parts)` added to each verb,
  with sentinel `NOT_HANDLED` so non-SecureBoot paths fall through to the existing 404.

### Wiring points in the monolith
- `do_GET` ~L1441, `do_POST` ~L1616, `do_PATCH` ~L1811 — add one delegating branch each.
- `get_vm_status` ~L1353 — add `"SecureBoot": {"@odata.id": ".../SecureBoot"}` to the response.
- Reuse `handle_proxmox_error` (~L105) for proxmoxer failures; `sb_error` handles host-op failures.
- Phase 4 adds a `do_DELETE` method (mirrors `do_PATCH` boilerplate) for certificate removal.

## SecureBoot operation data flow (apply a profile)

1. Client `PATCH /Systems/{vmid}/SecureBoot {"SecureBootEnable": true}` (or `ResetKeys` action).
2. `secureboot.route_patch` validates body, resolves the target profile.
3. `hostops.locate_efidisk` reads `qemu(vmid).config.get()['efidisk0']`, parses volid + `efitype`,
   resolves the block device (via `pvesm path`), checks `efitype=4m` (INV-02..04).
4. `hostops.stopped_vm_guard` confirms VM stopped via API (INV-08), takes the per-VM write lock,
   re-checks stopped (INV-09).
5. `hostops.write_varstore_image` validates device path regex + block-device + size (INV-05..12),
   then `dd if=<profile image> of=<device> bs=1M conv=fsync,notrunc` as argv (INV-14), post-write
   verify (INV-18), audit log (INV-17).
6. Update the per-VM sidecar state file; return the refreshed `SecureBoot` GET body (200).

## State model

Per-VM sidecar JSON `${REDFISH_SB_STATE_DIR}/vm-<vmid>.sb.json`: `{enabled, profile, mode,
has_pk/kek/db/dbx, applied_at, image_sha256}`. `GET /SecureBoot` reads this in O(1) (avoids
`dd`-on-every-GET, which is unsafe on a running VM's LV). Bootstrapped from a live varstore read
when absent (VM stopped) and reconciled after each write.
