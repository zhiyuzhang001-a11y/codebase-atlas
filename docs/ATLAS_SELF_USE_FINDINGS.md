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

### SELF-005: removal could not distinguish an Atlas-created rule file

- Observed task: exercise `enable -> remove -> remove` with repository routing
  enabled and require the repository to return to its original shape.
- Expected: if Atlas created `AGENTS.md`, removal deletes it; if the file
  existed before Atlas, removal preserves the file and removes only the exact
  managed block.
- Actual evidence: the original-existence bit lived only in the enable process.
  A later removal either left an empty `AGENTS.md` behind or could not encode
  “the original file was absent” in its recovery validation.
- Safe fallback: preserve the empty file rather than risk deleting user data.
- Resolution: a repository-identity-bound routing ownership record now survives
  process boundaries, participates in lifecycle recovery, and deletes only an
  Atlas-created rule file. Empty Atlas-created routing directories are removed
  after the final recoverable-removal marker is durable. Regression tests cover
  both created and pre-existing rule files.

### SELF-006: executable normalization escaped the Serena environment

- Observed task: run the installed-candidate acceptance with the verified
  Serena tool interpreter.
- Expected: execute the exact virtual-environment launcher supplied by the
  operator.
- Actual evidence: validation called `Path.resolve()`, followed the launcher's
  symlink to its base Python, and then reported that `serena` was not installed.
- Safe fallback: reject that run before treating it as product evidence.
- Resolution: executable validation now makes paths absolute without resolving
  symlinks. Both the concurrency and candidate-project acceptance entrypoints
  preserve virtual-environment launch semantics, with a regression test.

### SELF-007: macOS acceptance temp roots could violate Provider security

- Observed task: enable the candidate in a disposable repository under the
  default macOS per-user temporary directory.
- Expected: the Provider creates its authenticated local daemon endpoint.
- Actual evidence: inherited ACLs on the default temporary path caused the
  Provider to reject the endpoint as insecure.
- Safe fallback: reject the run; do not weaken Provider permission checks.
- Resolution: the macOS acceptance fixture uses a private `/private/tmp`
  directory, removes inherited ACLs from its runtime directory, and retains
  mode `0700`. Product security policy remains unchanged.

### SELF-008: Provider isolation fixtures assumed macOS utilities

- Observed task: prepare checkout/worktree isolation for the public
  Linux/macOS/Windows gate.
- Expected: the same test selects a secure runtime directory on every runner.
- Actual evidence: the fixture hard-coded `/private/tmp` and `chmod -N`, neither
  of which is portable to Linux or Windows.
- Safe fallback: keep the proof local to macOS and do not claim a platform
  matrix result.
- Resolution: runtime fixture creation is now platform-aware; macOS retains its
  ACL cleanup while other systems use their native temporary root and mode
  handling.

### SELF-009: Windows exposed non-portable file snapshot assumptions

- Observed task: run the 0.26 candidate on the public Windows/Python 3.14
  matrix.
- Expected: unchanged routing/config files compare identically across path and
  open-handle snapshots, and generated configuration has canonical bytes.
- Actual evidence: Windows gave `st_ctime_ns` different path/handle semantics,
  text writes translated LF to CRLF while the transaction authorized LF bytes,
  and `readlink` exposed an extended `\\?\\` path prefix. These cascaded into
  safe but false rollback conflicts.
- Safe fallback: the public gate failed; no merge or release was attempted.
- Resolution: routing races use portable identity/mode/size/mtime plus content,
  configuration writes disable newline translation, and Windows link targets
  are normalized without following them. The public matrix is the regression
  proof.
