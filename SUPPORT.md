# Support and compatibility

Codebase Atlas is maintained on a best-effort basis with no response-time or
long-term-support guarantee.

## Tested scope

- Python 3.11 through 3.14;
- Linux x86_64/ARM64, macOS Apple Silicon, and Windows x86_64/ARM64 through the
  CI matrix;
- Node.js 18 or newer;
- Python and TypeScript/JavaScript repositories;
- local CLI, JSON-lines batch, and read-only stdio MCP use;
- explicitly configured Codebase Memory and Serena runtimes.

The M32 multi-project and bounded-large-repository candidate requires a
Codebase Memory build containing the session-scoped worker boundary, bounded
two-pass pipeline and memory-aware global scheduler. The exact locally accepted
commits are recorded in the release evidence; a public Atlas release must pin an
installable upstream or maintained build rather than silently accepting an
older incompatible executable.

Releases through 0.26.2 provide exact-source managed Provider bundles for Linux
x86_64/ARM64, macOS Intel/Apple Silicon, and Windows x86_64/ARM64. Existing
macOS Intel installations and historical Release assets are retained, but
`macos-x86_64` is frozen: Atlas does not build, test, update, or promise new
Provider bundles for it after that release. Current releases actively support
the other five targets. Each bundle contains its own MIT license and manifest
and remains a separate program rather than becoming part of the Apache-2.0
Atlas wheel.

Only the latest release is the primary support target. A passing setup
check verifies discoverable capabilities, not every Provider/repository version.

## Known limits

- Recall is not exhaustive. Atlas covers explicit Python import/re-export
  bindings, resolved TypeScript expression-assigned callers/direct callees, and
  resolved external-helper test suites; dynamic imports, runtime dispatch, and
  deeper framework-specific indirection can still be unsupported.
- Python `registers` completeness applies only when callers/callees explicitly
  request `relation=registers` and the validated sidecar is current. The exact
  API set is documented in `docs/LOCAL_USAGE.md`; other framework APIs and
  runtime-generated callbacks remain unsupported rather than guessed.
- Broad TypeScript references/related-tests/impact can reach explicit node or
  time budgets. Exact compiler-backed `references` can resume a node-budget
  result only inside the issuing MCP or `query-batch` session; other truncated
  results remain partial evidence, not completeness.
- Large semantic references can use the full timeout; time-truncated results are
  not cached.
- Atlas is not a repository sandbox, hosted service, editor UI, background file
  watcher, or cross-repository intelligence system.

Use the bug form for sanitized reproductions and `SECURITY.md` for vulnerabilities.
Out-of-scope feature requests are not compatibility commitments.
