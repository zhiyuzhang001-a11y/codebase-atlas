# Changelog

All notable release changes are recorded here. The package uses semantic versions;
0.x minor versions may still refine interfaces within the documented support
boundary.

## 0.23.0 — 2026-09-02

- Add read-only exact dirty planning and an explicit same-MCP refresh operation
  so long-running tasks can refresh without a new task or a self-owned Provider
  lock conflict.
- Publish Provider database, Python registration sidecar, generation manifest
  and index state as one validated transaction, restoring the previous
  generation after failures, timeouts and cancellation.
- Bind queries to one project generation and coordinate concurrent readers and
  refresh writers across Atlas processes while preserving cross-project
  isolation.
- Keep automatic repository watching out of this release after its corrected
  process-tree idle CPU measurement exceeded the frozen resource gate.
- Update the Codex project rule to refresh once after a batch of source changes
  and before the next Atlas query, without requiring a user reminder.

## 0.22.1 — 2026-09-01

- Bound Atlas-owned Python registration and exact-reference inventories to the
  exact Git repository's tracked plus non-ignored untracked files.
- Exclude standard Git-ignored corpora, caches and nested repositories from
  Python facts while retaining a deterministic safe fallback for non-Git roots.
- Reject symlinked, escaped, missing and non-regular Python inventory entries;
  use the same boundary for onboarding sidecars and query-time reference scans.

## 0.22.0 — 2026-09-01

- Add bounded `locate_files` retrieval that returns at most two heuristic,
  repository-relative source candidates from the existing structural index;
  results remain explicitly non-exhaustive and never claim an exact callable.
- Keep one repository-bound managed Provider MCP transport alive across Atlas
  calls, with serialized request handling, deterministic shutdown and contained
  process cleanup.
- Separate the two-second global lock-admission budget from the longer cold
  Provider startup and MCP initialization budget, while preserving bounded warm
  calls and explicit `provider_busy` behavior.
- Pass independent 16-task acceptance at 13/16 Top 2 with 98.703% median
  repository-reading reduction, 18.403 ms warm P95 and 411.531 MiB peak RSS.
- Pin reproducible managed Provider bundles to exact source commit `e088a41b`
  and managed version `0.10.8-atlas.2+e088a41b` for all six release targets.

## 0.21.0 — 2026-08-29

- Add fail-closed `mcp-auto` discovery from the MCP startup directory to the
  innermost Git root, with explicit unconfigured, incomplete, invalid,
  mismatched, and ambiguous project states and no cross-project fallback.
- Add dry-run-first `global-auto` Codex registration and verified migration of
  the exact legacy fixed-repository Atlas transport, including exact rollback
  on add or read-back failure and refusal of foreign entries.
- Add dry-run-first project-scoped Codex integration that preserves unrelated
  `.codex/config.toml` bytes, refuses foreign/unsafe entries, and removes only
  its exact managed block without changing global configuration.
- Add exact `project_status` and MCP initialize guidance so an agent can verify
  the resolved repository before using Atlas after an A→B→A project switch.
- Add opt-in bounded session-start index refresh with a no-Provider fresh path,
  fast `provider_busy` fallback, explicit failure metadata, and preservation of
  the previous index.
- Add asynchronous, 24-hour-cached GitHub Release awareness that only notifies,
  never installs, and can be disabled without affecting MCP or queries.
- Add explicit migration from preserved per-project Provider caches to one
  deterministic account-level shared layout with exact session roots, rollback,
  conflict and disk-safety gates.
- Allow unrelated Atlas projects to query and index concurrently while keeping
  same-project mutation serial, cancellation isolated and project stores
  separate.
- Add complete 512-file two-pass large-repository indexing plus memory-aware
  daily/large admission, FIFO cancellation-safe queueing and contained
  process-tree RSS feedback. The frozen TypeScript graph stays exactly equal
  while peak RSS falls below the separate 3 GiB gate.
- Keep the exact-only impact contract when Codebase Memory labels an LSP edge
  below the 0.9 confidence floor; the final installed candidate preserves M17
  504/504 and M19 147/147 exact results.
- Document project-maintained, reproducible Codebase Memory bundles as an
  independent fallback channel with exact source, checksum, license and rollback
  evidence; upstream merge remains optional.
- Report an activated shared Provider layout accurately in `doctor`, rather
  than continuing to describe the already-migrated project as a future target.
- Publish separately licensed, exact-source managed Provider bundles for Linux
  x86_64/ARM64, macOS Intel/Apple Silicon, and Windows x86_64/ARM64, with two-build
  reproducibility checks, embedded manifests and SHA-256 verification.

## 0.20.0 — 2026-08-27

- Add one bounded `analyze_change` product operation across CLI and MCP. It
  resolves the exact definition first, stops on ambiguity, and returns callers,
  callees, references, impact, related tests, recommended reads, provenance,
  freshness and per-subquery completeness under one shared deadline.
- Add dry-run-first `codex plan`, explicit `codex apply`, and identity-safe
  `codex remove`; preserve virtualenv transports and refuse to overwrite or
  remove a different existing MCP entry.
- Bound Provider lock contention to two seconds, report `provider_busy`, reuse
  that result within the service session, and reliably release owned Provider
  state when an MCP client terminates.
- Pass the 16-task Codebase Atlas/FastAPI/Vite acceptance with 16/16 exact
  targets, 16/16 implementation Top-3, 14/16 test Top-5 and stable explicit
  completeness; preserve M17 24/24 and M19 147/147 exact gates.

## 0.19.0 — 2026-08-27

- Add a dependency-free, read-only browser UI bound strictly to loopback for all
  six query types, exact target qualification, registration relationships,
  resumable references, provenance, freshness, and explicit truncation.
- Protect each UI session with a random token, strict Host/Origin checks, bounded
  JSON input, no CORS, fixed packaged assets, and restrictive browser headers.

## 0.18.0 — 2026-08-25

- Add opaque, HMAC-protected continuation tokens for node-budget-truncated exact
  TypeScript `references` results in MCP and `query-batch` sessions.
- Reuse the complete compiler-backed ordered tuple for later pages without
  rerunning the compiler; each page revalidates the Git source fingerprint.
- Bind tokens to one service session and the exact symbol/path/owner query, with
  explicit invalid, unavailable, mismatch and stale errors.
- Bound retained answers with a 16 MiB per-entry, 64 MiB total and 32-entry
  byte-weighted LRU; clear all state on close and never retain timed-out,
  incomplete or oversized answers.
- Preserve one-shot `query`, narrow answers, Python references and graph
  traversals unchanged and non-resumable.
- Pass independent Microsoft TypeScript wide-reference validation 5/5, product
  160/160, M17 24/24, M19 147/147, reproducible packaging and installed
  lifecycle gates.

## 0.17.0 — 2026-08-24

- Add exact Python `registers` relationships for a closed, source-proven set of
  Django, Flask, FastAPI, and Home Assistant registration APIs without executing
  target code or using name/string heuristics.
- Persist deterministic, source-bound registration evidence during
  `index`/`update`, reuse unchanged per-file records incrementally, and expose
  sidecar health and repair through existing maintenance commands.
- Add explicit `--relation registers` scope to callers/callees across CLI,
  JSON-lines, and MCP so a valid complete sidecar can answer without structural
  Provider startup; missing/stale evidence remains explicitly truncated.
- Publish the sidecar, verified project configuration, and Atlas index state as
  one prior-generation-safe transaction during first index, update, repair, and
  guided onboarding.
- Reduce the frozen large-development scoped query from about 16.87 seconds to
  about 82 milliseconds while preserving generic query behavior and budgets.
- Pass the new Zulip/Flask-Admin independent catalog 10/10, M17 24/24, M19
  147/147, M9–M12, reproducible packaging, and clean installed lifecycle gates.

## 0.16.1 — 2026-08-23

- Add authoritative argument arrays beside guided command strings so callers do
  not need to parse display text before executing a plan.
- Render Windows guidance as literal, replayable PowerShell commands, including
  paths and arguments containing shell metacharacters or apostrophes.
- Publish flat-download-compatible wheel checksums without a `dist/` prefix.

## 0.16.0 — 2026-08-23

- Add a read-only-first `onboard` plan and an explicit `--apply` workflow that
  composes runtime checks, visible project configuration, indexing, readiness,
  next-query guidance, MCP guidance, repair, and removal instructions.
- Preserve existing or conflicting configuration, reject unsafe path and file
  identity changes, and publish freshness state only for the exact indexed
  source generation.
- Add deterministic interruption-and-resume coverage plus real installed Flask,
  Vite, and custom-configuration acceptance flows.

## 0.15.0 — 2026-08-21

- Run the Python structural path concurrently with semantic/exact-reference
  evidence for callers, related tests, and upstream impact while retaining one
  main-thread Codebase Memory owner and one query-owned evidence worker.
- Preserve one shared wall deadline, structural partial results, complete worker
  joins, on-demand Provider startup, and exception/timeout cleanup.
- Reduce the frozen Django/Flask six-query median by 25.9%; reduce the widest
  Home Assistant case from 29.07 to 16.63 seconds.
- Preserve the M17 24/24 exact gate, M19 147/147 exact soak, and all historical
  product, oracle, and M9–M12 regressions.

## 0.14.0 — 2026-08-21

- Return non-empty exact TypeScript compiler references without redundant
  semantic Provider startup; retain semantic analysis as the empty-result
  fallback and preserve explicit node/time budgets.
- Add bounded 128-entry LRU caches for successful Python reference, exact-scan,
  and caller-supplement session results; never cache timed-out answers and clear
  all session caches on close.
- Reduce frozen Vite references from about 30.10 seconds with time truncation to
  0.415 seconds without truncation, and VS Code `URI.file` from 30.20 to 8.06
  seconds while preserving its exact 5,532-result node-budget contract.
- Reduce repeated Django Python references from about 1.15 seconds to 0.11
  milliseconds with stable answers and no measured post-cold RSS growth.

## 0.13.0 — 2026-08-21

- Add exact Python import/re-export-bound references and use them to supplement
  callers, related tests, and upstream impact without name-only edges.
- Add compiler-resolved TypeScript expression-assigned callers, direct callees,
  overload implementation selection, and external-helper suite attribution.
- Order broad related-test results across files before applying node budgets,
  while preserving explicit truncation.
- Cache successful Python reference/caller supplements within one read-only
  service session and prefilter AST parsing by target-symbol presence.
- Close all seven M17 completeness gaps: 24/24 independent cold cases and all
  504 cold/soak executions are exact with zero errors or unstable answers.

## 0.12.2 — 2026-08-21

- Publish Codebase Atlas under Apache License 2.0 with the license included in
  source and wheel distributions.
- Add public security, privacy, support, contribution, governance, conduct,
  changelog, issue/PR, and automated publication-readiness policies.

## 0.12.1 — 2026-08-21

- Require npm or an explicit TypeScript language server in `setup`, preventing a
  false-ready result before Serena semantic queries.
- Pass M17 independent validation on Django, Flask, Vite, and Zod, including 504
  cold/soak queries and four-repository maintenance/failure checks.

## 0.12.0 — 2026-08-21

- Add read-only `inspect [--deep]`, planned/applied repair, and dry-run-first
  root-contained cleanup.
- Add corruption, transient-failure, generation-retention, and recovery checks.

## 0.11.0 — 2026-08-21

- Add setup/runtime preflight, reproducible wheel assets, lifecycle acceptance,
  cross-platform CI, and private tag-based releases.

Earlier 0.x milestones established the six-query service, exact TypeScript test
mapping, identity-safe impact, bounded queries, owner disambiguation, exact Python
caller supplementation, and incremental/daily operations.
