# Contributing

Codebase Atlas is currently a private, single-maintainer project. External
contribution terms are not open while the product license is unresolved. This
guide records technical validation expectations; it does not grant a license to
use, copy, modify, or redistribute the project.

## Change principles

- Preserve local/read-only source behavior and never mutate global editor/MCP
  configuration automatically.
- Prefer exact, source-supported evidence. Missing evidence is better than an
  incorrect exact edge.
- Keep query limits, truncation, freshness, provenance, and ownership explicit.
- Maintenance changes must preserve prior usable state on failure and remain
  dry-run/read-only by default where applicable.
- Do not add languages, hosted services, UI, telemetry, or public compatibility
  promises as an incidental change.

## Validation

Use Python 3.11–3.14 and Node.js 18+ with npm (or an explicit TypeScript language
server). From the repository root:

```bash
ATLAS_NODE=/absolute/path/to/node PYTHONPATH=src \
  python -m unittest discover -s tests -v
python -m pip wheel . --no-deps --wheel-dir dist-current
python scripts/verify_release.py --wheel dist-current/codebase_atlas-*.whl
python scripts/check_publication_readiness.py --mode pre-public
```

Packaging changes must also run `scripts/lifecycle_acceptance.py`. Query changes
require source-derived positive and negative holdouts frozen before candidate
output. Never commit local indexes, target repositories, credentials, absolute
machine configuration, or evaluation results containing user source.
