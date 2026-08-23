# Codebase Atlas

Local, explainable code intelligence built from proven provider components plus narrowly scoped gap providers.

Current local candidate: **0.16.1**, retaining read-only-first guided onboarding
while adding authoritative argument arrays, PowerShell-safe command replay, and
flat-download-compatible release checksums. External CI and release remain
separate owner actions.

Codebase Memory supplies broad structural graph facts, Serena supplies exact
definitions and references, and this repository owns normalized contracts,
exact TS/JS relation and test mapping, identity-safe impact traversal, exact
Python import/re-export reference and caller supplementation, provenance policy,
query budgets, index freshness, and product interfaces.

## Current scope

- local and read-only against user source;
- provider-neutral result and graph contracts;
- Python and TypeScript/JavaScript repositories;
- exact TS/JS Vitest/Jest callback mapping;
- path and owner-qualified identity for same-name symbols and members;
- explicit-depth impact traversal over stable identities;
- bounded node, edge, and time budgets with explicit partial-result metadata;
- Git-backed `fresh`/`stale` index diagnosis and safe Provider-managed updates;
- read-only index/storage inspection, explicit repair, and dry-run-first cleanup;
- large-repository and monorepo subproject support;
- one shared six-query service exposed by CLI, JSON-lines batch API, and read-only MCP.

UI, automatic source edits, and cloud sync remain out of scope.

Recall remains non-exhaustive for dynamic/runtime-only relationships. Broad
TypeScript queries and very large semantic queries can still reach explicit
budgets. See [`SUPPORT.md`](SUPPORT.md) for the precise support boundary.

## Project policies

- [Local use and data removal](docs/LOCAL_USAGE.md)
- [Privacy and local data](PRIVACY.md)
- [Security policy](SECURITY.md)
- [Support and compatibility](SUPPORT.md)
- [Contributing](CONTRIBUTING.md) and [code of conduct](CODE_OF_CONDUCT.md)
- [Governance](GOVERNANCE.md), [changelog](CHANGELOG.md), and
  [release process](docs/RELEASING.md)
- [Public release checklist](docs/PUBLIC_RELEASE_CHECKLIST.md)

Codebase Atlas is licensed under the [Apache License 2.0](LICENSE). Bundled and
separately installed dependencies retain their own licenses; see
[third-party notices](THIRD_PARTY_NOTICES.md).

## Daily workflow

```bash
codebase-atlas onboard              # read-only first-project plan
codebase-atlas onboard --apply      # explicit config/index/doctor workflow
codebase-atlas setup
codebase-atlas init
codebase-atlas index
codebase-atlas doctor

# After source changes:
codebase-atlas update
codebase-atlas doctor

# Diagnose or maintain Atlas-owned data:
codebase-atlas inspect --deep
codebase-atlas repair                 # plan only
codebase-atlas repair --apply         # explicit mutation
codebase-atlas clean                  # dry run
codebase-atlas clean --apply          # exact planned targets only
```

`setup` is a read-only preflight: it executes version/import probes and returns
machine-readable remediation without installing software or changing project,
editor, or MCP configuration.

When source and Provider storage are already current, `update` takes an
Atlas-owned fast path without starting the Provider. Configured queries expose
index status and default to a warning when evidence may be stale; use
`--stale-policy error` for strict automation.

The structural Provider chooses an incremental, no-op, or safe full-rebuild
route. Atlas records freshness only after successful publication and preserves
the previous state when an update fails or the repository changes mid-run.

`inspect` validates Provider schema and identity without changing data; `--deep`
adds SQLite `quick_check` and can take longer on large indexes. `repair` is
read-only unless `--apply` is supplied and delegates publication/quarantine to
the Provider's staged atomic boundary. `clean` retains the current database and
newest previous quarantine/log generation, refuses symlinks or escaped paths,
and applies only the exact file identities reported by its dry run.

## Verify

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The product service supports `definition`, `references`, `callers`, `callees`,
`related_tests`, and `impact`. Use `codebase-atlas query --help` for one query,
`query-batch --help` for a reusable JSON-lines session, or `mcp --help` for the
read-only stdio MCP server. All three interfaces share `AtlasService` and stop
only provider processes they started themselves.

For installation, project initialization, indexing, MCP configuration, upgrade,
and removal, see [`docs/LOCAL_USAGE.md`](docs/LOCAL_USAGE.md).
