# Changelog

All notable release changes are recorded here. The package uses semantic versions;
0.x minor versions may still refine interfaces within the documented support
boundary.

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
