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
