# Codebase Atlas

Local, explainable code intelligence built from proven provider components plus narrowly scoped gap providers.

The initial product phase follows the `BUILD_PROVIDER_GAPS` decision from the sibling evaluation workspace. Codebase Memory supplies broad structural graph facts, Serena supplies exact definitions and references, and this repository owns normalized contracts, exact TS/JS test mapping, identity-safe impact traversal, provenance policy, and product interfaces.

## Current scope

- local and read-only against user source;
- provider-neutral result and graph contracts;
- exact TS/JS Vitest/Jest callback mapping;
- explicit-depth impact traversal over stable identities;
- one shared six-query service exposed by CLI, JSON-lines batch API, and read-only MCP.

UI, automatic source edits, cloud sync, and token-saving claims are out of scope.

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
