# Changelog

All notable private-release changes are recorded here. The package uses semantic
versions; until a public compatibility policy is approved, pre-public 0.x minor
versions may refine interfaces.

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
