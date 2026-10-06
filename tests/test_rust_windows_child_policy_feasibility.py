import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scripts import rust_windows_child_policy_feasibility as probe


class WindowsChildPolicyFeasibilityTests(unittest.TestCase):
    def test_restricted_fixture_requires_exact_native_denial(self):
        denied = OSError("blocked")
        denied.winerror = 367
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(probe, "supported_machine", return_value=True), \
                patch.object(probe.subprocess, "run", side_effect=denied):
            result = probe.fixture(Path(temporary), True)
            self.assertTrue(result["same_domain_scratch_control"])
            self.assertEqual(len(result["attempts"]), 2)
            self.assertTrue(all(item["winerror"] == 367 and not item["marker_executed"]
                                for item in result["attempts"]))

    def test_arbitrary_access_error_cannot_count_as_policy_denial(self):
        denied = OSError("access denied")
        denied.winerror = 5
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(probe, "supported_machine", return_value=True), \
                patch.object(probe.subprocess, "run", side_effect=denied):
            with self.assertRaises(RuntimeError):
                probe.fixture(Path(temporary), True)

    def test_baseline_requires_independent_execution_marker(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(probe, "supported_machine", return_value=True), \
                patch.object(probe.subprocess, "run", return_value=MagicMock(returncode=0)):
            with self.assertRaises(RuntimeError):
                probe.fixture(Path(temporary), False)

    def test_non_windows_rejected_before_native_load(self):
        with patch.object(probe, "supported_machine", return_value=False), \
                patch.object(probe.ctypes, "WinDLL", create=True) as native:
            with self.assertRaises(ValueError):
                probe.launch_fixture(Path("unused"), True)
            native.assert_not_called()

    def test_controls_use_separate_scratch(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(probe, "launch_fixture", return_value={}) as launch:
            result = probe.controller(Path(temporary))
            self.assertEqual(result["status"], "deny_all_probe_passed")
            self.assertEqual([call.args[1] for call in launch.call_args_list], [False, True])
            self.assertNotEqual(launch.call_args_list[0].args[0], launch.call_args_list[1].args[0])

    def test_collected_probe_never_admits_product(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result.json"
            with patch.object(probe, "validate_identity"), patch.object(probe, "supported_machine", return_value=True), \
                    patch.object(probe, "run_owned", return_value=MagicMock(
                        stdout='{"status":"deny_all_probe_passed"}')) as owned:
                code = probe.main(["--source-sha", "a" * 40, "--target", "windows-arm64",
                                   "--output", str(output)])
            result = json.loads(output.read_text())
            self.assertEqual(code, 0)
            self.assertEqual(result["qualification_status"], "blocked")
            self.assertFalse(result["product_enforcement"])
            self.assertFalse(result["public_rust_enabled"])
            self.assertEqual(owned.call_count, 3)

    def test_failed_controller_is_not_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result.json"
            with patch.object(probe, "validate_identity"), patch.object(probe, "supported_machine", return_value=True), \
                    patch.object(probe, "run_owned", side_effect=TimeoutError("expired")):
                code = probe.main(["--source-sha", "a" * 40, "--target", "windows-arm64",
                                   "--output", str(output)])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.read_text())["collection_status"], "failed")

    def test_failed_controller_preserves_bounded_cause(self):
        failure = probe.subprocess.CalledProcessError(1, ["owned-fixture"],
                                                     output="partial", stderr="reason" * 2000)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result.json"
            with patch.object(probe, "validate_identity"), \
                    patch.object(probe, "supported_machine", return_value=True), \
                    patch.object(probe, "run_owned", side_effect=failure):
                code = probe.main(["--source-sha", "a" * 40, "--target", "windows-arm64",
                                   "--output", str(output)])
            result = json.loads(output.read_text())
            self.assertEqual(code, 1)
            self.assertEqual(result["error"]["controller_stdout"], "partial")
            self.assertEqual(len(result["error"]["controller_stderr"]), 8192)


if __name__ == "__main__":
    unittest.main()
