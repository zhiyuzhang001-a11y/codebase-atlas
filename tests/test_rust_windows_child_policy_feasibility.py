import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scripts import rust_windows_child_policy_feasibility as probe


class WindowsChildPolicyFeasibilityTests(unittest.TestCase):
    def setUp(self):
        directory = patch.object(probe, "windows_directory", return_value="C:\\Windows")
        directory.start()
        self.addCleanup(directory.stop)

    def test_environment_is_narrow_and_uses_os_root(self):
        with patch.dict(probe.os.environ, {"SystemRoot": "foreign", "PATH": "foreign",
                                          "RUSTC_WRAPPER": "foreign", "GITHUB_TOKEN": "foreign"}):
            environment = probe.controller_environment()
        self.assertEqual(set(environment), {"PYTHONPATH", "SystemRoot"})
        self.assertEqual(environment["SystemRoot"], "C:\\Windows")
        probe.windows_directory.assert_called_once()

    def test_detached_fixture_flags_do_not_allow_breakaway(self):
        self.assertEqual(probe.FIXTURE_CREATION_FLAGS, 0x80008)
        self.assertFalse(probe.FIXTURE_CREATION_FLAGS & 0x01000000)
        self.assertFalse(probe.FIXTURE_CREATION_FLAGS & 0x08000000)

    def test_barrier_requires_exact_checked_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            scratch = Path(temporary)
            (scratch / "membership-checked").write_bytes(b"exact-job-checked")
            probe.await_start_barrier(scratch)
            (scratch / "membership-checked").write_bytes(b"any-job")
            with self.assertRaises(ValueError):
                probe.await_start_barrier(scratch)

    def test_unreleased_membership_barrier_times_out(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(TimeoutError):
                probe.await_start_barrier(Path(temporary), timeout=0)

    def test_control_record_is_published_complete_and_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "control.json"
            probe.publish_control(path, b'{"owned": true}')
            self.assertEqual(probe.await_control(path), {"owned": True})
            self.assertFalse(path.with_name(path.name + ".staging").exists())
            with self.assertRaises(ValueError):
                probe.publish_control(path, b'{"foreign": true}')
            self.assertEqual(probe.await_control(path), {"owned": True})

    def test_missing_control_record_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(TimeoutError):
                probe.await_control(Path(temporary) / "missing", timeout=0)

    def test_parent_exit_requires_native_windows_before_loading_api(self):
        with patch.object(probe, "supported_machine", return_value=False), \
                patch.object(probe.ctypes, "WinDLL", create=True) as native:
            with self.assertRaises(ValueError):
                probe.parent_exit_experiment(Path("unused"), True)
            native.assert_not_called()

    def test_handoff_launcher_requires_explicit_policy(self):
        with self.assertRaises(SystemExit), patch.object(probe, "launch_fixture") as launch:
            probe.main(["--handoff-launcher", "unused"])
        launch.assert_not_called()

    def test_duplicate_handle_is_non_inheritable_and_object_bound(self):
        native = MagicMock()
        native.GetCurrentProcess.return_value = 123

        def duplicate(source_process, source_handle, target_process, output, access, inherit, options):
            self.assertEqual((source_process, source_handle, target_process), (123, 456, 789))
            self.assertEqual((access, inherit, options), (0, False, 2))
            output._obj.value = 987
            return True

        native.DuplicateHandle.side_effect = duplicate
        self.assertEqual(probe.duplicate_to(native, 456, 789), 987)

    def test_controller_retains_baseline_when_restricted_launch_fails(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(probe, "launch_fixture", side_effect=[
                    {"restricted": False, "fixture_reaped": True}, RuntimeError("DLL-init failed")]):
            result = probe.controller(Path(temporary))
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["failed_restricted"])
        self.assertEqual(len(result["fixtures"]), 1)

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
                    patch.object(probe, "parent_exit_experiment", return_value={"fixture_reaped": True}) as parent_exit, \
                    patch.object(probe, "run_owned", return_value=MagicMock(
                        stdout='{"status":"deny_all_probe_passed"}', returncode=0)) as owned:
                code = probe.main(["--source-sha", "a" * 40, "--target", "windows-arm64",
                                   "--output", str(output)])
            result = json.loads(output.read_text())
            self.assertEqual(code, 0)
            self.assertEqual(result["qualification_status"], "blocked")
            self.assertFalse(result["product_enforcement"])
            self.assertFalse(result["public_rust_enabled"])
            self.assertEqual(owned.call_count, 3)
            self.assertEqual(parent_exit.call_count, 6)
            self.assertEqual([call.args[1] for call in parent_exit.call_args_list], [False, True] * 3)
            self.assertEqual(len(result["parent_exit_experiments"]), 6)

    def test_parent_retains_failed_partial_fixture_report(self):
        partial = {"status": "failed", "failed_restricted": True,
                   "fixtures": [{"restricted": False, "fixture_reaped": True}]}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result.json"
            with patch.object(probe, "validate_identity"), \
                    patch.object(probe, "supported_machine", return_value=True), \
                    patch.object(probe, "run_owned", return_value=MagicMock(
                        stdout=json.dumps(partial), returncode=1)):
                code = probe.main(["--source-sha", "a" * 40, "--target", "windows-arm64",
                                   "--output", str(output)])
            result = json.loads(output.read_text())
            self.assertEqual(code, 1)
            self.assertEqual(result["collection_status"], "failed")
            self.assertEqual(result["qualification_status"], "blocked")
            self.assertEqual(len(result["experiments"][0]["fixtures"]), 1)

    def test_parent_exit_failure_keeps_gate_blocked_and_prior_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result.json"
            with patch.object(probe, "validate_identity"), \
                    patch.object(probe, "supported_machine", return_value=True), \
                    patch.object(probe, "run_owned", return_value=MagicMock(
                        stdout='{"status":"deny_all_probe_passed"}', returncode=0)), \
                    patch.object(probe, "parent_exit_experiment", side_effect=RuntimeError("worker exited early")):
                code = probe.main(["--source-sha", "a" * 40, "--target", "windows-arm64",
                                   "--output", str(output)])
            result = json.loads(output.read_text())
            self.assertEqual(code, 1)
            self.assertEqual(result["collection_status"], "failed")
            self.assertEqual(result["qualification_status"], "blocked")
            self.assertEqual(len(result["experiments"]), 3)
            self.assertEqual(result["parent_exit_experiments"], [])
            self.assertIn("worker exited early", result["error"]["message"])

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
