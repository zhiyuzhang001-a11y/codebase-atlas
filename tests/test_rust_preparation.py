from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from codebase_atlas.cli import main
from codebase_atlas.rust_installation import verify_existing_toolchain
from codebase_atlas.rust_preparation import plan_existing_toolchain, prepare_existing_toolchain
from codebase_atlas.rust_runtime import RustRuntimeError
from tests import test_rust_installation as installation_tests


class RustPreparationTests(unittest.TestCase):
    def setUp(self):
        installation_tests.RustInstallationTests.setUp(self)
        self.repo = self.base / "project"
        self.repo.mkdir()
        for target in self.lock["targets"].values():
            for identity in target["components"].values():
                identity["available"] = True
        self.lock["rust_src"]["available"] = True
        for target, value in (
            ("codebase_atlas.rust_preparation.toolchain_store", self.store),
            ("codebase_atlas.rust_preparation.current_platform_target", "macos-arm64"),
            ("codebase_atlas.rust_acquisition.release_lock", self.lock),
            ("codebase_atlas.rust_acquisition.atlas_data_root", self.base / "data"),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {
            "HOME": str(self.base), "USERPROFILE": str(self.base),
            "CARGO_HOME": str(self.base / "cargo"), "RUSTUP_HOME": str(self.base / "rustup"),
        }, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def prepare(self, **kwargs):
        return prepare_existing_toolchain(self.repo, self.root, archives=self.archives, **kwargs)

    def test_plan_is_readonly_and_apply_reuses_all_verified_official_bytes(self):
        original = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        plan = plan_existing_toolchain(self.repo, self.root, archives=self.archives)
        self.assertEqual(plan["status"], "planned")
        self.assertEqual(plan["verified_files"], 8)
        self.assertFalse(self.store.exists())
        result = self.prepare()
        self.assertEqual(result["status"], "prepared")
        self.assertFalse(result["project_enabled"])
        self.assertFalse(result["executes_tools"])
        self.assertEqual(result["project_writes"], [])
        self.assertEqual(list(self.repo.iterdir()), [])
        self.assertEqual(original, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_repeat_and_second_project_need_neither_archives_nor_network(self):
        first = self.prepare()
        receipt = Path(first["receipt"])
        identity = os.stat(receipt)
        other = self.base / "other"
        other.mkdir()
        with patch("codebase_atlas.rust_preparation.acquire_components") as acquire:
            result = prepare_existing_toolchain(other, self.root, network_authorized=True)
            acquire.assert_not_called()
        self.assertTrue(result["reuse_receipt"])
        self.assertEqual(result["receipt"], first["receipt"])
        self.assertTrue(os.path.samestat(identity, os.stat(receipt)))
        self.assertEqual(list(other.iterdir()), [])

    def test_missing_archives_give_readonly_plan_and_unauthorized_apply_no_writes(self):
        plan = plan_existing_toolchain(self.repo, self.root)
        self.assertEqual(plan["status"], "needs_archives")
        self.assertEqual(len(plan["missing_components"]), 5)
        with self.assertRaisesRegex(RustRuntimeError, "network authorization"):
            prepare_existing_toolchain(self.repo, self.root)
        self.assertFalse(self.store.exists())
        self.assertFalse((self.base / "data").exists())

    def test_damaged_installed_library_prevents_receipt_publication(self):
        (self.root / "lib/rustlib/target/lib/libstd.rlib").write_bytes(b"tampered")
        with self.assertRaisesRegex(RustRuntimeError, "differs from official"):
            self.prepare()
        self.assertFalse(self.store.exists())

    def test_dangerous_environment_prevents_receipt_publication(self):
        with patch.dict(os.environ, {"RUSTC_WRAPPER": str(self.base / "sentinel")}):
            with self.assertRaisesRegex(RustRuntimeError, "override requires review"):
                self.prepare()
        self.assertFalse(self.store.exists())

    def test_dangerous_project_configuration_prevents_receipt_publication(self):
        cargo = self.repo / ".cargo"
        cargo.mkdir()
        config = cargo / "config.toml"
        config.write_text('[build]\nrustc-wrapper="sentinel"\n')
        before = config.read_bytes()
        with self.assertRaisesRegex(RustRuntimeError, "reviewed safe preparation"):
            self.prepare()
        self.assertEqual(config.read_bytes(), before)
        self.assertFalse(self.store.exists())

    def test_bad_existing_receipt_is_preserved_and_not_repaired(self):
        receipt = Path(self.prepare()["receipt"])
        receipt.write_text("not json")
        with self.assertRaises(RustRuntimeError):
            self.prepare()
        self.assertEqual(receipt.read_text(), "not json")

    def test_repeat_revalidates_installed_library_and_preserves_old_receipt(self):
        receipt = Path(self.prepare()["receipt"])
        before = receipt.read_bytes()
        (self.root / "lib/rustlib/target/lib/libstd.rlib").write_bytes(b"tampered")
        with self.assertRaisesRegex(RustRuntimeError, "installed content mismatch"):
            prepare_existing_toolchain(self.repo, self.root)
        self.assertEqual(receipt.read_bytes(), before)

    def test_apply_passes_explicit_acquisition_authority_without_installing_tools(self):
        with patch("codebase_atlas.rust_preparation.acquire_components", return_value=self.archives) as acquire:
            result = prepare_existing_toolchain(self.repo, self.root, network_authorized=True)
        acquire.assert_called_once_with(self.repo, "macos-arm64", network_authorized=True)
        self.assertEqual(result["status"], "prepared")
        self.assertFalse(result["executes_tools"])

    def test_failed_publication_verification_removes_only_new_receipt(self):
        with patch("codebase_atlas.rust_installation.load_toolchain_receipt", side_effect=RustRuntimeError("final verification failed")):
            with self.assertRaisesRegex(RustRuntimeError, "final verification failed"):
                self.prepare()
        self.assertFalse(any(path.is_file() for path in self.store.rglob("*")))
        self.assertEqual(list(self.repo.iterdir()), [])

    def test_project_local_toolchain_or_store_and_missing_root_are_rejected(self):
        root = self.repo / "toolchain"
        root.mkdir()
        with self.assertRaisesRegex(RustRuntimeError, "outside the project"):
            prepare_existing_toolchain(self.repo, root, archives=self.archives)
        with patch("codebase_atlas.rust_preparation.toolchain_store", return_value=self.repo / "store"):
            with self.assertRaisesRegex(RustRuntimeError, "outside the project"):
                self.prepare()
        with self.assertRaisesRegex(RustRuntimeError, "existing toolchain"):
            prepare_existing_toolchain(self.repo, self.base / "missing", archives=self.archives)
        self.assertFalse(self.store.exists())

    def test_concurrent_preparation_publishes_one_complete_receipt(self):
        barrier = threading.Barrier(2)
        def verify(*args):
            barrier.wait(timeout=10)
            return verify_existing_toolchain(*args)
        with patch("codebase_atlas.rust_preparation.verify_existing_toolchain", side_effect=verify):
            with ThreadPoolExecutor(max_workers=2) as workers:
                futures = [workers.submit(self.prepare) for _ in range(2)]
                results = [future.result(timeout=20) for future in futures]
        self.assertEqual(results[0]["receipt"], results[1]["receipt"])
        receipt = Path(results[0]["receipt"])
        self.assertEqual(list(receipt.parent.iterdir()), [receipt])
        self.assertEqual(len(json.loads(receipt.read_text())["files"]), 8)

    def test_cli_gate_remains_closed_without_preparation_side_effects(self):
        output = StringIO()
        with redirect_stdout(output):
            code = main(["rust-prepare", "--repo", str(self.repo), "--toolchain-root", str(self.root), "--apply"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "blocked")
        self.assertFalse(self.store.exists())

    def test_candidate_cli_plan_apply_and_network_without_apply_rejection(self):
        args = ["rust-prepare", "--repo", str(self.repo), "--toolchain-root", str(self.root)]
        for component, archive in self.archives.items():
            args.extend(["--archive", component + "=" + str(archive)])
        with patch("codebase_atlas.cli.get_language", return_value=SimpleNamespace(public_enabled=True)):
            output = StringIO()
            with redirect_stdout(output):
                code = main(args)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "planned")
            self.assertFalse(self.store.exists())
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(args + ["--allow-network"]), 2)
            self.assertFalse(self.store.exists())
            with redirect_stdout(StringIO()):
                self.assertEqual(main(args + ["--apply"]), 0)


if __name__ == "__main__":
    unittest.main()
