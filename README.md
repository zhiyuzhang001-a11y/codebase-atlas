# Codebase Atlas

Local, explainable code intelligence built from proven provider components plus narrowly scoped gap providers.

Current release: **0.11.0**, promoted as a private installable release after validation on
FastAPI, Home Assistant, NestJS, VS Code, and the product repository itself.

Codebase Memory supplies broad structural graph facts, Serena supplies exact
definitions and references, and this repository owns normalized contracts,
exact TS/JS test mapping, identity-safe impact traversal, exact Python caller
supplementation, provenance policy, query budgets, index freshness, and product
interfaces.

## Current scope

- local and read-only against user source;
- provider-neutral result and graph contracts;
- Python and TypeScript/JavaScript repositories;
- exact TS/JS Vitest/Jest callback mapping;
- path and owner-qualified identity for same-name symbols and members;
- explicit-depth impact traversal over stable identities;
- bounded node, edge, and time budgets with explicit partial-result metadata;
- Git-backed `fresh`/`stale` index diagnosis and safe Provider-managed updates;
- large-repository and monorepo subproject support;
- one shared six-query service exposed by CLI, JSON-lines batch API, and read-only MCP.

UI, automatic source edits, and cloud sync remain out of scope.

## Daily workflow

```bash
codebase-atlas setup
codebase-atlas init
codebase-atlas index
codebase-atlas doctor

# After source changes:
codebase-atlas update
codebase-atlas doctor
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
