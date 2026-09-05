# Multi-process MCP concurrency stability results

## Outcome

The concurrency hardening gates passed on the supported macOS ARM64 Provider
release. The verified boundary had zero observed failures; this is not a claim
that failures are impossible outside the tested workloads and time budgets.

## Correctness changes

- Recovery and staging cleanup now run only while holding the exact-project
  refresh lease. Registration staging names are transaction-unique and rollback
  refuses to replace a publication owned by a later transaction.
- Refresh followers wait within the caller deadline, adopt only a newly
  published generation, and retry when an owner failed without advancing the
  generation. Queries never treat automatic-refresh failure as stale success.
- Snapshot waits, Provider contention, and unexpected request exceptions return
  structured diagnostics without terminating the MCP stdio loop.
- Shared Provider frontend startup is globally admitted. Atlas-managed Provider
  caches disable both watcher settings, and structural queries use the same
  generation-bound managed transport instead of a separate CLI session.
- Provider admission consumes the caller's declared query budget; the removed
  two-second internal cap had produced `provider_busy` empty results under
  healthy multi-client startup contention.
- Generation switches clear in-memory query state and reconnect a started
  Provider frontend before it serves the new generation.

## Executed gates

- Full unit/regression discovery: 329 tests passed and 14 environment-gated
  tests skipped, including the added transport and failed-owner regression
  cases.
- Real refresh integration: rollback, mutation, rename, deletion, two-project
  parallelism, and performance cases passed against Provider
  `0.10.8-atlas.2+e088a41b`.
- Same-repository stress: 10 consecutive rounds passed with four independent MCP
  processes. Each round covered three concurrent create/modify/delete writers,
  an observer, generation convergence, deleted-symbol absence, and staging
  cleanup. Maximum measured owner wait in the final run was about 7.38 seconds.
- Crash recovery: a refresh owner was killed after journal publication; another
  MCP recovered the transaction, refreshed, and later removed the injected
  symbol without residue.
- Three independently operated Agent workers passed against one exact test
  repository. All reported the same create, modify, and delete generations and
  independently verified the baseline target hit and unrelated-project miss.
- Exact-root acceptance passed: repository identity matched, `doctor=ready`,
  `index=fresh`, deep Provider integrity was healthy, target hit was exact,
  foreign hits were zero, and `auto_watch=false` plus
  `watcher_enabled=false` were confirmed in the isolated Atlas-managed cache.

## Operational tradeoff

After another process publishes a generation, each long-lived MCP reconnects its
Provider frontend under the global admission lock. This adds bounded latency at
generation transitions but prevents a healthy startup queue from being reported
as a successful empty answer. Warm queries within one generation continue to
reuse the existing frontend.
