# Codebase Atlas daily-use playbook

## Start with a narrow identity

Use the repository configuration and ask for a definition first:

```bash
codebase-atlas query definition solve_dependencies \
  --target-path fastapi/dependencies/utils.py
```

For a method in a file that contains other methods with the same name, always
add its owner:

```bash
codebase-atlas query definition fire \
  --target-path src/vs/base/common/event.ts \
  --target-owner Emitter
```

The stable selector is `target_path + target_owner + symbol`. `target_owner` is
optional for module-level functions and unique declarations.

## Follow a maintenance question

- Before changing a contract: run `definition`, then `references`.
- Before changing behavior: run `callers` and upstream `impact --depth 2`.
- Before refactoring internals: run `callees`.
- Before editing TypeScript code: run `related_tests`; exact compiler references
  also include tests excluded by production tsconfig files.

Example:

```bash
codebase-atlas query impact dispose \
  --target-path src/vs/base/common/lifecycle.ts \
  --target-owner DisposableStore \
  --direction upstream --depth 2 \
  --max-nodes 50 --max-edges 100 --timeout-ms 30000
```

## Read the completion contract

Treat `truncated: false` as complete within the requested scope. When
`truncated: true`, use `truncation.reasons`, `observed`, and `returned` to decide
whether the partial result is sufficient. A time- or node-budget result is usable
evidence, but not proof that no additional callers or tests exist.

Only `resolution: exact` graph edges are returned by default. Low-confidence
name-only Provider guesses are excluded rather than presented as facts.

## Reuse one session

For several questions in one repository, prefer `query-batch` or MCP so Provider
startup and exact graph traversals can be reused. Repeated complete narrow graph
queries should normally return from the in-session cache.

Run `codebase-atlas doctor` after installation or configuration changes. Atlas is
read-only: it should leave repository source and global editor/MCP settings
unchanged, and Provider processes should stop with the session.
