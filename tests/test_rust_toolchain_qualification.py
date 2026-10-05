import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "rust_toolchain_qualification", Path(__file__).resolve().parents[1] / "scripts/rust_toolchain_qualification.py")
qualification = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qualification)


class OfficialToolQualificationTests(unittest.TestCase):
    def test_identity_requires_exact_sha_and_native_target(self):
        sha = "a" * 40
        with patch.object(qualification.subprocess, "run", return_value=SimpleNamespace(stdout=sha)), \
                patch.object(qualification, "current_platform_target", return_value="macos-arm64"):
            qualification.validate_identity(Path.cwd(), sha, "macos-arm64")
            for candidate, target in (("b" * 40, "macos-arm64"), (sha, "windows-arm64"), ("short", "macos-arm64")):
                with self.assertRaises(ValueError):
                    qualification.validate_identity(Path.cwd(), candidate, target)

    def test_open_public_gate_is_not_internal_qualification(self):
        with patch.object(qualification.subprocess, "run", return_value=SimpleNamespace(stdout="a" * 40)), \
                patch.object(qualification, "current_platform_target", return_value="macos-arm64"), \
                patch.object(qualification, "get_language", return_value=SimpleNamespace(public_enabled=True)):
            with self.assertRaisesRegex(ValueError, "closed public gate"):
                qualification.validate_identity(Path.cwd(), "a" * 40, "macos-arm64")

    def test_ci_requires_clean_tracked_harness_before_tools(self):
        with patch.object(qualification.subprocess, "run", side_effect=[
                SimpleNamespace(stdout="a" * 40), subprocess.CalledProcessError(1, ["git", "diff"])]), \
                patch.object(qualification, "current_platform_target", return_value="macos-arm64"), \
                patch.object(qualification, "install_toolchain") as install:
            with self.assertRaises(subprocess.CalledProcessError):
                qualification.validate_identity(Path.cwd(), "a" * 40, "macos-arm64", require_clean=True)
            install.assert_not_called()

    def test_fixture_environment_is_isolated_and_restored_even_on_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            with patch.dict(os.environ, {"RUSTC_WRAPPER": "foreign", "DYLD_INSERT_LIBRARIES": "foreign"}):
                original = dict(os.environ)
                with self.assertRaisesRegex(RuntimeError, "fixture"):
                    with qualification.isolated_environment(base, base / "data") as removed:
                        self.assertIn("RUSTC_WRAPPER", removed)
                        self.assertNotIn("RUSTC_WRAPPER", os.environ)
                        self.assertEqual(os.environ["CARGO_HOME"], str(base / "cargo"))
                        raise RuntimeError("fixture")
                self.assertEqual(dict(os.environ), original)

    def test_version_requires_exact_pinned_tool_name_and_version(self):
        for name, prefix in (("cargo", "cargo"), ("rustc", "rustc"), ("analyzer", "rust-analyzer")):
            qualification.validate_version(name, prefix + " 1.98.0 (official)")
            for output in (prefix + " 1.98.1", prefix + " 1.98.00", "foreign 1.98.0"):
                with self.assertRaises(ValueError):
                    qualification.validate_version(name, output)

    def test_failed_identity_preserves_failure_evidence_without_acquisition(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence.json"
            args = ["qualification", "--source-sha", "a" * 40, "--target", "macos-arm64",
                    "--output", str(output), "--require-clean-source"]
            with patch("sys.argv", args), \
                    patch.object(qualification, "validate_identity", side_effect=ValueError("source mismatch")), \
                    patch.object(qualification, "qualify") as qualify:
                self.assertEqual(qualification.main(), 1)
                qualify.assert_not_called()
            report = json.loads(output.read_text())
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["error"]["message"], "source mismatch")
            with patch("sys.argv", args), patch.object(qualification, "validate_identity") as identity:
                with self.assertRaises(FileExistsError):
                    qualification.main()
                identity.assert_not_called()
            self.assertEqual(json.loads(output.read_text()), report)

    def test_qualification_reuses_offline_receipt_and_only_runs_three_versions(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            receipt = base / "receipt.json"
            receipt.write_text("{}")
            result = {"receipt": str(receipt), "reuse_receipt": True}
            tools = {name: SimpleNamespace(path=base / name, sha256="a" * 64)
                     for name in ("cargo", "rustc", "analyzer")}
            runtime = SimpleNamespace(**tools, environment=lambda repo: {"CARGO_NET_OFFLINE": "true"})
            outputs = [subprocess.CompletedProcess([], 0, prefix + " 1.98.0", "")
                       for prefix in ("cargo", "rustc", "rust-analyzer")]
            with patch.object(qualification, "install_toolchain", return_value=result) as install, \
                    patch.object(qualification, "load_toolchain_receipt", return_value={}), \
                    patch.object(qualification, "runtime_from_receipt", return_value=runtime), \
                    patch.object(qualification, "run_owned", side_effect=outputs) as owned:
                report = {}
                qualification.qualify(report, base, base / "data", allow_network=False)
                self.assertEqual(report["status"], "passed")
                self.assertEqual(install.call_count, 2)
                self.assertTrue(all(call.kwargs == {"network_authorized": False} for call in install.call_args_list))
                self.assertEqual(owned.call_count, 3)
                for call in owned.call_args_list:
                    self.assertEqual(call.args[0][1:], ["--version"])
                    self.assertEqual(call.kwargs["timeout"], 5)


if __name__ == "__main__":
    unittest.main()
