# Release process

Codebase Atlas releases from its public GitHub repository under Apache License
2.0. It is not currently published to PyPI.

## Preconditions

1. `pyproject.toml` and `codebase_atlas.__version__` contain the same version.
2. Product tests pass on the supported Python/OS matrix.
3. The package lifecycle job passes install, upgrade, downgrade, and uninstall.
4. The wheel contains the product license, both bridge scripts, TypeScript 5.9.3,
   its license, and the third-party notice.
5. The tag is exactly `v<version>`.
6. `scripts/check_publication_readiness.py --mode public` passes.
7. The `Managed Provider Bundles` workflow passes on the final candidate and
   produces all six exact-source bundles plus `PROVIDER_SHA256SUMS.txt`.

## Release

```bash
VERSION=0.22.1
python scripts/verify_release.py
git push origin main
# Wait for all 12 OS/Python jobs and package-lifecycle on this exact SHA.
git tag -a "v${VERSION}" -m "Codebase Atlas ${VERSION}"
git push origin "v${VERSION}"
```

The tag workflow rebuilds the wheel, rejects version/tag or packaged-asset
mismatches, writes `SHA256SUMS.txt`, and creates a **draft** GitHub release. It
does not publish to PyPI. Download the final candidate's
`managed-provider-release-assets` workflow artifact, verify it again, and add
all of its files to the draft:

```bash
VERSION=0.22.1
python scripts/verify_managed_provider_bundles.py provider-assets
gh release upload "v${VERSION}" provider-assets/*
```

From the draft assets, verify the wheel checksum and install it into a new
virtual environment. Extract the Provider bundle matching the current platform,
verify its adjacent checksum and embedded manifest/binary digest, then run
`--version`, one isolated project onboarding/query, and uninstall. Publish only
after this exact public-download candidate passes:

```bash
VERSION=0.22.1
gh release edit "v${VERSION}" --draft=false --prerelease=false --latest
```
Release builds set a fixed `SOURCE_DATE_EPOCH`; rebuilding identical tagged
source therefore produces identical wheel bytes and SHA-256.

Setuptools currently stamps generated sdist tar metadata with build time. If an
sdist is produced for local verification, normalize two independent builds and
compare them before distribution:

```bash
VERSION=0.22.1
python scripts/normalize_sdist.py "dist-a/codebase_atlas-${VERSION}.tar.gz" \
  normalized-a.tar.gz --epoch "$SOURCE_DATE_EPOCH"
python scripts/normalize_sdist.py "dist-b/codebase_atlas-${VERSION}.tar.gz" \
  normalized-b.tar.gz --epoch "$SOURCE_DATE_EPOCH"
cmp normalized-a.tar.gz normalized-b.tar.gz
```

The published GitHub Release contains the verified Atlas wheel/checksum and six
separately licensed managed Provider bundles/checksums. The Provider bundles are
not embedded in the wheel and do not replace an unrelated global installation.

## License boundary

The Apache License 2.0 product license, TypeScript runtime license, and third-party
notices are distributed with the wheel. Serena and Codebase Memory remain
separately installed and separately licensed.
