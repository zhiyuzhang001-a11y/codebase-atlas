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
git tag -a v0.16.1 -m "Codebase Atlas 0.16.1"
git push origin main v0.16.1
```

The tag workflow rebuilds the wheel, rejects version/tag or packaged-asset
mismatches, writes `SHA256SUMS.txt`, and creates a GitHub release. It does not
publish to PyPI.
Release builds set a fixed `SOURCE_DATE_EPOCH`; rebuilding identical tagged
source therefore produces identical wheel bytes and SHA-256.

## License boundary

The Apache License 2.0 product license, TypeScript runtime license, and third-party
notices are distributed with the wheel. Serena and Codebase Memory remain
separately installed and separately licensed.
