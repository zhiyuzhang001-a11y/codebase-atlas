# Change Brief contract

`analyze-change` and MCP `analyze_change` use the same read-only product-layer
implementation and versioned response.

The request requires an exact symbol. `Owner.member` is accepted as a
deterministic shorthand for `symbol=member` plus `target_owner=Owner`; it is not
a fuzzy-name search. Add `target_path` whenever the repository may contain the
same identity in more than one file.

Definition always runs first. Zero complete definitions returns `unresolved`;
multiple definitions return `needs_disambiguation`; a truncated empty result is
`partial`, never proof of absence. Relationship queries run only for one exact
target. Their order follows the declared intent: bug fixes prioritize related
tests, contract changes prioritize callers/references/impact, and internal
refactors omit impact traversal.

The response contains exact target identity and provenance, implementation and
test suggestions, raw references/callers/callees/impact/test evidence, index
state, warnings, shared budget and timing. `completeness` records one of
`complete`, `partial`, `not_run`, or `error` for each primitive query and keeps
truncation reasons and continuation tokens.

The default `response_mode=full` preserves that response. The optional
`response_mode=compact` keeps the exact target, decision-oriented relationship
evidence, read/test suggestions, provenance, index state, budget, timing and all
completeness semantics while removing repeated raw paths and attributes. Use
full mode when raw primitive payloads are required for debugging or export.

Suggested paths are derived only from returned exact evidence. They tell an AI
or developer what to inspect; they do not claim a file must be edited or that a
test will pass. Source editing, test execution, natural-language inference and
LLM calls remain outside Atlas.
