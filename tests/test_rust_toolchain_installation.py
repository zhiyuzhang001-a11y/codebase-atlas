from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import hashlib
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import tarfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from codebase_atlas.cli import main
from codebase_atlas.installation_publication import publish_installation
from codebase_atlas.rust_installation import load_toolchain_receipt
from codebase_atlas.rust_toolchain_installation import install_toolchain, plan_toolchain_installation
from codebase_atlas.rust_runtime import RustRuntimeError
from tests import test_rust_installation as installation_tests


class RustToolchainInstallationTests(unittest.TestCase):
    def setUp(self):
        installation_tests.RustInstallationTests.setUp(self)
        self.repo = self.base / "project"
        self.repo.mkdir()
        self.destination = self.store / "1.98.0" / "macos-arm64"
        for target in self.lock["targets"].values():
            for identity in target["components"].values():
                identity["available"] = True
        self.lock["rust_src"]["available"] = True
        for name, value in (
            ("codebase_atlas.rust_toolchain_installation.toolchain_store", self.store),
            ("codebase_atlas.rust_toolchain_installation.current_platform_target", "macos-arm64"),
            ("codebase_atlas.rust_toolchain_installation.release_lock", self.lock),
            ("codebase_atlas.rust_acquisition.release_lock", self.lock),
            ("codebase_atlas.rust_acquisition.atlas_data_root", self.base / "data"),
        ):
            patcher = patch(name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {"HOME": str(self.base), "USERPROFILE": str(self.base),
                                     "CARGO_HOME": str(self.base / "cargo"), "RUSTUP_HOME": str(self.base / "rustup")}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def install(self):
        return install_toolchain(self.repo, archives=self.archives)

    def add_member(self, member, payload=b""):
        archive = self.archives["cargo"]
        with tarfile.open(archive, "r:xz") as source:
            entries = [(entry, source.extractfile(entry).read()) for entry in source if entry.isfile()]
        with tarfile.open(archive, "w:xz") as output:
            for entry, content in entries:
                output.addfile(entry, BytesIO(content))
            member.size = len(payload)
            output.addfile(member, BytesIO(payload))
        self.lock["targets"]["macos-arm64"]["components"]["cargo"]["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()

    def test_plan_and_unauthorized_missing_cache_do_not_write(self):
        plan = plan_toolchain_installation(self.repo)
        self.assertEqual(plan["status"], "planned")
        self.assertTrue(plan["network_authorization_required"])
        with self.assertRaisesRegex(RustRuntimeError, "network authorization"):
            install_toolchain(self.repo)
        self.assertFalse(self.store.exists())
        self.assertFalse((self.base / "data").exists())

    def test_private_install_retains_resources_and_licenses_without_execution(self):
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with patch("subprocess.Popen") as spawn, patch("subprocess.run") as run:
            result = self.install()
            spawn.assert_not_called()
            run.assert_not_called()
        receipt = Path(result["receipt"])
        document = load_toolchain_receipt(receipt, store=self.store)
        root = Path(document["root"])
        self.assertEqual(len(document["files"]), 5)
        self.assertEqual(len(document["managed_licenses"]), 10)
        self.assertEqual(root, self.destination / "toolchain")
        self.assertEqual((root / "lib/rustlib/target/lib/libstd.rlib").read_bytes(), b"sentinel bytes for rust-std")
        self.assertEqual(list(self.repo.iterdir()), [])
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})
        self.assertFalse(any(path.name.startswith(".toolchain-") for path in self.store.rglob("*")))

    def test_repeat_reuses_verified_install_without_acquisition(self):
        first = self.install()
        identity = os.stat(first["receipt"])
        with patch("codebase_atlas.rust_toolchain_installation.acquire_components") as acquire:
            second = install_toolchain(self.repo, network_authorized=True)
            acquire.assert_not_called()
        self.assertTrue(second["reuse_receipt"])
        self.assertTrue(os.path.samestat(identity, os.stat(second["receipt"])))

    def test_license_damage_is_rejected_without_repair(self):
        result = self.install()
        root = Path(result["root"])
        license_path = root / "share/codebase-atlas/licenses/cargo/LICENSE-MIT"
        license_path.write_bytes(b"foreign license")
        with self.assertRaisesRegex(RustRuntimeError, "license content mismatch"):
            install_toolchain(self.repo)
        self.assertEqual(license_path.read_bytes(), b"foreign license")

    def test_incomplete_foreign_directory_is_never_overwritten_or_removed(self):
        self.store.mkdir(mode=0o700)
        self.destination.parent.mkdir(mode=0o700)
        self.destination.mkdir(mode=0o700)
        foreign = self.destination / "foreign"
        foreign.write_bytes(b"keep")
        with self.assertRaises(OSError):
            self.install()
        self.assertEqual(foreign.read_bytes(), b"keep")

    def test_checksum_mismatch_cleans_only_staging(self):
        self.archives["cargo"].write_bytes(b"damaged")
        with self.assertRaisesRegex(RustRuntimeError, "archive checksum"):
            self.install()
        self.assertFalse(self.destination.exists())
        self.assertFalse(any(path.name.startswith(".toolchain-") for path in self.store.rglob("*")))

    def test_traversal_member_cannot_escape_staging(self):
        self.add_member(tarfile.TarInfo("package/cargo/../../outside"), b"bad")
        with self.assertRaisesRegex(RustRuntimeError, "archive path is unsafe"):
            self.install()
        self.assertFalse((self.base / "outside").exists())
        self.assertFalse(self.destination.exists())

    def test_escaping_link_is_refused(self):
        member = tarfile.TarInfo("package/cargo/bin/link")
        member.type = tarfile.SYMTYPE
        member.linkname = "../../outside"
        self.add_member(member)
        with self.assertRaisesRegex(RustRuntimeError, "archive path is unsafe"):
            self.install()
        self.assertFalse(self.destination.exists())

    def test_special_file_is_refused(self):
        member = tarfile.TarInfo("package/cargo/bin/fifo")
        member.type = tarfile.FIFOTYPE
        self.add_member(member)
        with self.assertRaisesRegex(RustRuntimeError, "special file"):
            self.install()
        self.assertFalse(self.destination.exists())

    def test_internal_link_content_is_materialized_without_filesystem_links(self):
        member = tarfile.TarInfo("package/cargo/bin/cargo-copy")
        member.type = tarfile.SYMTYPE
        member.linkname = "cargo"
        self.add_member(member)
        result = self.install()
        copied = Path(result["root"]) / "bin/cargo-copy"
        self.assertFalse(copied.is_symlink())
        self.assertEqual(copied.read_bytes(), b"sentinel bytes for cargo")

    def test_archive_root_installer_is_not_extracted_or_run(self):
        self.add_member(tarfile.TarInfo("package/install.sh"), b"sentinel installer must not run")
        result = self.install()
        self.assertFalse((Path(result["root"]) / "install.sh").exists())

    def test_resource_limit_failure_cleans_staging(self):
        with patch("codebase_atlas.rust_toolchain_installation.MAX_TOTAL_BYTES", 1):
            with self.assertRaisesRegex(RustRuntimeError, "content limit"):
                self.install()
        self.assertFalse(self.destination.exists())

    def test_dangerous_environment_prevents_publication(self):
        with patch.dict(os.environ, {"RUSTC_WRAPPER": "sentinel"}):
            with self.assertRaisesRegex(RustRuntimeError, "override requires review"):
                self.install()
        self.assertFalse(self.destination.exists())
        self.assertFalse(any(path.name.startswith(".toolchain-") for path in self.store.rglob("*")))

    def test_publication_failure_rolls_back_only_owned_installation(self):
        with patch("codebase_atlas.rust_toolchain_installation.load_toolchain_receipt", side_effect=RustRuntimeError("final verification failed")):
            with self.assertRaisesRegex(RustRuntimeError, "final verification failed"):
                self.install()
        self.assertFalse(self.destination.exists())
        self.assertFalse(any(path.is_file() for path in self.store.rglob("*")))

    def test_concurrent_completed_publisher_is_reused(self):
        barrier = threading.Barrier(2)
        completed = threading.Event()
        counter_lock = threading.Lock()
        counter = 0
        def publish(*args, **kwargs):
            nonlocal counter
            with counter_lock:
                counter += 1
                first = counter == 1
            barrier.wait(timeout=10)
            if not first:
                self.assertTrue(completed.wait(timeout=10))
            try:
                return publish_installation(*args, **kwargs)
            finally:
                if first:
                    completed.set()
        with patch("codebase_atlas.rust_toolchain_installation.publish_installation", side_effect=publish):
            with ThreadPoolExecutor(max_workers=2) as workers:
                futures = [workers.submit(self.install) for _ in range(2)]
                results = [future.result(timeout=20) for future in futures]
        self.assertEqual(results[0]["receipt"], results[1]["receipt"])
        self.assertEqual(sorted(result["reuse_receipt"] for result in results), [False, True])
        load_toolchain_receipt(Path(results[0]["receipt"]), store=self.store)
        self.assertFalse(any(path.name.startswith(".toolchain-") for path in self.store.rglob("*")))

    def test_candidate_cli_install_requires_apply_and_stable_gate_stays_closed(self):
        args = ["rust-prepare", "--repo", str(self.repo)]
        with redirect_stdout(StringIO()):
            self.assertEqual(main(args + ["--apply"]), 2)
        self.assertFalse(self.store.exists())
        for component, archive in self.archives.items():
            args.extend(["--archive", component + "=" + str(archive)])
        with patch("codebase_atlas.cli.get_language", return_value=SimpleNamespace(public_enabled=True)):
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(args), 2)
            self.assertFalse(self.store.exists())
            with redirect_stdout(StringIO()):
                self.assertEqual(main(args + ["--apply"]), 0)


if __name__ == "__main__":
    unittest.main()
