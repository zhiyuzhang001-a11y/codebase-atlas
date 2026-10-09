from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from codebase_atlas.rust_installation import (
    load_toolchain_receipt, save_toolchain_receipt, verify_existing_toolchain,
    runtime_from_receipt,
)
from codebase_atlas.rust_runtime import RustRuntimeError, SYSROOT_LIBRARY


class RustInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "toolchain"
        self.root.mkdir()
        self.store = self.base / "private-store"
        self.archives = {}
        self.lock = {"manifest_sha256": "a" * 64, "targets": {"macos-arm64": {"components": {}}}}
        mapping = {
            "cargo": "bin/cargo", "rustc": "bin/rustc", "rust-analyzer-preview": "bin/rust-analyzer",
            "rust-std": "lib/rustlib/target/lib/libstd.rlib", "rust-src": "lib/rustlib/src/rust/library/core/lib.rs",
        }
        # Minimal TOML manifest for fake archives; the production pinned manifest
        # identity is never changed. Use the real official vendor-config bytes.
        manifest = b'[workspace]\nmembers=[]\n'
        sysroot_files = {
            SYSROOT_LIBRARY + "/Cargo.toml": manifest,
            SYSROOT_LIBRARY + "/.cargo/config.toml": (
                b'[source.crates-io]\nreplace-with = "vendored-sources"\n\n'
                b'[source.vendored-sources]\ndirectory = "vendor"\n'),
            SYSROOT_LIBRARY + "/vendor/fixture": b"vendored fixture",
        }
        identity_patcher = patch.dict("codebase_atlas.rust_runtime.SYSROOT_FILE_SHA256", {
            SYSROOT_LIBRARY + "/Cargo.toml": hashlib.sha256(manifest).hexdigest(),
        })
        identity_patcher.start()
        self.addCleanup(identity_patcher.stop)
        for component, relative in mapping.items():
            payload = ("sentinel bytes for " + component).encode()
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            archive = self.base / (component + ".tar.xz")
            entries = {relative: payload}
            if component == "rust-src":
                entries.update(sysroot_files)
                for name, value in sysroot_files.items():
                    path = self.root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(value)
            with tarfile.open(archive, "w:xz") as bundle:
                for name, value in {
                    **{"package/" + component + "/" + name: value for name, value in entries.items()},
                    "package/LICENSE-MIT": b"MIT license",
                    "package/LICENSE-APACHE": b"Apache license",
                }.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(value)
                    bundle.addfile(member, BytesIO(value))
            identity = {"url": "https://static.rust-lang.org/dist/" + archive.name,
                        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
            if component == "rust-src":
                self.lock["rust_src"] = identity
            else:
                self.lock["targets"]["macos-arm64"]["components"][component] = identity
            self.archives[component] = archive
        patcher = patch("codebase_atlas.rust_installation.release_lock", return_value=self.lock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def verify(self):
        return verify_existing_toolchain(self.root, self.archives, "macos-arm64")

    def test_receipt_compares_official_bytes_and_is_reusable_without_execution(self):
        document = self.verify()
        self.assertEqual(len(document["files"]), 8)
        path = save_toolchain_receipt(document, self.store)
        before = path.read_bytes()
        self.assertEqual(load_toolchain_receipt(path, store=self.store), document)
        self.assertEqual(save_toolchain_receipt(document, self.store), path)
        self.assertEqual(path.read_bytes(), before)

    def test_archive_corruption_is_rejected(self):
        self.archives["cargo"].write_bytes(b"corrupted")
        with self.assertRaisesRegex(RustRuntimeError, "archive checksum"):
            self.verify()

    def test_receipt_rechecks_sysroot_not_only_three_binaries(self):
        path = save_toolchain_receipt(self.verify(), self.store)
        (self.root / "lib/rustlib/target/lib/libstd.rlib").write_bytes(b"tampered library")
        with self.assertRaisesRegex(RustRuntimeError, "installed content"):
            load_toolchain_receipt(path, store=self.store)

    def test_forged_source_and_project_local_receipt_are_rejected(self):
        path = save_toolchain_receipt(self.verify(), self.store)
        document = json.loads(path.read_text())
        document["components"]["cargo"]["sha256"] = "0" * 64
        path.write_text(json.dumps(document))
        with self.assertRaisesRegex(RustRuntimeError, "component source"):
            load_toolchain_receipt(path, store=self.store)
        fake = self.base / "project/receipt.json"
        fake.parent.mkdir()
        fake.write_text(json.dumps(document))
        fake.chmod(0o600)
        with self.assertRaisesRegex(RustRuntimeError, "canonical store"):
            load_toolchain_receipt(fake, store=self.store)

    def test_missing_component_cannot_be_adopted(self):
        del self.archives["rust-src"]
        with self.assertRaisesRegex(RustRuntimeError, "incomplete"):
            self.verify()

    def test_installed_symlink_outside_root_cannot_be_adopted(self):
        installed = self.root / "bin/cargo"
        outside = self.base / "foreign-cargo"
        installed.rename(outside)
        installed.symlink_to(outside)
        with self.assertRaisesRegex(RustRuntimeError, "escapes its root"):
            self.verify()

    def test_publication_never_exposes_partial_receipt_and_cleans_temporary(self):
        document = self.verify()
        import os
        real_link = os.link
        def publish(source, destination):
            self.assertFalse(Path(destination).exists())
            self.assertEqual(json.loads(Path(source).read_text()), document)
            real_link(source, destination)
        with patch("codebase_atlas.rust_installation.os.link", side_effect=publish):
            path = save_toolchain_receipt(document, self.store)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_runtime_uses_owned_store_and_rejects_wrong_platform(self):
        path = save_toolchain_receipt(self.verify(), self.store)
        repository = self.base / "project"
        repository.mkdir()
        with patch("codebase_atlas.rust_installation.toolchain_store", return_value=self.store):
            with patch("codebase_atlas.release_installation.current_platform_target", return_value="macos-arm64"):
                runtime = runtime_from_receipt(path, repository=repository)
                self.assertEqual(runtime.analyzer.path, self.root / "bin/rust-analyzer")
                self.assertEqual(runtime.toolchain_root, self.root)
            with patch("codebase_atlas.release_installation.current_platform_target", return_value="linux-arm64"):
                with self.assertRaisesRegex(RustRuntimeError, "platform"):
                    runtime_from_receipt(path, repository=repository)

    def test_runtime_rejects_project_owned_installation_store(self):
        with patch("codebase_atlas.rust_installation.toolchain_store", return_value=self.base / "store"):
            with self.assertRaisesRegex(RustRuntimeError, "project-local"):
                runtime_from_receipt(self.base / "receipt.json", repository=self.base)

    def test_factory_rejects_missing_or_alias_sysroot_receipt_identity(self):
        from codebase_atlas.rust_installation import _runtime_from_document
        from codebase_atlas.rust_runtime import SYSROOT_FILE_SHA256
        document = self.verify()
        relative = next(iter(SYSROOT_FILE_SHA256))
        identity = document["files"].pop(relative)
        with self.assertRaisesRegex(RustRuntimeError, "sysroot context identity"):
            _runtime_from_document(document)
        document["files"][relative] = identity
        document["files"][relative]["resolved"] = "foreign/Cargo.toml"
        with self.assertRaisesRegex(RustRuntimeError, "sysroot context identity"):
            _runtime_from_document(document)


if __name__ == "__main__":
    unittest.main()
