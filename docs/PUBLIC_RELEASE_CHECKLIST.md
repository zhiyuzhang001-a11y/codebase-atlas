# Public release checklist

This checklist separates completed technical readiness from the owner decisions
that must not be inferred or automated.

## Technical readiness complete

- [x] Independent M17 validation covered Django, Flask, Vite, and Zod.
- [x] Supported-platform CI, package verification, and install/upgrade/downgrade/
  uninstall acceptance pass for 0.12.1.
- [x] Product scope, known limits, privacy/data flow, security boundaries,
  support, governance, contribution expectations, and release procedure are
  documented.
- [x] Issue and pull-request templates avoid requesting private source.
- [x] The pre-public repository hygiene gate checks standard files, local links,
  package URLs, generated paths, and common secret/path signatures.

## Owner decisions required before public visibility

- [ ] Select and add the product license after confirming third-party
  compatibility; update package metadata and all pending-license statements.
- [ ] Approve changing the GitHub repository from private to public.
- [ ] Enable and verify an appropriate private vulnerability-reporting channel,
  then update `SECURITY.md` with the exact contact route.
- [ ] Confirm public contribution terms and a private code-of-conduct enforcement
  contact.
- [ ] Run `python scripts/check_publication_readiness.py --mode public`, the full
  test suite, wheel verification, lifecycle acceptance, and GitHub CI on the
  final public candidate.
- [ ] Test all repository, documentation, issue, security, changelog, and release
  links from a signed-out browser after visibility changes.

Public visibility must not be enabled while any owner-decision item is open.
Passing this checklist does not imply exhaustive query recall or remove the
documented limits in `SUPPORT.md`.
