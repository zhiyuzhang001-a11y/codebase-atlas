# Local installation and daily use

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
