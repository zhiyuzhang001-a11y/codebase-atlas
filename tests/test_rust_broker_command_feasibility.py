import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scripts import rust_broker_command_feasibility as probe
from scripts import rust_cooperative_broker_protocol as protocol


class BrokerCommandFeasibilityTests(unittest.TestCase):
    def test_fixed_input_short_writes_are_completed_and_closed(self):
        stream = MagicMock()
        received = bytearray()

        def short_write(data):
            received.extend(data[:2])
            return min(2, len(data))

        stream.write.side_effect = short_write
        probe.feed_fixed_input(stream, protocol.FIXED_STDIN)
        self.assertEqual(bytes(received), protocol.FIXED_STDIN)
        stream.close.assert_called_once()

    def test_invalid_or_nonprogress_input_writes_fail_and_close(self):
        for count in (0, None, True, 10000):
            stream = MagicMock()
            stream.write.return_value = count
            with self.subTest(count=count), self.assertRaises(ValueError):
                probe.feed_fixed_input(stream, protocol.FIXED_STDIN)
            stream.close.assert_called_once()
        stream = MagicMock()
        with self.assertRaises(ValueError):
            probe.feed_fixed_input(stream, b"project")
        stream.write.assert_not_called()
        stream.close.assert_called_once()

    def test_three_real_owned_children_record_actual_command_after_mutation(self):
        report = {"experiments": []}
        probe.collect(report)
        self.assertEqual(report["collection_status"], "complete")
        self.assertEqual(len(report["experiments"]), 3)
        for evidence in report["experiments"]:
            self.assertEqual(evidence["actual"]["parent_id"], os.getpid())
            self.assertEqual(evidence["actual"]["argv"], ["-c", protocol.FIXTURE])
            self.assertEqual(evidence["command_identity_match"], all(evidence["field_matches"].values()))
            for key in ("argv", "cwd", "parent_id", "process_id", "stdin_sha256"):
                self.assertTrue(evidence["field_matches"][key])
            # Runtime environment differences MUST remain visible, not skipped.
            self.assertEqual(evidence["field_matches"]["env"],
                             evidence["actual"]["env"] == evidence["command_environment"])
            self.assertNotEqual(evidence["request_before"], evidence["request_after"])
            self.assertTrue(evidence["owned_cleanup_completed"])
            self.assertLessEqual(evidence["timeline"]["signaled_at"],
                                 evidence["timeline"]["cleanup_started_at"])

    def test_command_tampering_is_denied_before_native_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            command = protocol.own_fixture_command(str(Path(sys.executable).resolve()),
                                                   str(Path(temporary).resolve()),
                system_root=probe.windows_directory() if os.name == "nt" else None)
            with patch.object(probe.subprocess, "Popen") as launch:
                for changed in (replace(command, stdin=b"project"),
                                replace(command, argv=(sys.executable, "foreign")),
                                replace(command, environment=(("PATH", "foreign"),))):
                    with self.assertRaises(ValueError):
                        probe.run_fixture(changed)
                launch.assert_not_called()

    def test_exit_zero_does_not_replace_actual_identity_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            command = protocol.own_fixture_command(str(Path(sys.executable).resolve()),
                                                   str(Path(temporary).resolve()),
                system_root=probe.windows_directory() if os.name == "nt" else None)
            result = probe.run_fixture(command)
            comparison = probe.compare_actual(command, result, os.getpid())
            # Synthetic all-matched control isolates each comparator below;
            # this is not evidence that the real runtime environment matched.
            result["stdout"] = json.dumps(comparison["expected"])
            self.assertTrue(probe.compare_actual(command, result, os.getpid())["command_identity_match"])
            for key in ("argv", "cwd", "env", "parent_id", "process_id", "stdin_sha256"):
                changed = copy.deepcopy(result)
                actual = json.loads(changed["stdout"])
                actual[key] = "foreign"
                changed["stdout"] = json.dumps(actual)
                with self.subTest(key=key):
                    checked = probe.compare_actual(command, changed, os.getpid())
                    self.assertFalse(checked["command_identity_match"])
                    self.assertFalse(checked["field_matches"][key])

    def test_collection_retains_completed_repeat_when_later_execution_fails(self):
        original = probe.run_fixture
        calls = []

        def execute(command):
            calls.append(command)
            if len(calls) == 2:
                raise RuntimeError("controlled-second-repeat-failure")
            return original(command)

        report = {"experiments": []}
        with patch.object(probe, "run_fixture", side_effect=execute), self.assertRaises(RuntimeError):
            probe.collect(report)
        self.assertEqual(len(report["experiments"]), 1)
        self.assertNotIn("collection_status", report)
