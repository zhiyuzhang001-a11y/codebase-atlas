# Rust semantic guest preparation

Isolated, uncompiled prototype source. Nothing here is imported by the product.
Rust remains disabled in stable 0.27.0. This directory is not a built engine,
a substitute matcher, or evidence that C1 has passed.

`src/coordinates.rs` drafts the existing one-based Unicode-codepoint contract's
conversion to the upstream semantic library's UTF-8 byte offsets, preserving
source bytes and rejecting invalid boundaries. Its Rust unit tests have not run:
compiler execution still requires the independently reviewed C0 build card.

`Cargo.toml.in` fixes the candidate wrapper root and its direct features/versions
for review; it is not a generated Cargo workspace or resolved dependency graph.
There is deliberately no build-ready `src/lib.rs` yet. The actual semantic API
and zero-import ABI must be implemented and independently reviewed before build.

`src/semantic_queries.rs` is an uncompiled real `ide::Analysis` API adapter draft,
not a matching fallback. It rejects foreign file IDs/invalid byte ranges, verifies
database text against admitted bytes, disables RA's embedded fixture remapping,
and requires a unique declaration for reference queries. The admission graph,
zero-import ABI, single-thread setup, upstream diagnostic clock adaptation and
independent execution review are still missing. Its 10,000-result draft ceiling
is not a new approved public limit; it must be reconciled with the existing
query contract before qualification.

The real query adapter must use the fixed upstream `ide` library and admitted
source roots/crate graph. No `Analysis::from_single_file`, string-matching
definition/reference fallback, project execution, WASI or guessed target layout.
See [semantic core plan](../../docs/RUST_SEMANTIC_CORE_PLAN.md).
