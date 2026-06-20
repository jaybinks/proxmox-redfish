# Bug log conventions

Lightweight, in-repo bug tracking. The log is [BUGLOG.md](BUGLOG.md) — an append-only table.

## ID scheme

- `BUG-NNN`, monotonically increasing integer (continues the existing `BUG-229`/`BUG-230` series).
- Never reuse or renumber an ID. Closed bugs stay in the log with status `Fixed`/`WontFix`.

## Fields

| Field | Meaning |
|-------|---------|
| ID | `BUG-NNN` |
| Title | one line |
| Status | `Open` · `In-progress` · `Fixed` · `WontFix` · `Duplicate` |
| Severity | `S1` (data loss / security) · `S2` (broken feature) · `S3` (minor) · `S4` (cosmetic) |
| Area | e.g. `hostops`, `secureboot`, `redfish`, `docs` |
| Tags | optional, e.g. `security`, `regression` |
| Linked test | the regression test that proves the fix |
| Notes | root cause + fix summary |

## Lifecycle

1. File as `Open` with a clear repro.
2. When work starts, set `In-progress`.
3. A fix **must** land with a regression test (named in `Linked test`); then set `Fixed`.
4. Security bugs: keep exploit detail out of the public log (see [../SECURITY.md](../SECURITY.md));
   reference the mitigation commit and tag `security`.

## Relationship to invariants

A safety bug in the write path should map to the `INV-*` it violates (or a missing one). If a bug
reveals a gap, add a new `INV-*` to SECURITY.md and a test, then reference both here.
