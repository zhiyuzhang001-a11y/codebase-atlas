# Concurrency hardening next-phase results

## Current outcome

The local macOS ARM64 qualification boundary passed for deterministic refresh
recovery, Python and TypeScript multi-MCP operation, and the first larger
contention profile. The native six-platform workflow is implemented but has not
yet run on GitHub, so Linux, Windows, macOS Intel, and hosted ARM claims remain
pending CI evidence.

The working tree is based on commit
`499383f1ef3e1e006ff4f3a8043334584a6f12db` and uses managed Provider
`0.10.8-atlas.2+e088a41b`. These results cover uncommitted changes on branch
`codex/multi-mcp-concurrency-stability`; they are not a release qualification.

## Implemented controls

- The recovery journal now records every durable publication boundary from
  `prepared` through `committing`, rejects unknown or non-monotonic transitions,
  and assigns explicit rollback or accept-new-generation semantics to each
  phase.
- A test-only coordinator phase observer exposes deterministic boundaries
  without adding a user-configurable production failure switch.
- An external-process test stops a refresh owner at all nine durable phases.
  A new process then recovers the exact transaction and verifies complete
  rollback or acceptance, followed by a second cleanup pass with zero residue.
  This deterministic matrix uses the production transaction code with a
  controlled Provider-database fixture; the real Provider crash path is covered
  separately at journal publication, not yet at all nine boundaries.
- The real Provider stress harness supports Python and TypeScript, configurable
  clients, writers, files per writer, rounds, and persistent JSON ledgers.
- TypeScript qualification covers definition, references, callers, callees,
  related tests, upstream impact, continuation paging, prior-generation token
  rejection, same-generation binding, rename replacement, deletion, and the
  absence of Python-only sidecars.
- Process termination and residual-process inventory now have native POSIX and
  Windows implementations. Windows uses process termination plus CIM process
  inventory instead of POSIX signals or `ps`.
- `Concurrency Acceptance` is scheduled and manually dispatchable across the
  six managed Provider targets. It downloads a previously verified Provider
  artifact set, revalidates checksums and exact source identity, runs both
  10-round language gates, runs the deterministic phase matrix, and adds an
  8-client 100-files-per-writer profile. Structured ledgers are uploaded on
  every run.

## Executed local evidence

- Complete regression suite with the managed Node runtime: 335 tests passed;
  five real-Provider integration cases remained environment-gated. An earlier
  run before the Windows process-inventory regression was added passed 334 tests
  with 14 expected environment skips.
- Four opt-in real-Provider integration tests passed: post-publication rollback,
  same-child mutation/rename/delete, simultaneous two-project isolation, and
  no-op/small-batch/warm-query/long-task replay.
- Durable phase matrix: all nine external termination cases passed in 4.25
  seconds. Early phases restored byte-identical prior artifacts; published
  phases retained the journal's candidate generation; every second recovery
  pass removed zero files.
- TypeScript qualification: 10 rounds passed with four MCP clients and three
  concurrent writers. The ledger contains 40 distinct create, modify, rename,
  and delete generation identifiers. Maximum observed owner wait was about
  6.94 seconds.
- Python qualification: the matching 10-round gate passed with 40 distinct
  generation identifiers. Maximum observed owner wait was about 6.91 seconds.
- Larger Python contention: one round passed with eight MCP clients, six
  concurrent writers, and ten files per writer. All four generation barriers
  converged and maximum observed owner wait was about 7.16 seconds.
- Every real run ended with `doctor=ready`, `index=fresh`, healthy deep Provider
  integrity, an exact baseline target hit, zero foreign hits, disabled Provider
  watchers, clean source restoration, clean staging, and no residual MCP or
  Provider process.

## Findings during this phase

Two stress-harness defects were found and corrected before qualification:

- a local variable shadowed the language source generator in the first
  TypeScript run;
- the first scale run paired writer sentinel symbols with the wrong paths when
  `files-per-writer` exceeded one. Atlas returned the correct renamed path; the
  assertion mapping was wrong.

The first cross-generation continuation check returned
`continuation_unavailable`, rather than `continuation_stale`, because adopting a
new published generation intentionally clears the old session cache. Both are
safe rejection outcomes; successful continuation reuse is forbidden.

## Remaining qualification

- Run the new workflow on all six native managed Provider targets and preserve
  the exact run IDs.
- Extend the nine-phase observer driver from the controlled Provider fixture to
  the native Provider transport before claiming real-Provider coverage at every
  individual publication boundary.
- Collect three clean baselines per runner before freezing latency thresholds.
- Execute the 16-client/12-writer, 100-file and 1,000-file profiles, plus the
  scheduled 50-round soak and concurrent independent-project scale profile.
- Repeat package lifecycle and clean-room upgrade/downgrade checks only after
  native correctness is green on one exact commit.
- Obtain separate authorization before tagging, publishing, or changing any
  machine or project MCP configuration.
