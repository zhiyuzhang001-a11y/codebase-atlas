# Codebase Atlas

Local, explainable code intelligence built from proven provider components plus narrowly scoped gap providers.

The initial product phase follows the `BUILD_PROVIDER_GAPS` decision from the sibling evaluation workspace. Codebase Memory supplies broad structural graph facts, Serena supplies exact definitions and references, and this repository owns normalized contracts, exact TS/JS test mapping, identity-safe impact traversal, provenance policy, and product interfaces.

## Current scope

- local and read-only against user source;
- provider-neutral result and graph contracts;
- exact TS/JS Vitest/Jest callback mapping;
- explicit-depth impact traversal over stable identities;
- later CLI, JSON API, and read-only MCP over the same service.

UI, automatic source edits, cloud sync, and token-saving claims are out of scope.

## Verify

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The product CLI currently exposes exact `related-tests` and identity-safe `impact` queries. A read-only stdio MCP server uses the same `AtlasService` and owns the Codebase Memory daemon lifecycle, stopping only a daemon it started itself. Run `codebase-atlas mcp --help` for the isolated runtime arguments.
