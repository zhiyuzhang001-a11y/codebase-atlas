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

## Release

```bash
python scripts/verify_release.py
git push origin main
# Wait for all 12 OS/Python jobs and package-lifecycle on this exact SHA.
git tag -a v0.21.0 -m "Codebase Atlas 0.21.0"
git push origin v0.21.0
```

The tag workflow rebuilds the wheel, rejects version/tag or packaged-asset
mismatches, writes `SHA256SUMS.txt`, and creates a GitHub release. It does not
publish to PyPI.
Release builds set a fixed `SOURCE_DATE_EPOCH`; rebuilding identical tagged
source therefore produces identical wheel bytes and SHA-256.

Setuptools currently stamps generated sdist tar metadata with build time. If an
sdist is produced for local verification, normalize two independent builds and
compare them before distribution:

```bash
python scripts/normalize_sdist.py dist-a/codebase_atlas-0.21.0.tar.gz \
  normalized-a.tar.gz --epoch "$SOURCE_DATE_EPOCH"
python scripts/normalize_sdist.py dist-b/codebase_atlas-0.21.0.tar.gz \
  normalized-b.tar.gz --epoch "$SOURCE_DATE_EPOCH"
cmp normalized-a.tar.gz normalized-b.tar.gz
```

The public GitHub Release workflow currently distributes only the verified
wheel and its checksum file.

## License boundary

The Apache License 2.0 product license, TypeScript runtime license, and third-party
notices are distributed with the wheel. Serena and Codebase Memory remain
separately installed and separately licensed.
