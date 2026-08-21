# Private release process

Codebase Atlas currently releases only from its private GitHub repository. No
public product license has been selected.

## Preconditions

1. `pyproject.toml` and `codebase_atlas.__version__` contain the same version.
2. Product tests pass on the supported Python/OS matrix.
3. The package lifecycle job passes install, upgrade, downgrade, and uninstall.
4. The wheel contains both bridge scripts, TypeScript 5.9.3, its license, and the
   third-party notice.
5. The tag is exactly `v<version>`.

## Release

```bash
python scripts/verify_release.py
git tag -a v0.11.0 -m "Codebase Atlas 0.11.0"
git push origin main v0.11.0
```

The tag workflow rebuilds the wheel, rejects version/tag or packaged-asset
mismatches, writes `SHA256SUMS.txt`, and creates a release in the private
repository. It does not publish to PyPI or make the repository public.
Release builds set a fixed `SOURCE_DATE_EPOCH`; rebuilding identical tagged
source therefore produces identical wheel bytes and SHA-256.

## License boundary

The TypeScript runtime license and third-party notices are distributed with the
wheel. Serena and Codebase Memory remain separately installed and separately
licensed. Choosing a public Codebase Atlas license is a user decision reserved
for the public-release readiness gate.
