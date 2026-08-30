from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.build_managed_provider import (
    DEFAULT_COMMIT,
    DEFAULT_VERSION,
    TARGETS,
    write_tar,
    write_zip,
)
from scripts.verify_managed_provider_bundles import main as verify_main


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ManagedProviderBundleTests(unittest.TestCase):
    def make_release_set(self, directory: Path) -> None:
        epoch = 1_788_068_456
        for target, (_system, binary_name, kind) in TARGETS.items():
            bundle = directory / target
            bundle.mkdir()
            binary = bundle / binary_name
            binary.write_bytes(f"managed-provider:{target}".encode())
            (bundle / "LICENSE").write_text("MIT License\n", encoding="utf-8")
            manifest = {
                "source": {"commit": DEFAULT_COMMIT},
                "build": {
                    "managed_version": DEFAULT_VERSION,
                    "platform_arch": target,
                    "reproducible": True,
                    "independent_builds": 2,
                },
                "artifact": {
                    "sha256": sha256(binary),
                    "size": binary.stat().st_size,
                    "version_output": f"codebase-memory-mcp {DEFAULT_VERSION}",
                },
            }
            (bundle / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            suffix = ".zip" if kind == "zip" else ".tar.gz"
            archive = directory / f"codebase-atlas-provider-{DEFAULT_VERSION}-{target}{suffix}"
            if kind == "zip":
                write_zip(bundle, archive, epoch)
            else:
                write_tar(bundle, archive, epoch)
            archive.with_name(archive.name + ".sha256").write_text(
                f"{sha256(archive)}  {archive.name}\n", encoding="utf-8"
            )
            for path in bundle.iterdir():
                path.unlink()
            bundle.rmdir()

    def test_complete_release_set_passes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            self.make_release_set(directory)
            with mock.patch.object(sys, "argv", ["verify", str(directory)]):
                self.assertEqual(verify_main(), 0)

    def test_corrupt_archive_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            self.make_release_set(directory)
            archive = next(directory.glob("*.tar.gz"))
            archive.write_bytes(archive.read_bytes() + b"corrupt")
            with mock.patch.object(sys, "argv", ["verify", str(directory)]):
                with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                    verify_main()


if __name__ == "__main__":
    unittest.main()
