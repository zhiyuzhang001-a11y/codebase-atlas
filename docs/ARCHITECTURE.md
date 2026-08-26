# Architecture v1

```text
Repository
  ├─ Codebase Memory adapter ─ broad definitions/calls
  ├─ Serena adapter ─ exact definitions/references
  ├─ Atlas TS test provider ─ exact test callback nodes/edges
  └─ Atlas-owned gopls ─ exact Go definitions/references/static calls/tests
             ↓
        Normalized Node/Edge contracts
             ↓
       Identity-safe local graph store
             ↓
     definitions/references/callers/callees/
          related-tests/impact service
             ↓
          CLI / JSON API / read-only MCP
```

Provider output is evidence, not truth by declaration. Exact and heuristic relationships remain distinguishable. Ambiguous endpoints are never silently merged by display name. User repositories are read-only; indexes and dependency runtimes live under an Atlas-owned data directory.

## Runtime dependencies

- TypeScript 5.9.3 (Apache-2.0), pinned by `package.json` and `pnpm-lock.yaml`, is used only for TS/JS syntax and symbol identity resolution.
- Codebase Memory and Serena remain external provider processes behind adapters; their data is normalized before entering the Atlas graph.
- Go 1.27.0 and gopls v0.23.0 are external, never auto-installed, and run with
  offline caches contained under the Atlas data root. A central language
  capability registry controls discovery, validation, Provider construction,
  indexing, lifecycle, and query dispatch.
