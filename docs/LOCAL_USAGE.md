# Local installation and daily use

For the task-oriented workflow and result interpretation, also see
`docs/DAILY_USE_PLAYBOOK.md`.

## Requirements

- Python 3.11 or newer
- Node.js
- a local Codebase Memory executable
- a Python environment with Serena installed

Atlas does not edit global MCP/editor configuration. Provider caches are stored
under `~/.local/share/codebase-atlas/` by default and user source files remain
unchanged.
The wheel includes the pinned TypeScript 5.9.3 runtime used by the exact test
analyzer; it does not depend on a target repository's `node_modules` for that API.

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

For TypeScript projects, scoped `references` first preserves exact compiler
occurrences (including test files excluded by a production tsconfig), then uses
the semantic Provider for additional occurrences within the remaining deadline.
If semantic expansion times out, the exact partial results are retained with
explicit truncation metadata.

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

Within a long-lived JSON-lines or MCP session, Atlas caches exact definitions and
completed graph traversals. Repeating the same query and budget avoids duplicate
CBM trace/identity work. The cache is cleared automatically when the configured
CBM index database's modification fingerprint changes; time-truncated traversals
are never cached.

Atlas serializes CBM use across local Atlas processes because the upstream Provider
supports only one global daemon at a time, even when repositories use different
cache directories. A second query waits for the first session to release the lock;
that wait counts against `timeout_ms` and returns explicit time truncation if the
budget expires. The per-user lock lives in `XDG_RUNTIME_DIR` or the system temporary
directory. Set `ATLAS_RUNTIME_DIR` only when a different runtime directory is
required. Atlas does not stop a daemon it did not start.

Use `--config /path/to/config.toml` when running outside the repository. The
long-lived JSON-lines interface is `codebase-atlas query-batch`; the read-only MCP
server is `codebase-atlas mcp`.

## MCP connection

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
