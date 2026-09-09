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

## Repository analysis

- For cross-file discovery, dependency tracing, or impact analysis, first read
  `.agents/skills/codebase-atlas/SKILL.md` and use this repository's Atlas MCP.
- Check the exact repository identity and freshness with `project_status`
  before accepting evidence, and use `analyze_change` for change-impact work.
- If Atlas is stopped, stale, partial, mismatched, or fails the query, state the
  limitation and fall back to direct source inspection. Never present failed or
  mismatched Atlas output as repository fact.
- Do not create parallel navigation caches, generated summaries, or other
  substitute indexes in the repository.
- Multiple agents may issue Atlas read/query calls concurrently. Assign
  explicit enable, stop, update, remove, migration, index, or refresh work to
  one owning agent while the others wait. Treat `provider_busy` and
  `provider_startup_timeout` as transient backpressure: retry within the
  remaining task budget after the owner finishes; do not archive or recreate a
  task merely to recover.

## Atlas self-use feedback

- When Atlas is used to develop Atlas, record reproducible product shortcomings
  in `docs/ATLAS_SELF_USE_FINDINGS.md`.
- Each finding must include the observed command or task, expected behavior,
  actual evidence, safe fallback, and the test or change that resolves it.
- Keep unresolved findings explicit. Do not turn stale, partial, mismatched, or
  failed output into facts about the codebase.
