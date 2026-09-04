# Codebase Atlas daily-use playbook

## Start a new project safely

Run `codebase-atlas onboard --repo /path/to/repository` first. The default is a
read-only plan: it writes no configuration or index, starts no Provider, and
does not install Node.js or Provider dependencies. Review its checks and exact
actions, then rerun the reported command with `--apply` when ready. A completed
project can be rerun safely; current state returns without Provider work.

## Start with a narrow identity

Check operational state first. A source-current update uses the Atlas fast path
and does not start the structural Provider:

```bash
codebase-atlas update
codebase-atlas doctor
```

Configured queries default to `--stale-policy warn`. Use
`--stale-policy error` in CI or other workflows where stale evidence must be
rejected. Batch and MCP sessions capture state at startup to preserve warm-query
latency; restart them after editing. They do not update the index automatically.

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

For normal AI-assisted work, prefer one Change Brief instead of manually
orchestrating six primitive queries:

```bash
codebase-atlas analyze-change LocalUiServer.close \
  --target-path src/codebase_atlas/web_ui.py \
  --intent change_behavior
```

The `Owner.member` form is an explicit shorthand, not fuzzy search. If the
definition is unresolved or ambiguous, the analysis stops and asks for a more
precise path/owner. Read the returned `recommended_reads`, inspect every
`completeness` entry, then run only evidence-backed tests.

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

For an exact TypeScript `references` result in MCP or `query-batch`, follow
`truncation.continuation` while `resumable` is true. Repeat the same symbol,
target path and owner; `max_nodes` controls the next page size. Tokens work only
inside the session that created them.

Only `resolution: exact` graph edges are returned by default. Low-confidence
name-only Provider guesses are excluded rather than presented as facts.

## Reuse one session

For several questions in one repository, prefer `query-batch` or MCP so Provider
startup and exact graph traversals can be reused. Repeated complete narrow graph
queries should normally return from the in-session cache.

Run `codebase-atlas doctor` after installation or configuration changes. Atlas is
read-only: it should leave repository source and global editor/MCP settings
unchanged, and Provider processes should stop with the session.

The Provider daemon is account-global, but shared-layout Atlas sessions retain
exact per-project identity and can remain active for different repositories.
Same-project writes still serialize. Daily indexes use bounded adaptive slots;
large indexes queue in one exclusive index lane while queries remain available.
Legacy-layout sessions can still report `provider_busy` until explicitly
migrated.

## Connect Codex safely

For automatic project selection across normal Codex projects, preview and apply
the fail-closed global transport once:

```bash
codebase-atlas codex plan --scope global-auto
codebase-atlas codex apply --scope global-auto
```

Then start a new task in the repository and call `project_status`. Atlas never
falls back to another repository: an unconfigured or unsafe directory exposes
only its structured status until that repository is configured and indexed.
Existing tasks do not hot-reload the changed MCP transport.

For a deliberate project-local override, preview the MCP block first:

```bash
codebase-atlas codex plan --scope project \
  --config /path/to/.codebase-atlas.toml
```

If Codex opens a parent workspace instead of the indexed repository itself,
also pass `--codex-project-root /path/to/workspace`. Trust that project through
Codex's normal first-open prompt; Atlas intentionally does not change global
trust settings.

The plan reports the exact repository, target `.codex/config.toml`, full stdio
command/arguments, existing-entry state, managed block, project rule, and
verification steps. It performs no write and never changes global Codex state.
Apply or remove only with the explicit subcommand:

```bash
codebase-atlas codex apply --scope project --config /path/to/.codebase-atlas.toml
codebase-atlas codex remove --scope project --config /path/to/.codebase-atlas.toml
```

Apply preserves unrelated valid TOML bytes and refuses a same-name foreign
transport, invalid config, symlink, or repository mismatch; remove touches only
the exact Atlas-managed block. Do not commit the generated machine-specific
absolute paths. Codex does not hot-load a new MCP into an already running task,
so start a new task rooted at the project, call `project_status` to verify the
exact repository and session-start freshness result, then run one real
`analyze_change` call. The automatic refresh checks again before each Atlas code
query, calls the Provider only after relevant changes, and does not run a
permanent watcher. The software check only notifies and never installs.

Do not use `codex mcp get` alone as the project-switching test: it may expose
only the global management layer. The in-task `project_status` result is the
authoritative check.

The legacy single global registration is still available with `--scope global`,
but it remains fixed to one repository and is not recommended for project
switching.
