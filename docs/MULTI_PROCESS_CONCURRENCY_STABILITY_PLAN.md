# Multi-process MCP concurrency stability plan

## Scope and invariants

This work is limited to Codebase Atlas product source, tests, and isolated test
fixtures. It must not edit machine-local Atlas/Codex configuration, user-global
Provider settings, unrelated repositories, or target source outside temporary
fixtures.

A query succeeds only when it is bound to a fully published generation whose
source fingerprint is current. A refresh owned by another process is a wait or
retry condition, never permission to return stale or deleted facts as success.

## Phases and failure criteria

1. **Baseline and deterministic reproduction**
   - Inspect the refresh, MCP, registration, Provider transport, lifecycle, and
     status-reporting call chains.
   - Add a deterministic multi-process harness for simultaneous status/query,
     create-modify-delete, ownership transfer, process death, Provider lock
     contention, staging publication, and generation convergence.
   - Fail if the harness cannot reproduce or deterministically inject every
     reported boundary.
2. **Request and status safety**
   - Convert snapshot-wait exhaustion and refresh contention into structured,
     diagnostic tool results without terminating stdio MCP.
   - Report the configured automatic-update policy and include wall-clock wait
     time in request/refresh timings.
   - Fail on an uncaught request exception, false `isError`, policy mismatch, or
     unobserved wait.
3. **Cross-process generation coordination**
   - Bound all work by one exact-project lease, coalesce behind its owner, adopt
     the newly published manifest, and re-plan safely after owner death/failure.
   - Keep queries behind the reader lease and reject stale generation/source
     combinations before Provider access.
   - Fail on old-generation success after a completed modification or deletion.
4. **Transactional artifacts and Provider serialization**
   - Give staging/backup/journal artifacts transaction-unique ownership; publish
     atomically; roll back and clean up only owned paths.
   - Serialize shared Provider access across independent Atlas MCP processes and
     configure Atlas-managed cache sessions to disable Provider watching without
     changing global configuration.
   - Fail on missing-temp races, foreign cleanup, Provider lock timeout, or a
     watcher enabled in an Atlas-managed session.
5. **Automated regression and stress**
   - Run unit, integration, fault-injection, process-crash, and multi-process MCP
     tests. Successful runs must remove their children and temporary artifacts;
     failed runs must retain a diagnostic ledger.
   - Run at least 10 consecutive stress rounds. Every round requires live MCPs,
     no uncaught exception/Provider timeout/temp race, no deleted-node hit,
     correct modified generation, matching policy, and wall timing covering wait.
6. **Exact-root acceptance**
   - On one isolated target repository, require exact identity, `doctor=ready`,
     `index=fresh`, deep Provider health, a real target-symbol hit, and a
     wrong-project miss.
   - Verify no source/unrelated-config pollution and no Atlas/Provider test
     process remains.

Completion is reported only when every gate passes. Stress evidence establishes
the tested boundary and zero observed failures; it is not a claim that failures
are impossible outside that boundary.
