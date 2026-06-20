# Design — the verified efidisk write path

The single most dangerous operation in the project: overwriting a VM's efidisk LV with a varstore
image. This design specifies the resolver + executor in `hostops.py`. Every numbered check maps to
an `INV-*` in [../SECURITY.md](../SECURITY.md). **Order matters: cheap/derivation checks first,
the device is opened last, and any failure aborts before the open.**

## Module boundary

`hostops.py` is the **only** module that calls `subprocess`. One private chokepoint:

```python
def _run(argv: list[str], *, timeout: int = 120, input_bytes: bytes | None = None) -> CompletedProcess:
    """Sole shell-out point. subprocess.run(argv, shell=False, check=False, capture_output=True).
    argv is always a list; no string is ever passed to a shell (INV-14)."""
```

## Data shapes

```python
@dataclass(frozen=True)
class EfiDisk:
    volid: str          # "local-lvm:vm-3009-disk-0"
    storage: str        # "local-lvm"
    device_path: str    # "/dev/pve/vm-3009-disk-0" (resolved, verified block device)
    efitype: str        # "4m"
    pre_enrolled: bool
    size_bytes: int     # actual LV size

@dataclass(frozen=True)
class SecureBootState:
    enabled: bool
    mode: str           # SetupMode | UserMode | AuditMode | DeployedMode
    has_pk: bool; has_kek: bool; has_db: bool; has_dbx: bool
    source: str         # "varstore" | "sidecar"
```

## Resolver — `locate_efidisk(proxmox, vmid) -> EfiDisk`

1. **INV-01** `vmid` validated as int in PVE range (the caller passes an int; re-assert here).
2. **INV-02/03** read `cfg = proxmox.nodes(NODE).qemu(vmid).config.get()`; require `cfg['efidisk0']`.
3. Parse the volid + options string (`local-lvm:vm-3009-disk-0,efitype=4m,pre-enrolled-keys=1,size=528K`).
4. **INV-04** require `efitype == "4m"`.
5. Resolve the block device: `_run(["pvesm", "path", volid])` → e.g. `/dev/pve/vm-3009-disk-0`.
6. **INV-05** assert the path matches `^/dev/pve/vm-%d-disk-\d+$ % vmid` (anchored, vmid substituted).
7. **INV-06** `os.path.realpath` stays under `/dev/pve/`; `stat.S_ISBLK(os.stat(path).st_mode)`.
8. **INV-07** the LV basename's `vm-<vmid>-` prefix equals `vmid`.
9. `size_bytes = _run(["blockdev", "--getsize64", path])`.
10. Return `EfiDisk`. Any failure raises a typed `HostOpError` (no write attempted).

## Stopped guard — `stopped_vm_guard(proxmox, vmid, *, allow_autostop) -> ctxmgr`

1. **INV-08** read `status.current`; if `qmpstatus != "stopped"`: if `allow_autostop` → `qm stop`
   via proxmoxer and poll until stopped, else raise `VmRunningError`.
2. Acquire the **exclusive per-VM write lock** (reuse the existing file-lock helper) — **INV-15**.
3. **INV-09** re-read status; still stopped or abort.
4. `yield was_running`; on exit, optionally restart if `allow_autostop` and `was_running`.

## Executor — `write_varstore_image(efi, image_path, *, dry_run, expected_sha256) -> WriteResult`

1. **INV-10** `realpath(image_path)` is inside `REDFISH_SB_VARSTORE_DIR`.
2. **INV-11** image is a regular file; its sha256 == `expected_sha256` (from the profile catalog).
3. **INV-12** `image_size <= efi.size_bytes` (prefer `==` 540,672 for 4m); never larger.
4. **INV-19** if the current device content hash == image hash → return `no-op` (no write).
5. **INV-16** build argv: `["dd", f"if={image_path}", f"of={efi.device_path}", "bs=1M", "conv=fsync,notrunc"]`.
   If `dry_run`: **INV-17** audit-log the argv + all resolved facts and return without writing.
6. **INV-17** audit-log "about to write" (caller, vmid, device, image+sha, sizes).
7. Execute via `_run(argv)`; require exit 0.
8. **INV-18** read back `count` bytes from the device, hash, compare to the image hash; mismatch →
   `WriteVerifyError`.
9. **INV-17** audit-log outcome. Return `WriteResult(wrote=True, bytes=..., verified=True)`.

`conv=notrunc` preserves the LV geometry; `conv=fsync` forces the write to stable storage before
verify. `bs=1M` matches the manual baseline. The 528 KB image leaves the rest of the LV untouched.

## State read — `read_varstore_state(efi) -> SecureBootState`

Only invoked when the VM is **stopped** (reading a live efidisk LV is unsafe): `dd` the LV to a temp
file, `_run(["virt-fw-vars", "--input", tmp, "--print"])`, parse PK/KEK/db/dbx presence +
`SecureBootEnable`. Derive `mode`: no PK → `SetupMode`; else `UserMode`. Used to bootstrap/reconcile
the per-VM sidecar; `GET /SecureBoot` normally serves the sidecar (O(1), no `dd`).

## Failure mapping

Each step raises a specific `HostOpError` subclass (see [../spec/error-model.md](../spec/error-model.md)).
`secureboot.sb_error` turns it into the Redfish envelope. **No partial state** is ever exposed: the
device is opened only at step 7, after every precondition has passed.

## Test seams

- `test_hostops.py` patches `hostops._run` and the proxmoxer mock; asserts the **exact argv** and
  that each INV violation raises the right exception — `dd` never runs.
- `test_secureboot.py` patches `proxmox_redfish.secureboot.hostops` wholesale; exercises routing,
  body validation, profile mapping, and error envelopes.
