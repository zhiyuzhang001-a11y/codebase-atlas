# MCP freshness contract

This candidate prioritizes current-source verification over the former cached,
no-scan warm-query latency requirement. The user approved this tradeoff after
reviewing approximately 30–80 ms query overhead on 500–2000-file synthetic
repositories. The former POSIX p95 <=2.022 ms test is superseded for this MCP
path; its results and the original stale-evidence failure remain historical
records, not passing results under that old gate.

## Query admission

Under the existing shared generation lease, MCP checks repository identity and
source freshness before each query, even when the published generation is
unchanged. Existing file changes, deletion, rename and new untracked files
must be detected on the next query. No TTL may relabel previous source facts
as fresh. Index artifacts remain bound to their published generation.

With `--stale-policy error`, stale or mismatched index state must reject facts
before invoking the query service. Other policies retain their existing
warning/ignore behavior; detection does not silently strengthen those policies.
Revalidation does not refresh the Provider index or reset generation caches.
Batch sessions keep their documented startup-snapshot behavior.

## Validation and latency

Required regressions cover immediate, three-second and newly created-session
stale detection; rejection of old/foreign facts; add/delete/rename; restoration
and unchanged queries without Provider refresh, cache reset or artifact writes.

Measure warmed repeated queries using bounded synthetic repositories and record
Git invocation/worktree-check time, changed-file hashing, total latency and
Provider calls. Compare optimizations in alternating order on the same fixture.
The current measurements support the tested workloads, not a universal 80 ms
ceiling or a large-repository latency guarantee. The former no-scan gate is not
a release blocker for this approved correctness-first contract.

The candidate does not add a permanent watcher or background framework. Git
snapshot work is not newly bounded by the entire query timeout; disclose this
limit rather than implying that the shared-lease timeout covers every phase.

## Adoption boundary

This source candidate must first be integrated and reviewed in an authorized
development checkout, preserving unrelated changes. Passing these checks does
not authorize updating an existing installation, changing global MCP settings,
reindexing user projects, publishing a release or claiming cloud-build parity.
