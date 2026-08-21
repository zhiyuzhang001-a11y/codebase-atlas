from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codebase_atlas.operations import (
    attach_operational_status,
    index_warnings,
    operational_index_status,
    stale_policy_error,
)


class OperationPolicyTests(unittest.TestCase):
    def test_warn_error_and_ignore_are_explicit(self) -> None:
        stale = {"status": "stale", "ok": False, "reason": "repository_changed"}
        self.assertEqual(index_warnings(stale, "warn")[0]["code"], "index_not_current")
        self.assertEqual(index_warnings(stale, "ignore"), [])
        self.assertIn("run codebase-atlas update", stale_policy_error(stale, "error"))
        self.assertIsNone(stale_policy_error(stale, "warn"))

    def test_status_attachment_is_machine_readable(self) -> None:
        stale = {"status": "stale", "ok": False, "reason": "repository_changed"}
        payload = attach_operational_status({"status": "ok"}, stale, "warn")
        self.assertEqual(payload["index"], stale)
        self.assertEqual(payload["warnings"][0]["status"], "stale")

    def test_missing_provider_database_overrides_fresh_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = root / "repo"
            repository.mkdir()
            status = operational_index_status(
                root / "data", repository, root / "cache", "project"
            )
            self.assertEqual(status["status"], "rebuild_required")
            self.assertEqual(status["reason"], "provider_database_missing")


if __name__ == "__main__":
    unittest.main()
