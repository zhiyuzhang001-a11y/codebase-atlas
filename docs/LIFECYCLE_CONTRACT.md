# Atlas lifecycle contract

This document freezes the short public lifecycle contract for the 0.26 line.
It supplements the detailed deployment rules; it does not authorize global
configuration changes or weaken exact repository identity.

## Commands and exit codes

- `atlas enable`, `atlas stop`, `atlas update`, and `atlas remove` return `0`
  only when the requested state is reached or already holds. Expected failures
  return `2`; an unexpected interrupt is rolled back and re-raised.
- `atlas status` returns `0` for a complete observation of ready, stale,
  stopped, removed, or not-enabled state. Ambiguous identity, invalid state, or
  incomplete discovery returns `2` and never claims ready.
- `atlas verify` returns `PASS/0`, `INCOMPLETE/2`, or `BLOCKED/2`. A project
  intentionally stopped by the user returns `BLOCKED/4` and is not started.
- Every structured failure includes a stable `reason_code`, a readable error or
  failed check, and one preferred `next_action`. Existing schema-1 fields retain
  their meaning; additions are backward-compatible.

Project lifecycle, index freshness, Codex configuration, discovery completeness,
and current-task connection are separate fields. A configured MCP entry is not
evidence that an already-open Codex task loaded it.

## Read-only budgets

`status` does not install, repair, refresh, start a Provider, or persist a scan
cache. Nested repository discovery has these fixed limits:

- total discovery time: 2.0 seconds;
- directories visited: 4,096;
- depth below the exact Git root: 6;
- each local Git identity probe: at most 0.25 seconds;
- symbolic directory links are not followed.

Crossing any limit returns `discovery=partial` and `status=incomplete/2`.

`verify` does not refresh. It may start one owned stdio Provider to run its real
query, then closes stdin and waits 10 seconds, terminates and waits 3 seconds,
and finally kills and waits 3 seconds. Failure to prove cleanup makes the result
incomplete. Existing shared Providers are not selected for termination.
The multi-MCP release gate then allows 20 seconds for all owned descendants and
new Provider processes to disappear; any remainder fails the gate.

## Recovery and ownership

Lifecycle writes are serialized per exact repository. Enable, update, and stop
publish a repository-external recovery journal before project changes. Remove
publishes a provisional receipt before its first destructive change. A later
write-capable lifecycle command may recover these records; `status` and `verify`
only report `lifecycle_recovery_required`.

Recovery restores only Atlas-owned state or a repository file whose current
content matches a state authorized by the interrupted operation. Unrecognized
user edits are preserved and recovery fails closed. Routing assets are updated
or removed only when their managed content hash is recognized.

## Baseline measurement

On 2026-09-07, a 20-run warm/cold-mixed sample on an Apple M4, macOS arm64,
Python 3.13.7, and a fresh empty Git repository measured `status` at 18.355 ms
median, 20.445 ms p95, and 20.495 ms maximum. The product repository at that
point contained 164 tracked files and 71 directories. These measurements record
the test context; the fixed safety budgets above are the contractual limits.
