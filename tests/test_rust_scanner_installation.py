import hashlib
from io import BytesIO
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from codebase_atlas.rust_runtime import RustRuntimeError
from codebase_atlas.rust_scanner_installation import install_scanner_asset, scanner_lock, verified_scanner


class RustScannerInstallationTests(unittest.TestCase):
    def test_publication_failure_rolls_back_owned_files_and_can_retry(self):
        sha = self.bundle()
        import os
        real_link = os.link
        count = [0]
        def fail_second(source, destination):
            count[0] += 1
            if count[0] == 2:
                raise OSError("simulated disk failure")
            return real_link(source, destination)
        with patch("codebase_atlas.installation_publication.os.link", side_effect=fail_second):
            with self.assertRaises(OSError):
                self.install(sha)
        self.assertFalse((self.store / "0.2.0/macos-arm64").exists())
        self.assertTrue(self.install(sha).is_file())

    def test_source_lock_matches_frozen_native_inputs(self):
        source = Path(__file__).resolve().parents[1]
        lock = scanner_lock()
        for relative, digest in lock["files"].items():
            self.assertEqual(hashlib.sha256((source / "native/rust-syntax-scanner" / relative).read_bytes()).hexdigest(), digest)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repository = self.root / "repo"
        self.repository.mkdir()
        self.store = self.root / "scanner-store"
        patcher = patch("codebase_atlas.rust_scanner_installation.scanner_store", return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("codebase_atlas.rust_scanner_installation.current_platform_target", return_value="macos-arm64")
        patcher.start()
        self.addCleanup(patcher.stop)
        lock = scanner_lock()
        source = Path(__file__).resolve().parents[1]
        self.payloads = {"atlas-rust-syntax": b"verified scanner sentinel",
                         "LICENSE": (source / "LICENSE").read_bytes(),
                         "THIRD_PARTY_NOTICES.md": (source / "native/rust-syntax-scanner/THIRD_PARTY_NOTICES.md").read_bytes()}
        self.commit = "1" * 40
        manifest = {"schema_version": 1, "source": {"repository": lock["repository"], "commit": self.commit,
                    "files": lock["files"], "license": lock["license"], "source_sha256": hashlib.sha256(
                        json.dumps(lock["files"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()},
                    "build": {"scanner_version": lock["version"], "platform_arch": "macos-arm64",
                              "independent_builds": 2, "reproducible": True},
                    "artifact": {"file": "atlas-rust-syntax", "size": len(self.payloads["atlas-rust-syntax"]),
                                 "sha256": hashlib.sha256(self.payloads["atlas-rust-syntax"]).hexdigest(),
                                 "version_output": "atlas-rust-syntax 0.2.0 (test)"}}
        self.payloads["manifest.json"] = json.dumps(manifest).encode()
        self.archive = self.root / "scanner.tar.gz"

    def bundle(self, payloads=None):
        with tarfile.open(self.archive, "w:gz") as archive:
            for name, payload in (payloads or self.payloads).items():
                member = tarfile.TarInfo("macos-arm64/" + name)
                member.size = len(payload)
                archive.addfile(member, BytesIO(payload))
        return hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def install(self, sha):
        return install_scanner_asset(self.archive, sha256=sha, commit=self.commit, target="macos-arm64")

    def test_verified_asset_is_reused_and_never_executed(self):
        sha = self.bundle()
        binary = self.install(sha)
        self.assertEqual(self.install(sha), binary)
        self.assertEqual(verified_scanner(self.repository).verify(), binary)
        binary.write_bytes(b"tampered")
        with self.assertRaises(RustRuntimeError):
            verified_scanner(self.repository)

    def test_corrupted_archive_or_source_cannot_create_installation(self):
        sha = self.bundle()
        with self.assertRaisesRegex(RustRuntimeError, "checksum"):
            self.install("0" * 64)
        manifest = json.loads(self.payloads["manifest.json"])
        manifest["source"]["commit"] = "2" * 40
        self.payloads["manifest.json"] = json.dumps(manifest).encode()
        with self.assertRaisesRegex(RustRuntimeError, "identity"):
            self.install(self.bundle())
        self.assertFalse(self.store.exists())

    def test_archive_traversal_is_rejected_before_writes(self):
        with self.assertRaisesRegex(RustRuntimeError, "unsafe"):
            self.install(self.bundle({"../escape": b"foreign"}))
        self.assertFalse(self.store.exists())

    def test_license_corruption_is_rejected(self):
        self.payloads["LICENSE"] = b"different license"
        with self.assertRaisesRegex(RustRuntimeError, "identity"):
            self.install(self.bundle())

    def test_existing_foreign_directory_is_not_replaced(self):
        destination = self.store / "0.2.0/macos-arm64"
        destination.mkdir(parents=True, mode=0o700)
        self.store.chmod(0o700)
        destination.parent.chmod(0o700)
        with self.assertRaises(RustRuntimeError):
            self.install(self.bundle())
        self.assertEqual(list(destination.iterdir()), [])

    def test_project_local_scanner_store_is_rejected(self):
        self.store = self.repository / "scanner-store"
        with patch("codebase_atlas.rust_scanner_installation.scanner_store", return_value=self.store):
            self.install(self.bundle())
            with self.assertRaisesRegex(RustRuntimeError, "project-local"):
                verified_scanner(self.repository)


if __name__ == "__main__":
    unittest.main()
