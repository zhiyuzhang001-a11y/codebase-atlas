# Atlas self-use findings

This is the product feedback ledger produced while Atlas is used to develop
Atlas itself. It records reproducible shortcomings, not speculative feature
requests. A finding stays open until a regression test and a verified change
address the observed behavior.

## Open

### SELF-010: cross-project refreshes raced in the shared Provider daemon

- Observed task: run two real repository refreshes concurrently in the
  six-architecture Provider qualification gate.
- Expected: both distinct projects publish fresh, isolated generations and the
  final frontend disconnect releases all private-fixture files.
- Actual evidence: Windows x86_64 intermittently returned one `refreshed` and
  one `failed`; improved diagnostics then identified a false path-safety
  rejection caused by 8.3/long-path spelling changes during concurrent
  directory creation. Both Windows architectures also retained daemon
  log/lifetime-lock handles long enough for immediate fixture cleanup to fail.
- Safe fallback: reject the architecture gate and do not merge or release.
- Planned resolution: validate project names lexically without racing two path
  resolutions, serialize only Provider mutation calls across projects, retain
  concurrent read queries, wait for the private non-permanent daemon to retire
  in real Windows fixtures, then rerun local and six-architecture gates.

### SELF-011: Serena child-exit diagnostics omitted stderr

- Observed task: run the real multi-MCP stress gate on Linux x86_64.
- Expected: if the Serena subprocess exits, the structured failure identifies
  its underlying startup or runtime error.
- Actual evidence: Atlas reported only `Serena runner exited before responding
  (exit=1)`; concurrent clients also wrote to one shared `runner.stderr.log`, so
  the failed process's diagnostic could not be attributed reliably.
- Safe fallback: reject the architecture gate and avoid guessing at the cause.
- Planned resolution: give each runner a private stderr log, include a bounded
  tail in child-exit errors, clean successful-run logs, and rerun the failing
  architecture before deciding whether a deeper Serena coordination fix is
  required.

### SELF-012: cleanup probed a retiring Unix daemon unnecessarily

- Observed task: wait for a private Provider daemon to retire after the Linux
  ARM dual-repository isolation proof had passed.
- Expected: `daemon status` returns promptly and permits bounded cleanup.
- Actual evidence: Linux ARM's `daemon status` frontend itself remained in the
  daemon retirement path for more than five seconds and timed out. Unlike
  Windows, Unix does not require the process to release handles before removing
  the private fixture tree.
- Safe fallback: reject the platform job even though its isolation assertions
  passed.
- Planned resolution: poll daemon retirement only on Windows, where open file
  handles block fixture removal, then rerun the cross-platform gate.

### SELF-013: project Codex routing still translated newlines on Windows

- Observed task: run `enable` from the installed 0.26 candidate wheel on
  Windows x86_64.
- Expected: the bytes authorized by lifecycle recovery exactly match the bytes
  published to `.codex/config.toml`.
- Actual evidence: project configuration writes used text mode's platform
  newline translation, producing CRLF while the transaction authorized LF;
  acceptance correctly failed closed and preserved the unexpected file.
- Safe fallback: reject the installed-candidate lifecycle gate and do not
  weaken transaction comparison.
- Planned resolution: disable newline translation at every project Codex
  config write/update/remove path and cover the file-open contract directly.

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
