# Codebase Atlas agent guidance

## Deployment requests

- Treat `docs/CODEX_DEPLOYMENT_RULES.md` as the canonical workflow whenever a
  user asks to install, deploy, connect, update, verify, or remove Codebase
  Atlas in another repository.
- Use only a stable GitHub Release for end-user installation. Do not install
  from this checkout, `main`, an evaluation directory, or an Actions artifact.
- Keep installations versioned and reusable across projects, but keep each
  project's configuration, repository identity and Atlas data isolated.
- Run read-only discovery and planning before writes. Never overwrite a
  different MCP entry or configuration, modify target source, or commit
  machine-local absolute paths without explicit approval.
- Deployment is complete only after repository identity, health, freshness and
  one real query are verified. Existing Codex tasks must be reopened after MCP
  configuration changes.
