from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.build_rust_syntax_scanner import (
    SCANNER,
    SOURCE_FILES,
    TARGETS,
    build_once,
    reproducibility_flags,
    source_identity,
    write_tar,
    write_zip,
)
from scripts.verify_rust_syntax_bundles import main as verify_main


VERSION = "0.2.0"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RustSyntaxBundleTests(unittest.TestCase):
    def test_windows_reproducibility_controls(self) -> None:
        flags = reproducibility_flags(Path("source with spaces"), Path("first build"), windows=True)
        self.assertIn("-Clink-arg=/Brepro", flags)
        self.assertIn("-Clink-arg=/DEBUG:NONE", flags)
        self.assertIn("-Clink-arg=/INCREMENTAL:NO", flags)
        self.assertIn("--remap-path-prefix=first build=/atlas-build", flags)
        unix_flags = reproducibility_flags(Path("source"), Path("second build"), windows=False)
        self.assertFalse(any("link-arg" in flag for flag in unix_flags))

    def test_build_passes_encoded_flags_without_shell_splitting(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas build ") as raw:
            target = Path(raw)
            binary = target / "release" / ("atlas-rust-syntax.exe" if os.name == "nt" else "atlas-rust-syntax")
            binary.parent.mkdir()
            binary.touch()
            with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
                "scripts.build_rust_syntax_scanner.run"
            ) as runner:
                self.assertEqual(build_once(SCANNER, target, "cargo", 123), binary)
            env = runner.call_args.kwargs["env"]
            self.assertEqual(env["SOURCE_DATE_EPOCH"], "123")
            self.assertEqual(env["CARGO_ENCODED_RUSTFLAGS"].split("\x1f"),
                             reproducibility_flags(SCANNER, target, windows=os.name == "nt"))

    def test_build_refuses_ambient_compiler_flags(self) -> None:
        for name in ("RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS"):
            with self.subTest(name=name), mock.patch.dict(os.environ, {name: "-g"}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "ambient"):
                    build_once(SCANNER, Path("unused"), "cargo", 123)

    def make_release_set(self, directory: Path) -> None:
        epoch = 1_788_068_456
        source = source_identity(SCANNER)
        for target, (_system, _machines, binary_name, kind) in TARGETS.items():
            bundle = directory / target
            bundle.mkdir()
            binary = bundle / binary_name
            binary.write_bytes(f"rust-syntax:{target}".encode())
            (bundle / "LICENSE").write_text("Apache License\n", encoding="utf-8")
            (bundle / "THIRD_PARTY_NOTICES.md").write_text("The MIT License\n", encoding="utf-8")
            manifest = {
                "schema_version": 1,
                "source": source,
                "build": {
                    "scanner_version": VERSION,
                    "platform_arch": target,
                    "reproducible": True,
                    "independent_builds": 2,
                },
                "artifact": {
                    "file": binary_name,
                    "sha256": sha256(binary),
                    "size": binary.stat().st_size,
                    "version_output": f"atlas-rust-syntax {VERSION}",
                },
            }
            (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            suffix = ".zip" if kind == "zip" else ".tar.gz"
            archive = directory / f"codebase-atlas-rust-syntax-{VERSION}-{target}{suffix}"
            (write_zip if kind == "zip" else write_tar)(bundle, archive, epoch)
            archive.with_name(archive.name + ".sha256").write_text(
                f"{sha256(archive)}  {archive.name}\n", encoding="utf-8"
            )
            for path in bundle.iterdir():
                path.unlink()
            bundle.rmdir()

    def test_target_set_excludes_macos_intel(self) -> None:
        self.assertEqual(
            set(TARGETS),
            {"linux-x86_64", "linux-arm64", "macos-arm64", "windows-x86_64", "windows-arm64"},
        )

    def test_source_identity_is_content_bound(self) -> None:
        identity = source_identity(SCANNER)
        self.assertEqual(set(identity["files"]), set(SOURCE_FILES))
        self.assertRegex(identity["source_sha256"], r"^[0-9a-f]{64}$")

    def test_complete_release_set_passes_and_tamper_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            self.make_release_set(directory)
            with mock.patch.object(sys, "argv", ["verify", str(directory)]):
                self.assertEqual(verify_main(), 0)
            archive = next(directory.glob("*.tar.gz"))
            archive.write_bytes(archive.read_bytes() + b"tamper")
            with mock.patch.object(sys, "argv", ["verify", str(directory)]):
                with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                    verify_main()


if __name__ == "__main__":
    unittest.main()
