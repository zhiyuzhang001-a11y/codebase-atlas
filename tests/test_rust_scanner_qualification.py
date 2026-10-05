import json
import subprocess
import unittest
from unittest.mock import patch

from scripts import rust_scanner_qualification as qualification
from tests import test_rust_scanner_installation as fixtures


class ScannerQualificationTests(unittest.TestCase):
    def setUp(self):
        fixtures.RustScannerInstallationTests.setUp(self)
        self.sha = fixtures.RustScannerInstallationTests.bundle(self)
        self.archive.with_name(self.archive.name + ".sha256").write_text(self.sha + "  " + self.archive.name + "\n")

    def qualify(self, report):
        return qualification.qualify(report, self.archive, target="macos-arm64",
                                     source_sha=self.commit, project=self.repository)

    def test_same_validation_reuse_and_owned_version_route(self):
        with patch.object(qualification, "run_owned", return_value=subprocess.CompletedProcess([], 0, "atlas-rust-syntax 0.2.0 (test)", "")) as owned:
            report = {}
            self.qualify(report)
            self.assertEqual(report["status"], "passed")
            self.assertTrue(report["reused_binary"])
            self.assertEqual(report["manifest"]["source"]["commit"], self.commit)
            self.assertEqual(owned.call_args.args[0][1:], ["--version"])
            self.assertEqual(owned.call_args.kwargs["timeout"], 5)

    def test_wrong_source_cannot_execute(self):
        manifest = json.loads(self.payloads["manifest.json"])
        manifest["source"]["commit"] = "2" * 40
        self.payloads["manifest.json"] = json.dumps(manifest).encode()
        sha = fixtures.RustScannerInstallationTests.bundle(self)
        self.archive.with_name(self.archive.name + ".sha256").write_text(sha + "  " + self.archive.name + "\n")
        with patch.object(qualification, "run_owned") as owned:
            with self.assertRaises(ValueError):
                self.qualify({})
            owned.assert_not_called()
        self.assertFalse(self.store.exists())

    def test_wrong_adjacent_checksum_cannot_execute_or_publish(self):
        self.archive.with_name(self.archive.name + ".sha256").write_text("0" * 64 + "  " + self.archive.name + "\n")
        with patch.object(qualification, "run_owned") as owned:
            with self.assertRaises(ValueError):
                self.qualify({})
            owned.assert_not_called()
        self.assertFalse(self.store.exists())

    def test_failed_version_keeps_captured_evidence_and_does_not_pass(self):
        with patch.object(qualification, "run_owned", return_value=subprocess.CompletedProcess([], 1, "", "actual failure")):
            report = {}
            with self.assertRaises(subprocess.CalledProcessError):
                self.qualify(report)
            self.assertEqual(report["version_probe"]["stderr"], "actual failure")
            self.assertNotEqual(report.get("status"), "passed")


if __name__ == "__main__":
    unittest.main()
