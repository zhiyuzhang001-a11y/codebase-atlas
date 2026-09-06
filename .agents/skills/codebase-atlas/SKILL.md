---
name: codebase-atlas
description: Use Codebase Atlas for repository-scoped code navigation and for natural-language requests to enable, stop, update, verify, or remove Atlas. Apply it to cross-file behavior changes, impact analysis, and test discovery; skip it for simple edits already localized to one file.
---

# Codebase Atlas

Resolve the exact Git worktree before using Atlas. Treat every worktree as a separate project identity and never reuse facts or configuration from another checkout.

## Project lifecycle

For enable, deployment, connection, update, verification, or removal requests, read and follow the canonical `docs/CODEX_DEPLOYMENT_RULES.md` before changing anything. Use the four-command interface and its JSON result instead of rebuilding lifecycle steps:

- `atlas enable --repo <exact-root> --json`
- `atlas stop --repo <exact-root> --json`
- `atlas update --repo <exact-root> --json`
- `atlas remove --repo <exact-root> --json`

Do read-only discovery first. Do not alter account-wide MCP/editor configuration or commit machine-local paths unless the user separately authorizes it. After project MCP configuration changes, explain that a new Codex task is required to load the connection; do not describe a configured file as a live connection.

## Code navigation

Start with `project_status`. If it is not ready, follow its `next_action` and do not query another repository's index.

- For an ordinary local edit with an already known file and implementation, read the source directly.
- For unclear natural-language intent, use `locate_files`, inspect the returned files, then resolve an exact symbol.
- For a cross-file behavior or contract change, call `analyze_change` with the exact symbol and `target_path` before editing.
- Use the narrower definition, caller, callee, reference, impact, or related-test tools only when the Change Brief is unnecessary or reports a specific evidence gap.

Treat `partial`, truncation, timeout, skipped, and not-run fields as missing evidence, not as proof that no dependency or test exists. Fill important gaps with a bounded follow-up query or direct source search. Keep source provenance and index generation in conclusions where stale or cross-worktree facts would be risky.
