import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scripts import rust_landlock_feasibility as probe


class LandlockFeasibilityTests(unittest.TestCase):
    def test_other_platform_never_loads_native_syscalls(self):
        with patch.object(probe, "supported_machine", return_value=False), \
                patch.object(probe.ctypes, "CDLL") as library:
            with self.assertRaises(ValueError):
                probe.experiment(Path("/unused"))
            library.assert_not_called()

    def test_low_abi_is_blocked_without_enforcement(self):
        native = MagicMock()
        native.syscall.return_value = 3
        with patch.object(probe, "supported_machine", return_value=True), \
                patch.object(probe.ctypes, "CDLL", return_value=native):
            result = probe.experiment(Path("/unused"))
        self.assertEqual(result["status"], "blocked")
        native.syscall.assert_called_once()
        native.prctl.assert_not_called()

    def test_collected_probe_cannot_qualify_product(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result.json"
            with patch.object(probe, "validate_identity"), \
                    patch.object(probe, "supported_machine", return_value=True), \
                    patch.object(probe, "run_owned", return_value=MagicMock(
                        stdout=json.dumps({"status": "resource_probe_passed"}))) as owned:
                code = probe.main(["--source-sha", "a" * 40, "--target", "linux-arm64",
                                   "--output", str(output)])
            result = json.loads(output.read_text())
            self.assertEqual(code, 0)
            self.assertEqual(result["qualification_status"], "blocked")
            self.assertFalse(result["product_enforcement"])
            self.assertFalse(result["public_rust_enabled"])
            self.assertEqual(owned.call_count, 3)
            self.assertEqual([item["repeat"] for item in result["experiments"]], [1, 2, 3])

    def test_unknown_helper_status_rejects_collection(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result.json"
            with patch.object(probe, "validate_identity"), \
                    patch.object(probe, "supported_machine", return_value=True), \
                    patch.object(probe, "run_owned", return_value=MagicMock(stdout='{"status":"passed"}')):
                code = probe.main(["--source-sha", "a" * 40, "--target", "linux-arm64",
                                   "--output", str(output)])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.read_text())["collection_status"], "failed")

    def test_failed_child_is_not_collected_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result.json"
            with patch.object(probe, "validate_identity"), \
                    patch.object(probe, "supported_machine", return_value=True), \
                    patch.object(probe, "run_owned", side_effect=TimeoutError("expired")):
                code = probe.main(["--source-sha", "a" * 40, "--target", "linux-x86_64",
                                   "--output", str(output)])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.read_text())["collection_status"], "failed")


if __name__ == "__main__":
    unittest.main()
