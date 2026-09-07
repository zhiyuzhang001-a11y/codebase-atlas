# Atlas self-use findings

This is the product feedback ledger produced while Atlas is used to develop
Atlas itself. It records reproducible shortcomings, not speculative feature
requests. A finding stays open until a regression test and a verified change
address the observed behavior.

## Open

### SELF-001: mixed-language repository selects an unrelated fixture project

- Observed task: query the impact of `update_project` in
  `src/codebase_atlas/simple_cli.py` from the Codebase Atlas repository.
- Command: `atlas query impact update_project --config .codebase-atlas.toml
  --target-path src/codebase_atlas/simple_cli.py --direction upstream --depth 2
  --max-nodes 80 --max-edges 120 --stale-policy warn`.
- Expected: select an index scope containing the requested Python target, or
  return a structured scope mismatch that explains how to correct the project
  configuration.
- Actual evidence: the configured TypeScript provider selected
  `fixtures/ts-tests/tsconfig.json`, then raised an unstructured `RuntimeError`
  because the Python target was outside that TypeScript project.
- Safe fallback: reject the Atlas evidence and inspect the relevant source and
  tests directly.
- Follow-up: define mixed-language target routing, add a regression test, and
  replace the raw provider exception with a stable diagnostic and remediation.

## Resolved

### SELF-002: lifecycle rollback existed only in process memory

- Observed task: review `enable`, `update`, and `stop` failure handling while
  preparing lifecycle regression tests.
- Expected: an interrupted process leaves enough durable evidence for the next
  lifecycle command to restore or safely reject the prior transaction.
- Actual evidence: `EnableTransaction` and `RoutingTransaction` could roll back
  exceptions, but process termination discarded their snapshots.
- Safe fallback: report the transitional lifecycle state and avoid accepting it
  as ready.
- Resolution: a repository-identity-bound journal outside the repository now
  snapshots the wider lifecycle boundary, distinguishes accepted operations,
  preserves external edits, cleans owned staging, and is exercised through an
  actual child process exiting without exception cleanup.

### SELF-003: removal receipt was published after destructive changes

- Observed task: inject termination at every `remove` publication boundary.
- Expected: every destructive phase has an already-durable recovery plan.
- Actual evidence: the receipt was written only after configuration deletion
  and data-directory movement, so termination in between was not resumable.
- Safe fallback: retain the `removing` marker and refuse further mutation.
- Resolution: schema 3 provisional receipts are written before the marker and
  before source mutation. Recovery now handles interruption after routing
  removal or data movement and preserves conflicting user edits.
