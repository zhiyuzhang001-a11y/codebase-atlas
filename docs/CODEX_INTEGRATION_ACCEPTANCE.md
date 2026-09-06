# Codex integration acceptance

This document records the fixed 2026-09-06 dogfood run used to qualify the
worktree-aware Codex integration. Measurements are descriptive observations on
one Apple Silicon machine, not universal performance guarantees.

## Scope and identities

The run kept two independent Atlas projects:

- the isolated `Local-Codebase-Intelligence` Codex worktree at
  `5783d99960df74f72940fd3698d72aab70d517a5`
- the separate Codebase Atlas product checkout at
  `1ccde2bfec0e80509983b745da946dee4ce219aa`

Both reported a fresh full index and returned project-prefixed node identities.
The same-name `main` query, constrained to each target path, returned only that
project's source path and its own commit generation.

## Fixed tasks

1. Python exact definition: `load_cases` resolved to
   `src/codebase_atlas_eval/models.py`.
2. Python cross-file callers: `load_cases` returned seven nodes across the CLI,
   suite, workspace check and model tests without truncation.
3. Python impact: upstream depth-two analysis of `score_provider` returned nine
   nodes across implementation, CLI, suite and tests without truncation.
4. TypeScript exact member: `PrimaryWorker.run` resolved only in
   `fixtures/ts-tests/src/members.ts`.
5. TypeScript test discovery: the same exact member returned the `runs the
   primary worker` callback in `fixtures/ts-tests/tests/members.test.ts` with an
   exact call edge.
6. Lifecycle and isolation: `atlas stop` preserved the index, a query failed
   closed with `code=stopped`, and `atlas enable` restored `ready/fresh` and
   passed positive plus random negative verification. A random wrong-project
   symbol returned zero nodes.

The configured MCP command was also exercised over stdio, not only through the
CLI. Initialization negotiated protocol `2025-11-25`; `project_status` reported
the exact fbe5 worktree as fresh, and a real `definition` tool call returned the
expected `load_cases` source with `isError=false`.

## Navigation and response measurements

On one long-lived query batch, observed Atlas durations were 9.5 s for the first
definition, 3.4 s for callers, 19.8 s for depth-two impact, 43 ms for a later
reference query and 1.6 s for a negative definition. A TypeScript cold member
definition took 9.6 s; a warm exact related-test query took 129 ms. Cold and hot
behavior therefore must be reported separately.

Equivalent `rg` discovery commands took 4–12 ms and emitted 41–951 bytes, but
they did not provide stable symbol identity, semantic edge resolution, index
generation, truncation or completeness evidence. The integration skill routes
already-localized edits to direct reading and reserves Atlas for ambiguity,
cross-file behavior, impact and test discovery.

For the fixed `load_cases`, `fix_bug`, 20-second Change Brief sample, full mode
emitted 58,862 bytes and compact mode emitted 28,375 bytes: a 51.8% reduction.
Both reported the same exact target and the same per-query completeness. Both
took about 22 seconds, so response compaction is not claimed as query-speed
improvement. Observable model token usage was unavailable and is not inferred
from byte counts.

## Host boundary

Project MCP configuration is loaded when a Codex task starts. A task that has
already loaded the 0.25 reloadable bootstrap observes later stop, enable and
version switches at request boundaries. A task that never loaded the project
MCP cannot be injected in place; after the first project enable, start one new
task. Host-level implicit skill selection must be checked in that newly started
task and is not implied by the successful stdio protocol test above.

For a newly created worktree, the repeatable safe operation is `atlas enable
--repo <exact-worktree> --json`: it reuses the verified machine installation but
creates a worktree-specific configuration, identity and index. Do not copy
another checkout's `.codebase-atlas.toml`. A Codex local-environment setup script
should not be adopted until an end-to-end host test proves that its generated
project MCP configuration is discovered before the first task request.
