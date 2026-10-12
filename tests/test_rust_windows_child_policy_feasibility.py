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

    def test_expired_barrier_cannot_run_even_if_receipt_present(self):
        with tempfile.TemporaryDirectory() as temporary:
            scratch = Path(temporary)
            (scratch / "membership-checked").write_bytes(b"exact-job-checked")
            with patch.object(probe, "monotonic", return_value=10), self.assertRaises(TimeoutError):
                probe.await_start_barrier(scratch, deadline=9)

    def test_armed_deadline_requires_remaining_crash_budget(self):
        record = {"armed_at": 10.0, "barrier_deadline": 15.0}
        self.assertEqual(probe.armed_remaining(record, 11, 3), 4)
        for now in (9, 12, 15):
            with self.assertRaises(ValueError):
                probe.armed_remaining(record, now, 3)
        for record in ({}, {"armed_at": float("nan"), "barrier_deadline": 15},
                       {"armed_at": True, "barrier_deadline": 6},
                       {"armed_at": 10, "barrier_deadline": 16}):
            with self.assertRaises(ValueError):
                probe.armed_remaining(record, 11, 3)

    def test_armed_deadline_validates_construction_not_cancelling_subtraction(self):
        # Across a float exponent boundary, (start + 5) - start need not equal 5.
        start = 60.00000000000001
        deadline = start + 5
        self.assertNotEqual(deadline - start, 5)
        self.assertGreater(probe.armed_remaining(
            {"armed_at": start, "barrier_deadline": deadline}, start + 1, 3), 3)
        with self.assertRaises(ValueError):
            probe.armed_remaining({"armed_at": start, "barrier_deadline": deadline + 0.01},
                                  start + 1, 3)

    def test_sanitized_receipts_remove_native_handle_values(self):
        record = {"worker_handle": 123, "observer_handle": 456, "job_handle": 789,
                  "process_id": 42, "exact_job_checked_at": 10.0,
                  "tested_job_handle_transferred": False}
        self.assertEqual(probe.sanitized_control(record), {
            "process_id": 42, "exact_job_checked_at": 10.0,
            "tested_job_handle_transferred": False})
        self.assertEqual(record["worker_handle"], 123)

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

    def test_control_retries_only_explicit_windows_sharing_errors(self):
        for code in (32, 33):
            error = PermissionError(13, "controlled sharing conflict")
            error.winerror = code
            path = MagicMock()
            path.exists.return_value = True
            path.read_text.side_effect = [error, '{"owned": true}']
            with patch.object(probe, "sleep"):
                self.assertEqual(probe.await_control(path), {"owned": True})
            self.assertEqual(path.read_text.call_count, 2)
        for code in (None, 5):
            error = PermissionError(13, "controlled access denial")
            error.winerror = code
            path = MagicMock()
            path.exists.return_value = True
            path.read_text.side_effect = error
            with self.assertRaises(PermissionError):
                probe.await_control(path)
            self.assertEqual(path.read_text.call_count, 1)

    def test_control_sharing_retry_does_not_reset_deadline(self):
        error = PermissionError(13, "controlled sharing conflict")
        error.winerror = 32
        path = MagicMock()
        path.exists.return_value = True
        path.read_text.side_effect = error
        with patch.object(probe, "monotonic", side_effect=[0, 0, 0, 5]), \
                patch.object(probe, "sleep"):
            with self.assertRaises(TimeoutError):
                probe.await_control(path, timeout=5)
        self.assertEqual(path.read_text.call_count, 1)

    def test_control_late_read_and_invalid_json_are_not_accepted(self):
        path = MagicMock()
        path.exists.return_value = True
        path.read_text.return_value = '{"owned": true}'
        with patch.object(probe, "monotonic", side_effect=[0, 0, 5]):
            with self.assertRaises(TimeoutError):
                probe.await_control(path, timeout=5)
        with patch.object(probe, "monotonic", side_effect=[0, 0, 0, 5]):
            with self.assertRaises(TimeoutError):
                probe.await_control(path, timeout=5)
        path.read_text.return_value = '{'
        with self.assertRaises(json.JSONDecodeError):
            probe.await_control(path)

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

    def test_loss_holder_requires_explicit_policy(self):
        with self.assertRaises(SystemExit), patch.object(probe, "launch_fixture") as launch:
            probe.main(["--loss-holder", "unused"])
        launch.assert_not_called()

    def test_disconnect_worker_cannot_be_used_as_public_mode(self):
        with self.assertRaises(SystemExit):
            probe.main(["--disconnect-worker"])

    def test_holder_loss_requires_native_windows_before_api_load(self):
        with patch.object(probe, "supported_machine", return_value=False), \
                patch.object(probe.ctypes, "WinDLL", create=True) as native:
            with self.assertRaises(ValueError):
                probe.holder_loss_experiment(Path("unused"), True, True)
            native.assert_not_called()

    def test_disconnect_worker_positive_and_armed_record_precede_barrier(self):
        with tempfile.TemporaryDirectory() as temporary:
            scratch = Path(temporary)

            def release(path, *, deadline):
                self.assertEqual(path, scratch)
                self.assertEqual((scratch / "armed-positive").read_bytes(), b"armed-positive")
                armed = probe.await_control(scratch / "worker-armed.json")
                self.assertTrue(armed["same_domain_armed_positive"])
                self.assertEqual(armed["barrier_deadline"], deadline)
                self.assertEqual(deadline, armed["armed_at"] + 5)
                self.assertFalse((scratch / "after-disconnect-barrier").exists())

            with patch.object(probe, "await_start_barrier", side_effect=release), \
                    patch.object(probe, "fixture", return_value={"restricted": True}) as fixture:
                result = probe.disconnect_worker(scratch, True)
            fixture.assert_called_once_with(scratch, True)
            self.assertTrue(result["after_barrier_executed"])
            self.assertEqual((scratch / "after-disconnect-barrier").read_bytes(), b"executed-after-barrier")

    def test_unreleased_disconnect_worker_never_runs_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            scratch = Path(temporary)
            with patch.object(probe, "await_start_barrier", side_effect=TimeoutError("not released")), \
                    patch.object(probe, "fixture") as fixture:
                with self.assertRaises(TimeoutError):
                    probe.disconnect_worker(scratch, False)
            fixture.assert_not_called()
            self.assertFalse((scratch / "after-disconnect-barrier").exists())

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

    def test_observer_handle_can_be_limited_to_duplication_access(self):
        native = MagicMock()
        native.GetCurrentProcess.return_value = 123

        def duplicate(source, handle, target, output, access, inherit, options):
            self.assertEqual((access, inherit, options), (0x40, False, 0))
            output._obj.value = 987
            return True

        native.DuplicateHandle.side_effect = duplicate
        self.assertEqual(probe.duplicate_to(native, 456, 789, desired_access=0x40), 987)

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
                    patch.object(probe, "holder_loss_experiment", return_value={"owned_job_cleanup_completed": True}) as loss, \
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
            self.assertEqual(loss.call_count, 12)
            self.assertEqual([(call.args[1], call.args[2]) for call in loss.call_args_list],
                             [(False, False), (False, True), (True, False), (True, True)] * 3)
            self.assertEqual(len(result["holder_loss_experiments"]), 12)

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

    def test_holder_loss_failure_keeps_gate_blocked_and_prior_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result.json"
            with patch.object(probe, "validate_identity"), \
                    patch.object(probe, "supported_machine", return_value=True), \
                    patch.object(probe, "run_owned", return_value=MagicMock(
                        stdout='{"status":"deny_all_probe_passed"}', returncode=0)), \
                    patch.object(probe, "parent_exit_experiment", return_value={}), \
                    patch.object(probe, "holder_loss_experiment", side_effect=TimeoutError("worker survived")):
                code = probe.main(["--source-sha", "a" * 40, "--target", "windows-arm64",
                                   "--output", str(output)])
            result = json.loads(output.read_text())
            self.assertEqual(code, 1)
            self.assertEqual(result["collection_status"], "failed")
            self.assertEqual(result["qualification_status"], "blocked")
            self.assertEqual(len(result["experiments"]), 3)
            self.assertEqual(len(result["parent_exit_experiments"]), 6)
            self.assertEqual(result["holder_loss_experiments"], [])
            self.assertIn("worker survived", result["error"]["message"])

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
