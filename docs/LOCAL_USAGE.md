# Local installation and daily use

For the task-oriented workflow and result interpretation, also see
`docs/DAILY_USE_PLAYBOOK.md`.

## Requirements

- Python 3.11 through 3.14
- Node.js 18 or newer, with npm available (or an explicit
  `typescript-language-server`) for TypeScript semantic queries
- a local Codebase Memory executable
- a Python environment with Serena installed

Atlas does not edit global MCP/editor configuration unless the user explicitly
runs global-scope `codebase-atlas codex apply`; `codex plan` is read-only and
shows the exact change first. The recommended multi-project form uses explicit
`--scope project`, which writes only a managed block under the selected
repository. Provider caches are stored
under `~/.local/share/codebase-atlas/` by default and user source files remain
unchanged.
The wheel includes the pinned TypeScript 5.9.3 runtime used by the exact test
analyzer; it does not depend on a target repository's `node_modules` for that API.
The Serena semantic provider separately needs either npm to provision its pinned
managed language server or an existing `typescript-language-server` executable.
`setup` treats the absence of both as a required failure.

Run the read-only preflight before initialization:

```bash
codebase-atlas setup \
  --repo /path/to/repository \
  --serena-python /path/to/serena-venv/bin/python
```

The JSON result validates executable versions and Serena import capability and
provides a remediation command for every required failure. It never installs a
runtime or changes project, editor, or MCP configuration. Once a project config
exists, `codebase-atlas setup --config /path/to/.codebase-atlas.toml` validates
the exact recorded paths.

## Guided onboarding

For a single ordered first-project plan, use `onboard`. Without `--apply` it is
strictly read-only: it creates no configuration or data, starts no Provider, and
never installs Node or external Providers.

```bash
codebase-atlas onboard --repo /path/to/repository
```

It reports missing local prerequisites and both an exact apply command and its
authoritative `apply_argv` argument array. `command_shell` identifies the display
syntax (`posix` or `powershell`); automation should execute `apply_argv` directly
instead of parsing `apply_command`. The `guidance_argv` object provides the same
structured form for subsequent query, MCP, repair, and removal commands. On
Windows, displayed commands are PowerShell-safe and can be copied as shown.
When all required runtimes are discoverable or explicitly supplied, apply only
the shown plan:

```bash
codebase-atlas onboard --repo /path/to/repository --apply
```

`--apply` may create the visible project configuration and Atlas-owned index
state, then runs the existing index/doctor flow. It never overwrites a differing
configuration; rerunning a fresh project reports `current` without Provider work.
For TypeScript monorepos, provide the project boundary explicitly:

```bash
codebase-atlas onboard --repo /path/to/repository --language typescript \
  --tsconfig packages/app/tsconfig.json --apply
```

## Install

From a checkout:

```bash
python3.12 -m pip install .
```

For an isolated editable development install:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
```

## Initialize and index a repository

If Node and Codebase Memory are on `PATH`, and the Serena Python is exported,
initialization can be short:

```bash
export ATLAS_SERENA_PYTHON=/path/to/serena-venv/bin/python
cd /path/to/repository
codebase-atlas init
codebase-atlas index
codebase-atlas doctor
```

After the first build, use the explicit daily update flow:

```bash
codebase-atlas doctor
codebase-atlas update
codebase-atlas doctor
```

When the recorded source fingerprint and index mode already match and the
Provider database is present, `update` returns `status=current` without starting
the Provider. To bypass this Atlas fast path for diagnosis or Provider-level
maintenance, run:

```bash
codebase-atlas update --force-provider
```

`doctor` reports the index as `fresh`, `stale`, `unknown`, or
`rebuild_required`. For Git repositories, Atlas fingerprints the commit plus
the exact contents of changed and untracked files, so repeated edits to an
already-dirty file are still detected. `update` delegates to Codebase Memory's
staged manifest-based router, which can take an incremental, no-op, or safe
full-rebuild path. Atlas deliberately reports this as `provider_managed`
because the Provider does not expose the selected internal route in its public
response.

The Atlas state marker is written atomically only after the Provider publishes
a successful index. If indexing fails, or the repository changes during the
operation, the previous state marker is preserved and another `update` is
required. Non-Git repositories remain queryable but freshness is reported as
`unknown`.

## Inspect, repair, and clean Atlas data

Inspection is read-only. The default checks SQLite schema and project identity;
the deep form also runs `quick_check` and may take longer for a large database:

```bash
codebase-atlas inspect
codebase-atlas inspect --deep
```

Repair also defaults to a read-only plan. It distinguishes a stale Atlas state,
a missing/invalid/corrupt Provider database, and a transient unavailable/locked
database. Transient failures are never treated as corruption. Apply a proposed
repair explicitly:

```bash
codebase-atlas repair
codebase-atlas repair --apply
```

The Provider builds and verifies a staged generation before atomic publication;
Atlas advances its state marker only after success. A failed repair leaves that
marker unchanged, and Provider rollback/quarantine preserves the previous live
generation according to its recovery boundary.

Cleanup is dry-run first and only recognizes obsolete Atlas temporary state,
orphan Provider staging files, older quarantine generations, and older rotated
logs under the configured Atlas data root:

```bash
codebase-atlas clean
codebase-atlas clean --apply
```

The apply form rechecks device, inode, modification time, size, regular-file
type, and root containment before removing anything. It retains the live
database, pending recovery files, the newest quarantine generation, current
logs, and the newest rotated log. Any refused/symlink/escaped target blocks the
entire apply operation.

Configured queries expose the current index state. The default `warn` policy
returns results plus a machine-readable `index_not_current` warning when the
source is stale. Use the strict policy when stale results must never enter an
automation, or explicitly ignore warnings for compatibility:

```bash
codebase-atlas query definition MyClass --stale-policy error
codebase-atlas query-batch --stale-policy warn
codebase-atlas mcp --stale-policy ignore
```

For a single task-oriented result, use:

```bash
codebase-atlas analyze-change Class.method \
  --target-path package/module.py --intent fix_bug
```

The response distinguishes unresolved/ambiguous identity from partial evidence,
preserves every subquery's truncation, and lists evidence-backed source/test
targets. Shared-layout UI, MCP and query sessions for different repositories may
reuse the same Provider daemon concurrently. Same-project writes remain
serialized. A legacy-layout or short admission conflict returns
`provider_busy`; inspect and explicitly migrate that project instead of deleting
its old cache.

Long-lived batch and MCP sessions expose the state captured at session startup
without adding Git work to every warm query. A project-scoped M30 MCP transport
performs one bounded update before the service starts; the default/global and
older transports remain explicit-update only. Restart the session after editing,
or run `doctor`/`update` first. No permanent watcher or per-query Git scan is
added.

Otherwise provide the runtime locations once:

```bash
codebase-atlas init \
  --node /path/to/node \
  --cbm-binary /path/to/codebase-memory-mcp \
  --serena-python /path/to/serena-venv/bin/python \
  --node-bin-dir /path/containing/typescript-language-server
```

This creates `.codebase-atlas.toml`. It contains paths only; indexes and logs go
to the Atlas data directory. Add this configuration to version control only when
its paths are portable for the intended users.

### Project-maintained Codebase Memory build

Codebase Atlas does not require its Codebase Memory changes to be merged by the
upstream project. When using a project-maintained Provider bundle:

1. Verify the adjacent archive `.sha256` file and the executable digest recorded
   in `manifest.json`.
2. Keep the included MIT `LICENSE` with the executable.
3. Extract it into a versioned local directory and pass that executable through
   `--cbm-binary`; do not overwrite an unrelated global Provider installation.
4. Keep the previous verified bundle until the new version has indexed and
   queried successfully. Roll back by restoring the prior `cbm_binary` path;
   never delete an existing index merely to change Provider versions.

The manifest identifies the fork, upstream source, exact commit, managed version,
platform/architecture, reproducible build command, binary SHA-256 and validation
evidence. A new managed version is accepted only after two independent builds
produce identical binaries and the frozen M17/M19 gates pass from an installed
Codebase Atlas wheel. Upstream review remains useful feedback but is not an
installation or release dependency.

Public 0.21.0 assets use these target names: `linux-x86_64`, `linux-arm64`,
`macos-x86_64`, `macos-arm64`, `windows-x86_64`, and `windows-arm64`. Download
the archive and adjacent `.sha256` file for exactly one matching target from the
same Atlas Release. `PROVIDER_SHA256SUMS.txt` covers the complete set.

For TypeScript repositories, `--node-bin-dir` must contain
`typescript-language-server` when it is not beside the configured Node executable.

Large TypeScript monorepos often have no root `tsconfig.json`. Select the intended
subproject explicitly during initialization:

```bash
codebase-atlas init --language typescript --tsconfig packages/app/tsconfig.json \
  --serena-python /path/to/serena-venv/bin/python
codebase-atlas index
```

The selected config defines the compiler boundary used by exact test analysis.

## Query

After initialization and indexing, commands use the configuration automatically:

```bash
codebase-atlas query definition MyClass
codebase-atlas query references DEFAULT_PREFIX
codebase-atlas query callers render_user
codebase-atlas query callees render_user
codebase-atlas query related_tests render_user
codebase-atlas query impact render_user --direction upstream --depth 2
```

When a monorepo contains several declarations with the same name, identify the
intended declaration by repository-relative path. This option is available for
all six query types and through the MCP tool schemas:

```bash
codebase-atlas query references isUriComponents \
  --target-path src/vs/base/common/uri.ts
codebase-atlas query related_tests isUriComponents \
  --target-path src/vs/base/common/uri.ts
```

If one file contains several members with the same name, add the enclosing class
or object. This selects the stored qualified identity instead of merging every
`fire` declaration in the file:

```bash
codebase-atlas query callers fire \
  --target-path src/vs/base/common/event.ts \
  --target-owner Emitter
```

`target_owner` is also available in JSON-lines and MCP requests. A path-only
query remains compatible, but can intentionally return multiple definitions when
the file itself is ambiguous.

## Exact Python registration relationships

For a closed answer containing only exact callable-registration edges, scope a
Python callers or callees query to `registers`:

```bash
codebase-atlas query callers health_view --relation registers \
  --target-path app/views.py --target-owner health_view
codebase-atlas query callees registration@42 --relation registers \
  --target-path app/urls.py
```

The supported source-proven API identities are `django.urls.path`,
`django.urls.re_path`, `flask.Flask.route`, `flask.Flask.add_url_rule`,
`fastapi.FastAPI.add_api_route`, and
`homeassistant.helpers.dispatcher.async_dispatcher_connect`. Atlas resolves
constructor/import/callback identity statically; it does not execute the target
or infer relationships from strings or display names.

Python `index`, stale/forced `update`, applied repair, and guided onboarding
build `<data_dir>/python-registrations-v1.json`. The deterministic sidecar is
bound to the exact repository/project/source generation and is published with
verified configuration and Atlas state under one rollback boundary. `inspect`
reports its status under `python_registrations`; a missing/corrupt sidecar on an
otherwise current project can be rebuilt without starting the structural
Provider.

A validated sidecar is complete for explicit `relation=registers`, so that
scope skips structural Provider startup and performs no query-time repository
scan. Generic callers/callees continue to merge structural and registration
evidence. If scoped evidence is missing, corrupt, incompatible, or stale, the
response is explicitly truncated with `registration_index_unavailable`; it is
never presented as a complete empty answer.

For TypeScript projects, scoped `references` uses exact compiler-symbol
occurrences (including test files excluded by a production tsconfig) as the
authoritative answer for the selected project. The semantic Provider is started
only when the compiler returns no exact reference. Compiler or fallback timeout
still produces explicit truncation metadata.

All queries have explicit safety budgets. The defaults are 100 returned nodes,
200 evidence edges, and 30 seconds. Override them for a specific query when needed:

```bash
codebase-atlas query impact get_openapi --direction upstream --depth 2 \
  --max-nodes 50 --max-edges 100 --timeout-ms 10000
```

Every response includes `truncated` and `truncation`. A truncated response contains
valid partial evidence, but is not a complete answer. Its reasons can include
`node_budget_exceeded`, `edge_budget_exceeded`, `time_budget_exceeded`, or
`provider_result_limit`; the response also records limits, observed/returned counts,
elapsed time, and whether continuation is available. The current graph provider
cannot safely resume a partial traversal, so `continuation` is `null` and
`resumable` is `false` rather than implying that omitted results can be recovered.

Exact TypeScript `references` is the narrow exception in MCP and `query-batch`.
When the compiler has completed the full ordered answer and the returned page is
node-budget truncated, Atlas retains that tuple in a byte-bounded session cache
and returns an opaque `continuation` with `resumable: true`. Send the token with
the same symbol, `target_path`, and `target_owner`; a new `max_nodes` selects the
next page size. Each page revalidates the Git source fingerprint without rerunning
the compiler. The final page has a null continuation. Tokens are HMAC-protected,
expire when the session closes, and report explicit invalid, unavailable,
query-mismatch, or stale errors. One-shot `query`, Python references, partial or
timed-out results, and graph traversals remain non-resumable.

Within a long-lived JSON-lines or MCP session, Atlas caches exact definitions and
completed graph traversals, plus successful Python exact-reference and caller
supplements. Repeating the same query and budget avoids duplicate CBM,
semantic-reference, and AST work. Python supplement caches live only for the
read-only service session and are cleared on close; time-truncated supplements
and traversals are never cached. Graph caches also clear when the configured CBM
index database's modification fingerprint changes. TypeScript continuation
entries use a 16 MiB per-entry, 64 MiB total, 32-entry byte-weighted LRU and are
also cleared on close.

Atlas joins compatible shared-layout projects to one account-level Provider
daemon. Each session keeps an exact allowed root and deterministic project
identity, so unrelated repositories can query and index concurrently while
same-project writes remain serialized. Daily indexes use adaptive memory-aware
slots; large repositories use a bounded two-pass path and one exclusive index
slot while queries retain capacity. Legacy-layout projects continue using their
preserved per-project cache until an explicit migration. The short per-user
admission lock lives in `XDG_RUNTIME_DIR` or the system temporary directory.
Set `ATLAS_RUNTIME_DIR` only when a different runtime directory is required.
Atlas does not stop a daemon it did not start.

Use `--config /path/to/config.toml` when running outside the repository. The
long-lived JSON-lines interface is `codebase-atlas query-batch`; the read-only MCP
server is `codebase-atlas mcp`.

## MCP connection

For normal Codex use across several projects, preview and explicitly apply the
automatic global transport once:

```bash
codebase-atlas codex plan --scope global-auto
codebase-atlas codex apply --scope global-auto
```

It resolves only the MCP process startup directory and its ancestors up to the
innermost Git root. It does not search sibling or recently opened projects. A
missing, incomplete, invalid, mismatched, or ambiguous project keeps
`project_status` available and makes code queries fail with that structured
status. Applying can migrate only the exact older Atlas transport fixed to one
valid config; a foreign entry is refused and a failed migration restores and
verifies the old entry. Start a new Codex task after changing the registration.

Index each repository once before first use:

```bash
cd /absolute/path/to/repository
codebase-atlas onboard --apply
# or, when configuration already exists:
codebase-atlas index
```

Use project-local scope only when a repository needs a deliberate override:

```bash
codebase-atlas codex plan --scope project
codebase-atlas codex apply --scope project
```

When Codex opens a workspace above the indexed repository, add
`--codex-project-root /absolute/workspace/path` to both commands. The selected
Codex project must be trusted; accept Codex's normal trust prompt on first open.
Atlas never edits the global trust list.

This creates or appends one marker-delimited block in `.codex/config.toml`,
preserves unrelated valid TOML bytes, refuses symlinks/foreign Atlas entries,
and never changes `~/.codex/config.toml`. The file contains absolute local paths
and should not be committed. Start a new Codex task rooted at the repository and
call `project_status` to verify the resolved repository, index freshness and
session-start update result. Remove only Atlas's matching block with
`codebase-atlas codex remove --scope project`.

Treat that real `project_status` call as the verification gate. `codex mcp get`
can report only the global management layer and therefore is not sufficient to
verify project-local switching.

The session-start refresh is not a permanent watcher. If a legacy layout or
short migration/admission operation blocks startup, the new server reports
`provider_busy` and uses the previous index rather than waiting indefinitely.
Retry after that operation completes. Software release checks are asynchronous,
cached for 24 hours, notify-only, and disabled by
`CODEBASE_ATLAS_NO_UPDATE_CHECK=1`.

Point an MCP client at the executable and project configuration without changing
the configuration automatically:

```json
{
  "command": "codebase-atlas",
  "args": ["mcp", "--config", "/absolute/path/.codebase-atlas.toml"]
}
```

## Upgrade and removal

```bash
python3.12 -m pip install --upgrade /path/to/codebase-atlas
python3.12 -m pip uninstall codebase-atlas
```

Uninstalling the package does not remove project configuration or caches. After
checking the path printed by `codebase-atlas doctor`, those can be removed
separately if no longer needed.

Release mechanics, version/tag checks, checksums, and the external CI boundary
are documented in `docs/RELEASING.md`.
