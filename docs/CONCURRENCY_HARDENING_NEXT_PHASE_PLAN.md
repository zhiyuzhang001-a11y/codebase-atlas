# Concurrency hardening next-phase plan

## Objective

Extend the verified Python/macOS concurrency boundary to TypeScript, larger and
more heavily contended repositories, every durable refresh publication phase,
and real Linux and Windows Provider executions. Preserve the existing invariant:
a successful query belongs to one fresh, fully published generation; contention
or recovery uncertainty is an explicit diagnostic, never a stale success.

This phase does not change machine-local Atlas/Codex configuration, install a
different Provider, or publish a release. CI and release activation remain
separate reviewed operations.

## Workstream 1: deterministic publication failpoints

Add durable journal phase transitions around these boundaries:

1. recovery journal prepared;
2. Provider index returned;
3. Provider database validated;
4. Python sidecar published, when applicable;
5. generation manifest published;
6. index state published;
7. in-memory generation activated;
8. transaction committed.

The production path will expose phase observations without enabling arbitrary
failure from user configuration. Tests will kill the owning MCP externally after
observing each durable phase, start a new MCP, and verify either exact rollback to
the previous generation or acceptance of the complete new generation.

Failure criteria:

- mixed provider/sidecar/manifest/state generations;
- an ownerless journal or backup after recovery;
- a deleted or modified fact served from the rejected generation;
- recovery deleting an artifact owned by a later transaction;
- an MCP or Provider child remaining after the harness exits.

## Workstream 2: TypeScript multi-MCP parity

Generalize the existing stress harness with `--language python|typescript` and
language-specific fixtures. The TypeScript fixture will include declarations,
imports, direct calls, renamed files, test callbacks, and a minimal checked-in
`tsconfig.json`. Each round will run concurrent create, modify, rename, and delete
operations through independent MCP processes.

Required assertions:

- definition, references, callers, callees, related-tests, and impact results
  bind to the same generation;
- renamed paths replace old paths rather than coexist with them;
- deleted symbols and edges are absent;
- TypeScript continuation tokens from an earlier generation are rejected;
- Python-only sidecar artifacts are absent;
- exact-root and foreign-project checks remain unchanged.

## Workstream 3: scale and contention profiles

Parameterize the harness instead of cloning scenarios:

- MCP clients: 4, 8, and 16;
- concurrent writers: 3, 6, and 12;
- files per mutation batch: 1, 10, 100, and 1,000;
- repeated rounds: 10 for pull-request qualification and at least 50 for a
  scheduled soak;
- source sizes: compact symbols plus large files that exercise parser and graph
  publication time;
- one-project contention and simultaneous independent-project refreshes.

Record refresh-owner wait, Provider admission wait, end-to-end query latency,
generation convergence, retry counts, process RSS where portable, and cleanup
time. Freeze regression thresholds only after collecting three clean baseline
runs on each supported runner; correctness gates do not depend on latency
percentiles.

Failure criteria:

- starvation: one live client cannot complete within its declared deadline while
  earlier clients continue completing;
- unbounded retry growth or thundering-herd Provider indexing;
- generation divergence between clients after a phase barrier;
- monotonic process or staging growth across rounds;
- a successful empty answer whose truncation reason is internal contention.

## Workstream 4: real operating-system gates

Keep the existing fast 12-job unit matrix. Add a separate manually dispatchable
and scheduled `Concurrency Acceptance` workflow using verified managed Provider
artifacts for:

- Ubuntu x86-64 and ARM64;
- macOS Intel and Apple Silicon;
- Windows x86-64 and ARM64 when the corresponding hosted runner is available.

The workflow will use native path, locking, process termination, and temporary
directory APIs. POSIX signal-specific cases will have Windows equivalents using
process termination rather than being silently skipped. Every job uploads its
structured ledger on failure and a compact signed-off summary on success.

No platform is reported as verified until its native Provider binary completes:

1. Python 10-round multi-MCP stress;
2. TypeScript 10-round multi-MCP stress;
3. every publication failpoint recovery case;
4. an 8-client/100-file contention profile;
5. exact-root doctor, fresh-index, deep-integrity, target-hit, foreign-miss, and
   zero-residual-process gates.

## Workstream 5: release and deployment qualification

After all native jobs pass on one exact commit:

1. run package reproducibility, lifecycle, and public-readiness checks;
2. install the candidate wheel and matching Provider into new versioned
   locations without replacing the known-good installation;
3. repeat clean-room Python and TypeScript exact-root acceptance;
4. verify upgrade, downgrade, and uninstall recovery;
5. update the support matrix and release checklist with exact CI run IDs;
6. request separate authorization before tagging, publishing, or changing any
   machine/project MCP configuration.

## Execution order

Implement failpoint observability first because it supplies reusable evidence
for both languages and every OS. Add TypeScript parity next, then scale profiles,
then the remote workflow. Do not tune performance until correctness workloads are
green; do not publish while any native platform remains unverified.

## Completion statement

Completion means zero observed failures across the matrix above on one exact
commit and Provider identity. It remains a bounded empirical statement, not a
claim that all future workloads or Provider versions are failure-free.
