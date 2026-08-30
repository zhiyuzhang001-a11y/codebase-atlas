# Architecture v1

```text
Repository
  ├─ Codebase Memory adapter ─ broad definitions/calls
  ├─ Serena adapter ─ exact definitions/references
  └─ Atlas TS test provider ─ exact test callback nodes/edges
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

## Shared structural Provider

Shared-layout projects use one versioned account-level Codebase Memory root.
Atlas derives a deterministic Provider project identity from each canonical
repository path and supplies that exact repository as the session boundary.
Unrelated projects may execute concurrently; same-project mutations remain
serialized. Daily indexes use adaptive memory-aware admission. Large indexes use
the complete bounded two-pass pipeline and an exclusive index slot, while query
capacity remains available. Existing legacy caches are never adopted or deleted
implicitly; migration is an explicit rebuild with rollback and preservation.
