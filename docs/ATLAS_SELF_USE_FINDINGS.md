# Atlas self-use findings

This is the product feedback ledger produced while Atlas is used to develop
Atlas itself. It records reproducible shortcomings, not speculative feature
requests. A finding stays open until a regression test and a verified change
address the observed behavior.

## Open

No open self-use findings at this checkpoint.

## Resolved

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
- Resolution: target language scope is checked before Provider startup. CLI
  queries now return structured
  `target_outside_indexed_language_scope`/exit 2 with one remediation; MCP
  queries return the same reason as explicit incomplete evidence. Mixed-language
  multi-index routing remains a separate future capability, not an implied fact.

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

### SELF-004: concurrency qualification accepted invalid runtime paths too late

- Observed task: run the real four-client Python MCP qualification locally.
- Expected: missing Node.js or Serena runtimes fail before indexing and stress.
- Actual evidence: an empty Node lookup became the current directory and was
  rejected only by the final doctor after the concurrency rounds had run.
- Safe fallback: reject that run as a gate failure and rerun with the verified
  bundled Node and installed Serena interpreter.
- Resolution: the stress entrypoint now requires Provider, Node.js, and Serena
  Python to be executable files before creating the test repository.
