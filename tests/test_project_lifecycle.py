from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codebase_atlas.lifecycle import ProjectOperationLease
from codebase_atlas.project_lifecycle import (
    ProjectLifecycleState,
    lifecycle_state_path,
    load_lifecycle_state,
    load_removal_marker,
    operational_lifecycle_status,
    project_recovery_root,
    publish_lifecycle_state,
    publish_removal_marker,
)


class ProjectLifecycleStateTests(unittest.TestCase):
    def test_missing_state_preserves_legacy_configured_project_as_ready(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state = load_lifecycle_state(root / "data", root, "project-a")
            self.assertEqual(state.status, "ready")
            self.assertEqual(state.repository, str(root.resolve()))
            self.assertEqual(state.operation_generation, 0)

    def test_transition_round_trip_is_identity_bound(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            data = root / "data"
            initial = ProjectLifecycleState.initial(
                root, "project-a", atlas_version="0.24.0"
            )
            stopping = initial.transition("stopping", operation_id="operation-1")
            stopped = stopping.transition("stopped")
            path = publish_lifecycle_state(data, stopped)
            self.assertEqual(path, lifecycle_state_path(data))
            loaded = load_lifecycle_state(data, root, "project-a")
            self.assertEqual(loaded.status, "stopped")
            self.assertEqual(loaded.operation_generation, 2)
            self.assertEqual(loaded.last_ready_version, "0.24.0")
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o077, 0)
            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                load_lifecycle_state(data, root / "other", "project-a")

    def test_transition_state_requires_operation_id(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state = ProjectLifecycleState.initial(Path(raw), "project-a")
            with self.assertRaisesRegex(ValueError, "operation id"):
                state.transition("updating")

    def test_operational_status_fails_closed_for_stopped_and_invalid_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            data = root / "data"
            state = ProjectLifecycleState.initial(root, "project-a").transition("stopped")
            publish_lifecycle_state(data, state)
            stopped = operational_lifecycle_status(data, root, "project-a")
            self.assertFalse(stopped["ok"])
            self.assertEqual(stopped["reason"], "project_stopped")
            lifecycle_state_path(data).write_text("not json", encoding="utf-8")
            invalid = operational_lifecycle_status(data, root, "project-a")
            self.assertFalse(invalid["ok"])
            self.assertEqual(invalid["reason"], "lifecycle_state_invalid")

    def test_unsafe_state_path_is_rejected_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            data = root / "data"
            data.mkdir()
            foreign = root / "foreign.json"
            foreign.write_text('{"foreign": true}\n', encoding="utf-8")
            lifecycle_state_path(data).symlink_to(foreign)
            before = foreign.read_bytes()
            state = ProjectLifecycleState.initial(root, "project-a")
            with self.assertRaisesRegex(ValueError, "non-symlink"):
                publish_lifecycle_state(data, state)
            self.assertEqual(foreign.read_bytes(), before)

    def test_schema_requires_exact_fields(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            data = root / "data"
            state = ProjectLifecycleState.initial(root, "project-a").to_dict()
            state["unexpected"] = True
            data.mkdir()
            lifecycle_state_path(data).write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fields"):
                load_lifecycle_state(data, root, "project-a")

    def test_target_race_is_rejected_without_overwriting_new_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            data = root / "data"
            initial = ProjectLifecycleState.initial(root, "project-a")
            path = publish_lifecycle_state(data, initial)
            replacement = initial.transition("stopped")
            real_lexists = __import__("os").path.lexists
            calls = 0

            def replace_before_second_check(candidate: object) -> bool:
                nonlocal calls
                calls += 1
                if calls == 2:
                    path.unlink()
                    path.write_text('{"foreign": true}\n', encoding="utf-8")
                return real_lexists(candidate)

            with patch(
                "codebase_atlas.project_lifecycle.os.path.lexists",
                side_effect=replace_before_second_check,
            ):
                with self.assertRaisesRegex(ValueError, "changed before publication"):
                    publish_lifecycle_state(data, replacement)
            self.assertEqual(path.read_text(encoding="utf-8"), '{"foreign": true}\n')

    def test_removal_marker_fails_closed_after_project_data_moves(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = root / "repo"
            repository.mkdir()
            state_root = root / "state"
            with patch.dict(os.environ, {"XDG_DATA_HOME": str(state_root)}):
                receipt = project_recovery_root(repository) / "op" / "receipt.json"
                publish_removal_marker(
                    repository, "project-a", "operation-1", receipt,
                    status="removed",
                )
                marker = load_removal_marker(repository)
                status = operational_lifecycle_status(
                    root / "missing-data", repository, "project-a"
                )
            self.assertEqual(marker["receipt"], str(receipt.resolve()))
            self.assertFalse(status["ok"])
            self.assertEqual(status["status"], "removed")


class ProjectOperationLeaseTests(unittest.TestCase):
    def test_same_project_is_exclusive_and_different_projects_are_independent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            data = root / "data"
            first = ProjectOperationLease(data, root, "project-a")
            duplicate = ProjectOperationLease(data, root, "project-a")
            other = ProjectOperationLease(data, root, "project-b")
            try:
                self.assertTrue(first.acquire())
                self.assertFalse(duplicate.acquire())
                self.assertTrue(other.acquire())
                self.assertIn("operation-", first.path.name)
                self.assertNotEqual(first.path, other.path)
            finally:
                first.release()
                duplicate.release()
                other.release()


if __name__ == "__main__":
    unittest.main()
