# Codebase Atlas deployment rules for Codex

This is the canonical, version-controlled workflow for deploying Codebase Atlas
into a user's current repository. A request to “deploy Codebase Atlas in this
project” authorizes the bounded project-level workflow below. It does not
authorize source edits, global MCP/editor changes, replacement of unrelated
tools, or deletion of existing indexes.

## 1. Establish the target

1. Resolve the current Codex workspace and the intended Git repository root.
2. Inspect, without changing anything, existing `.codebase-atlas.toml`,
   `.codex/config.toml`, Atlas installation and index status.
3. Stop on an ambiguous workspace, a repository mismatch, a foreign managed
   block, or a different existing Atlas configuration. Never borrow a sibling,
   descendant, recent-project or development-repository configuration.

## 2. Acquire trusted release assets

1. Resolve the latest non-draft, non-prerelease release from
   `zhiyuzhang001-a11y/codebase-atlas`. Do not install from `main`, a source
   checkout, an evaluation directory, a pull request, or an Actions artifact.
2. Reuse an already verified installation when it is the requested release and
   its recorded paths still work. One machine installation may serve many
   projects; the projects must not share config, repository identity or Atlas
   state.
3. Otherwise download only the release wheel, `SHA256SUMS.txt`, and the managed
   Provider archive plus adjacent checksum for the current OS/architecture.
   Verify both checksums before installation or extraction.
4. Install into versioned user-owned locations. Keep the Provider's manifest
   and MIT license with its executable. Do not overwrite an unrelated global
   Provider or remove the previous verified version before acceptance passes.
5. Validate Python 3.11–3.14, Node.js 18+, the managed Provider and Serena.
   Report a missing prerequisite clearly; do not silently modify a system
   package manager, global Python, Node, editor or MCP configuration.

## 3. Plan, then apply project onboarding

1. Run `codebase-atlas onboard --repo <exact-root>` first without `--apply`.
   Treat its JSON `apply_argv` as authoritative; do not parse display text.
2. Review every proposed target. Configuration must name the exact repository,
   use the verified runtime paths and use project-owned Atlas data.
3. Because the user requested deployment, apply that accepted plan with
   `onboard --apply`. Refuse rather than overwrite a differing configuration.
4. Preview `codebase-atlas codex plan --scope project`, then apply the exact
   project-scoped plan. Do not select `global-auto` unless the user separately
   asks for an account-wide automatic entry.
5. Do not commit machine-local `.codebase-atlas.toml` or `.codex/config.toml`
   unless the user explicitly approves and the paths are portable.

## 4. Acceptance gate

Before reporting success, require all of the following:

- configured repository and `project_status` resolve to the exact target root;
- `doctor` reports `ready` and the index reports `fresh`;
- deep inspection reports a healthy Provider database;
- one known symbol from the target repository returns the expected target file;
- a wrong-project symbol does not return foreign facts;
- target source and unrelated configuration remain unchanged;
- no failed setup leaves a new Atlas process running.

MCP configuration is loaded only when a Codex task starts. After project-scoped
integration changes, tell the user to open a new task rooted at the repository
and verify `project_status` once. A CLI-only success is not proof that an
already-open task hot-reloaded the new MCP entry.

## 5. Updating and failure behavior

- Update only from a newer stable Release, repeat checksum verification and
  retain the previous verified version until health and query checks pass.
- Use Atlas migration/repair preview before applying recovery. Never delete an
  index merely to change Provider versions.
- If GitHub, a checksum, a prerequisite, repository identity, health or the
  verification query fails, report `incomplete` with the exact failed gate and
  remediation. Do not claim deployment success or fall back to another project.

The final user report should name the Atlas version, exact repository, index
status, verification query, whether a new Codex task is required, and any
remaining prerequisite. Keep it short and avoid exposing private source text.
