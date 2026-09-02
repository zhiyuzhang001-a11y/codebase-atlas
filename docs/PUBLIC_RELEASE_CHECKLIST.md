# Public release checklist

This checklist separates completed technical readiness from the owner decisions
that must not be inferred or automated.

## Technical readiness complete

- [x] Independent M17 validation covered Django, Flask, Vite, and Zod.
- [x] Supported-platform CI, package verification, and install/upgrade/downgrade/
  uninstall acceptance pass for 0.12.2.
- [x] Product scope, known limits, privacy/data flow, security boundaries,
  support, governance, contribution expectations, and release procedure are
  documented.
- [x] Issue and pull-request templates avoid requesting private source.
- [x] The pre-public repository hygiene gate checks standard files, local links,
  package URLs, generated paths, and common secret/path signatures.

## Public launch gates

- [x] Select Apache License 2.0, add it to source and package metadata, retain
  third-party notices, and include all license material in the wheel.
- [x] Approve changing the GitHub repository from private to public.
- [x] Enable and verify an appropriate private vulnerability-reporting channel,
  then update `SECURITY.md` with the exact contact route.
- [x] Confirm Apache-2.0 contribution terms and use the private reporting channel
  for sensitive code-of-conduct enforcement reports.
- [x] Run `python scripts/check_publication_readiness.py --mode public`, the full
  test suite, wheel verification, lifecycle acceptance, and GitHub CI on the
  final public candidate.
- [x] Test all repository, documentation, issue, security, changelog, and release
  links from a signed-out browser after visibility changes.

All public launch gates passed for 0.12.2. Passing this checklist does not imply
exhaustive query recall or remove the documented limits in `SUPPORT.md`.

## 0.23.0 explicit in-process refresh

- [x] Pass manifest/dirty planning, same-MCP refresh, transaction recovery,
  generation consistency, concurrency and cross-project isolation gates.
- [x] Reject and remove the optional polling watcher after corrected idle CPU
  measurement exceeded its frozen resource limit.
- [x] Pass real Provider no-op, 1/5/10-file, rename/delete, clean-oracle and
  failure rollback validation for the explicit refresh route.
- [x] Teach the project rule to refresh after a batch of source changes and
  before the next Atlas query without asking the user to remember a command.
- [x] Pass the full local suite, byte-identical wheel construction, wheel
  content verification and public-readiness checks.
- [ ] Pass all remote CI jobs, publish and reverify stable `v0.23.0`, install it
  side by side and complete the exact PDF-project deployment.

## 0.22.0 bounded file narrowing candidate

- [x] The frozen Atlas and Provider patches passed independent development,
  holdout, acceptance and clean-room upgrade/downgrade gates without changing
  their scoring thresholds.
- [x] Provider source integration passed 7,770 tests with zero failures and all
  production process/security guards at exact commit `e088a41b`.
- [x] Pass the final Atlas test, package, reproducibility and public-readiness
  gates on the exact `0.22.0` release commit.
- [x] Produce and verify all six managed Provider bundles from `e088a41b` with
  managed version `0.10.8-atlas.2+e088a41b`.
- [x] Pass GitHub CI on the exact release commit, publish the draft tag release,
  verify public-download assets, then promote it to stable.
- [x] Install into a new versioned machine location while retaining and
  revalidating the complete `0.21.0` recovery path.

## 0.22.1 Git-aware Python inventory correction

- [x] Freeze regression tests that fail when Git-ignored Python facts enter
  registration or exact-reference inventories.
- [x] Use one deterministic tracked plus non-ignored untracked inventory for
  both Python providers, with symlink and path-escape rejection.
- [x] Pass full local tests and an isolated exact-root full onboarding, deep
  inspection, real-symbol query and ignored-subrepository rejection.
- [x] Pass package lifecycle, reproducible-wheel and public-readiness gates.
- [ ] Pass all remote CI jobs, publish and reverify stable `v0.22.1`, install it
  side by side and complete the exact-root deployment.

## 0.21.0 managed-Provider candidate

- [x] The exact managed Provider source, version, MIT license and SHA-256 are
  recorded in its self-contained manifest and archive.
- [x] Two independent macOS arm64 Provider builds are byte-identical.
- [x] The exact installed Atlas wheel passes M17 504/504 and M19 147/147 with
  zero errors or unstable answers.
- [x] Product 239/239 and evaluation 49/49 tests pass after the final exact-edge
  correction.
- [x] Produce and verify separately downloadable managed Provider bundles for
  every publicly supported platform, or explicitly narrow the release platform.
- [x] Obtain owner authorization for the final version/tag/Release and public
  managed-binary channel. Upstream merge is not required.
- [x] Merge the exact candidate, pass `main` CI, create the draft tag release,
  attach and reverify all Provider assets, complete clean-room download/install/
  query/uninstall, and only then publish the draft.

The six-platform managed-Provider gate passed in GitHub Actions run
`33335042265`: Linux x86_64/ARM64, macOS Intel/Apple Silicon, and Windows
x86_64/ARM64 each produced byte-identical binaries from two independent builds.
The aggregate verifier accepted all six archives, embedded manifests, MIT
licenses, exact source/version identities, binary digests and sidecar checksums.

Codebase Atlas 0.21.0 was released from exact commit
`c0b2934974f69fc98ea51c31983d5030a08f0c37` after public-main CI run
`33335927225` passed 13/13 and release run `33336023937` passed. The published
Release has 15 assets. Unauthenticated public downloads passed their SHA-256
checks and were byte-identical to the clean-room-tested draft assets; fresh
install, full Flask onboarding, deep inspection, exact definition query,
process cleanup and uninstall all passed.
