# Contributing

Codebase Atlas is a public, single-maintainer project. Issues and focused pull
requests are welcome. By submitting a contribution, you represent that you have
the right to submit it and agree that it is licensed under the project's Apache
License 2.0 without additional terms.

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
python scripts/check_publication_readiness.py --mode public
```

Packaging changes must also run `scripts/lifecycle_acceptance.py`. Query changes
require source-derived positive and negative holdouts frozen before candidate
output. Never commit local indexes, target repositories, credentials, absolute
machine configuration, or evaluation results containing user source.
