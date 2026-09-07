from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import Mock, patch

from codebase_atlas.verification_state import protected_snapshot
from codebase_atlas.simple_cli import _VerificationTransport
from tests.test_simple_cli import configured_project


class VerificationStateTests(unittest.TestCase):
    def test_raw_empty_search_requires_complete_structure(self):
        transport = Mock()
        checked = _VerificationTransport(transport)
        for payload in ({}, {"groups": []}, {"groups": [], "cols": [], "truncated": True},
                        {"groups": [], "cols": [], "status": "partial"},
                        {"groups": [{}], "cols": []}):
            with self.subTest(payload=payload):
                transport.call.return_value = payload
                with self.assertRaises(RuntimeError):
                    checked.call("search_graph", {})
        transport.call.return_value = {"groups": [], "cols": []}
        self.assertEqual(checked.call("search_graph", {}), transport.call.return_value)

    def test_git_failure_is_a_diagnostic_error(self):
        with tempfile.TemporaryDirectory() as raw:
            _repository, config, path = configured_project(Path(raw))
            with patch("codebase_atlas.verification_state.subprocess.run",
                       side_effect=subprocess.TimeoutExpired("git", 30)):
                with self.assertRaisesRegex(RuntimeError, "Git discovery failed"):
                    protected_snapshot(config, path)

    def test_symlink_target_is_not_read(self):
        with tempfile.TemporaryDirectory() as raw:
            repository, config, path = configured_project(Path(raw))
            outside = Path(raw) / "outside.txt"
            outside.write_text("first")
            link = repository / "external"
            try:
                link.symlink_to(outside)
            except OSError:
                self.skipTest("symlinks unavailable")
            first = protected_snapshot(config, path)
            outside.write_text("second")
            self.assertEqual(first, protected_snapshot(config, path))
            self.assertEqual(first[str(config.repository / link.name)], "link:" + str(outside))

    def test_detects_edits_creations_and_ignored_configuration(self):
        with tempfile.TemporaryDirectory() as raw:
            repository, config, path = configured_project(Path(raw))
            first = protected_snapshot(config, path)
            source = repository / "new.py"
            source.write_text("value = 1\n")
            second = protected_snapshot(config, path)
            self.assertNotEqual(first, second)
            source.write_text("value = 2\n")
            self.assertNotEqual(second, protected_snapshot(config, path))
            before = protected_snapshot(config, path)
            folder = repository / ".codex"
            folder.mkdir()
            (folder / "config.toml").write_text("# custom config\n")
            self.assertNotEqual(before, protected_snapshot(config, path))

    def test_budget_exhaustion_is_explicit(self):
        with tempfile.TemporaryDirectory() as raw:
            _repository, config, path = configured_project(Path(raw))
            with patch("codebase_atlas.verification_state.MAX_BYTES", 1):
                with self.assertRaisesRegex(RuntimeError, "byte budget"):
                    protected_snapshot(config, path)

    def test_repeated_snapshot_preserves_contents_and_modification_time(self):
        with tempfile.TemporaryDirectory() as raw:
            _repository, config, path = configured_project(Path(raw))
            before = path.stat().st_mtime_ns, path.read_bytes()
            self.assertEqual(protected_snapshot(config, path), protected_snapshot(config, path))
            self.assertEqual(before, (path.stat().st_mtime_ns, path.read_bytes()))
