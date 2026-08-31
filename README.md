# Codebase Atlas

Local, explainable code intelligence built from proven provider components plus narrowly scoped gap providers.

Current stable release: **0.21.0**, adding fail-closed automatic Codex project
discovery, safe migration from a fixed global Atlas registration, bounded
session-start index freshness and notify-only software update awareness while
adding a shared multi-project Provider layout, bounded large-repository indexing
and memory-aware global scheduling, while retaining the task-oriented Change
Brief, lightweight local UI,
resumable exact TypeScript references, exact Python registration relationships,
and transactional onboarding.

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
- one shared six-query service plus a bounded `analyze_change` Change Brief,
  exposed by CLI, JSON-lines batch API, read-only MCP, and a dependency-free
  loopback browser UI;
- dry-run-first Codex MCP registration that refuses to overwrite or remove a
  different existing entry, plus automatic cwd-based project discovery, a
  project-scoped managed block, exact active-project status, bounded
  session-start index refresh, and non-blocking notify-only release awareness.

Automatic source edits, remote UI access, and cloud sync remain out of scope.

Recall remains non-exhaustive for dynamic/runtime-only relationships. Broad
TypeScript queries and very large semantic queries can still reach explicit
budgets. See [`SUPPORT.md`](SUPPORT.md) for the precise support boundary.

## Project policies

- [Local use and data removal](docs/LOCAL_USAGE.md)
- [Task-oriented Change Brief contract](docs/CHANGE_BRIEF.md)
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

## Deploy with Codex

The canonical agent workflow is
[`docs/CODEX_DEPLOYMENT_RULES.md`](docs/CODEX_DEPLOYMENT_RULES.md). It requires
stable GitHub Release assets and checksums, reuses a valid machine installation,
creates isolated project configuration/index data, and verifies repository
identity, health, freshness and one real query before success.

From another project, ask Codex:

> Follow the Codebase Atlas deployment rules at
> https://github.com/zhiyuzhang001-a11y/codebase-atlas/blob/main/docs/CODEX_DEPLOYMENT_RULES.md
> and deploy Codebase Atlas for the current repository.

For phrase-only requests in every repository, add a short global `AGENTS.md`
instruction that points to this canonical URL. Codex reads global and
project-level `AGENTS.md` when a new task starts; the GitHub file itself is not
automatically discovered by unrelated repositories.

## Daily workflow

```bash
codebase-atlas onboard              # read-only first-project plan
codebase-atlas onboard --apply      # explicit config/index/doctor workflow
codebase-atlas setup
codebase-atlas init
codebase-atlas index
codebase-atlas doctor
codebase-atlas ui                    # local read-only graph UI; Ctrl-C stops it
codebase-atlas analyze-change LocalUiServer.close \
  --target-path src/codebase_atlas/web_ui.py --intent change_behavior
codebase-atlas codex plan            # no configuration write
codebase-atlas codex plan --scope global-auto
codebase-atlas codex plan --scope project

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
Shared-layout Atlas sessions for different repositories reuse one Provider
daemon and may query or index concurrently. Same-project writes remain safely
serialized; daily indexes adapt to observed memory, and large indexes use one
bounded exclusive slot while queries remain available. Legacy-layout projects
still return explicit `provider_busy` instead of waiting indefinitely.

When source and Provider storage are already current, `update` takes an
Atlas-owned fast path without starting the Provider. Configured queries expose
index status and default to a warning when evidence may be stale; use
`--stale-policy error` for strict automation.

The structural Provider chooses an incremental, no-op, or safe full-rebuild
route. Atlas records freshness only after successful publication and preserves
the previous state when an update fails or the repository changes mid-run.

For ordinary Codex switching across repositories, preview and then explicitly
apply the automatic global transport once:

```bash
codebase-atlas codex plan --scope global-auto
codebase-atlas codex apply --scope global-auto
```

The plan recognizes the exact older Atlas registration fixed to one repository.
Apply replaces only that recognized entry, verifies the new transport through
Codex, and restores the old entry if either add or verification fails. A foreign
or ambiguous entry is refused. The automatic server searches only from its
startup working directory to the innermost Git root (or only that directory for
non-Git work), rejects symlink roots, multiple configurations, and repository
identity mismatches, and never guesses from siblings, descendants, or recent
projects. When no safe project is available, `project_status` stays available
and every code query returns a structured unavailable status instead of using
another repository.

Start a new Codex task after applying because existing tasks do not hot-reload
MCP configuration. Index each repository once with `codebase-atlas onboard
--apply` or `codebase-atlas index`; later task starts can perform the bounded
freshness update. Use `project_status` to confirm the exact repository.

For a deliberate per-project override, preview and explicitly apply project
scope. It takes precedence when Codex loads that trusted project:

It writes only an Atlas-managed block in the repository's
`.codex/config.toml`, preserves unrelated valid TOML bytes, and never changes
the global MCP entry:

```bash
codebase-atlas codex plan --scope project
codebase-atlas codex apply --scope project
```

If the Codex project root is an ancestor of the indexed repository, pass it
explicitly to both commands, for example
`--codex-project-root /path/to/workspace`. Codex loads project configuration
only for a trusted project. Trust the folder when Codex first opens it; Atlas
does not edit the global trust list.

Each new Codex task rooted at that trusted repository starts the matching Atlas
server and performs one bounded freshness gate. A fresh index does not start the
Provider; a stale index is updated when the Provider is available. Contention,
timeout, or update failure starts MCP with the prior index and an explicit
status. The `project_status` MCP tool reports the resolved repository, project,
config, refresh result, and notify-only software update state. Release checks
run asynchronously, use a 24-hour cache, and never install software. Set
`CODEBASE_ATLAS_NO_UPDATE_CHECK=1` to disable them. Because the managed config
contains machine-local absolute paths, do not commit it.

Use `project_status` in a newly opened task as the authoritative verification.
The `codex mcp get` management command may show only the global layer and is not
proof that a project-local override was or was not loaded.

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

The product MCP surface supports `project_status`, `definition`, `references`,
`callers`, `callees`, `related_tests`, `impact`, and the shared
`analyze_change` composition. Use
`codebase-atlas analyze-change --help` for an actionable Change Brief,
`codebase-atlas codex plan --help` for a read-only Codex integration preview,
or `codebase-atlas ui --help` for the visual
interface, `codebase-atlas query --help` for one query,
`query-batch --help` for a reusable JSON-lines session, or `mcp --help` for the
read-only stdio MCP server. All interfaces share `AtlasService` and stop
only provider processes they started themselves. In MCP and `query-batch`, a
budget-truncated exact TypeScript `references` answer can expose an opaque
same-session continuation token; one-shot `query` behavior is unchanged.

For installation, project initialization, indexing, MCP configuration, upgrade,
and removal, see [`docs/LOCAL_USAGE.md`](docs/LOCAL_USAGE.md).
