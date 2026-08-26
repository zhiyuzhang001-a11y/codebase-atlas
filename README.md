# Codebase Atlas

Local, explainable code intelligence built from proven provider components plus narrowly scoped gap providers.

Current release line: **0.18.0**, adding resumable exact TypeScript reference
pages inside one MCP or JSON-lines batch session while retaining the 0.17 exact
Python registration and transactional onboarding workflow.

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
- exact source-proven Python `registers` edges with an explicit closed-relation
  query scope;
- deterministic persistent/incremental Python registration evidence published
  transactionally with configuration and index state;
- read-only index/storage inspection, explicit repair, and dry-run-first cleanup;
- large-repository and monorepo subproject support;
- bounded same-session continuation for wide exact TypeScript references;
- one shared six-query service exposed by CLI, JSON-lines batch API, read-only
  MCP, and a dependency-free loopback browser UI.

Automatic source edits, remote UI access, and cloud sync remain out of scope.

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
codebase-atlas ui                    # local read-only graph UI; Ctrl-C stops it

# After source changes:
codebase-atlas update
codebase-atlas doctor

# Ask only for complete exact Python registration relationships:
codebase-atlas query callers my_view --relation registers \
  --target-path package/views.py --target-owner my_view

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

`ui` binds only to `127.0.0.1`, opens a session-token-protected browser page,
and reuses the same index and query service. Use `ui --no-open` in headless
environments. It does not expose source contents or perform index/config writes.

When source and Provider storage are already current, `update` takes an
Atlas-owned fast path without starting the Provider. Configured queries expose
index status and default to a warning when evidence may be stale; use
`--stale-policy error` for strict automation.

The structural Provider chooses an incremental, no-op, or safe full-rebuild
route. Atlas records freshness only after successful publication and preserves
the previous state when an update fails or the repository changes mid-run.

For Python repositories, `index`, stale/forced `update`, repair, and guided
onboarding also build a source-bound registration sidecar. A scoped
`--relation registers` callers/callees query treats that validated sidecar as
complete for the documented exact API set and does not start the structural
Provider. Generic callers/callees retain their normal merged behavior. If the
sidecar is unavailable or stale, the scoped request returns explicit
`registration_index_unavailable` truncation instead of scanning source or
silently returning an incomplete answer.

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
`related_tests`, and `impact`. Use `codebase-atlas ui --help` for the visual
interface, `codebase-atlas query --help` for one query,
`query-batch --help` for a reusable JSON-lines session, or `mcp --help` for the
read-only stdio MCP server. All interfaces share `AtlasService` and stop
only provider processes they started themselves. In MCP and `query-batch`, a
budget-truncated exact TypeScript `references` answer can expose an opaque
same-session continuation token; one-shot `query` behavior is unchanged.

For installation, project initialization, indexing, MCP configuration, upgrade,
and removal, see [`docs/LOCAL_USAGE.md`](docs/LOCAL_USAGE.md).
