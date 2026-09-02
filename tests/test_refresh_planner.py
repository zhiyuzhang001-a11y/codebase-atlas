from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from codebase_atlas.cli import main
from codebase_atlas.config import AtlasConfig
from codebase_atlas.index_state import RepositorySnapshot, record_index_state, state_path
from codebase_atlas.refresh_planner import (
    MANIFEST_NAME,
    RefreshPlanError,
    build_generation_manifest,
    manifest_path,
    plan_refresh,
    stage_generation_manifest_candidate,
    validate_generation_manifest,
)


def git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
    )


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RefreshPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repo"
        self.repository.mkdir()
        git(self.repository, "init", "-q")
        git(self.repository, "config", "user.email", "atlas@example.invalid")
        git(self.repository, "config", "user.name", "Atlas Test")
        (self.repository / ".gitignore").write_text("ignored/\n*.generated.py\n")
        (self.repository / "sample.py").write_text("def value():\n    return 1\n")
        git(self.repository, "add", ".gitignore", "sample.py")
        git(self.repository, "commit", "-qm", "initial")
        self.data = self.root / "data"
        self.project = "project"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def manifest(self, *, language: str = "python") -> dict[str, object]:
        return build_generation_manifest(
            self.repository,
            self.project,
            language,
            generation_id="generation-1",
            provider_identity={"sha256": "a" * 64},
            sidecar_identity={"sha256": "b" * 64},
            created_at="2026-09-02T00:00:00+00:00",
        )

    def publish_fixture(self, value: dict[str, object] | None = None) -> None:
        self.data.mkdir(parents=True, exist_ok=True)
        manifest_path(self.data).write_text(
            json.dumps(value or self.manifest(), indent=2) + "\n",
            encoding="utf-8",
        )

    def test_v1_requires_explicit_full_baseline_without_mutation(self) -> None:
        record_index_state(self.data, self.repository, self.project, "fast")
        before = state_path(self.data).read_bytes()
        result = plan_refresh(self.data, self.repository, self.project, "python")
        self.assertEqual(result["status"], "full_baseline_required")
        self.assertEqual(result["full_fallback_reason"], "index_state_v1_has_no_file_manifest")
        self.assertEqual(state_path(self.data).read_bytes(), before)
        self.assertFalse(manifest_path(self.data).exists())

    def test_noop_is_empty_and_five_runs_are_identical(self) -> None:
        self.publish_fixture()
        encoded = [
            json.dumps(
                plan_refresh(self.data, self.repository, self.project, "python"),
                sort_keys=True,
                separators=(",", ":"),
            )
            for _ in range(5)
        ]
        self.assertEqual(len(set(encoded)), 1)
        result = json.loads(encoded[0])
        self.assertEqual(result["dirty_paths"], [])
        self.assertEqual(result["route"], "same_connection")

    def test_candidate_staging_is_durable_deterministic_and_never_published(self) -> None:
        manifest = self.manifest()
        with stage_generation_manifest_candidate(
            self.data, manifest, self.repository, self.project
        ) as staged:
            self.assertTrue(staged.temporary.is_file())
            self.assertEqual(json.loads(staged.temporary.read_text()), manifest)
            self.assertFalse(manifest_path(self.data).exists())
            temporary = staged.temporary
        self.assertFalse(temporary.exists())
        self.assertFalse(manifest_path(self.data).exists())

    def test_modify_add_delete_and_rename_have_exact_dirty_sets(self) -> None:
        for mutation, expected, change_key in (
            (lambda: (self.repository / "sample.py").write_text("value = 2\n"), ["sample.py"], "modified"),
            (lambda: (self.repository / "added.py").write_text("value = 2\n"), ["added.py"], "added"),
            (lambda: (self.repository / "sample.py").unlink(), ["sample.py"], "deleted"),
            (lambda: (self.repository / "sample.py").rename(self.repository / "renamed.py"), ["renamed.py", "sample.py"], "renamed"),
        ):
            git(self.repository, "reset", "--hard", "-q", "HEAD")
            (self.repository / "added.py").unlink(missing_ok=True)
            (self.repository / "renamed.py").unlink(missing_ok=True)
            self.publish_fixture(self.manifest())
            mutation()
            result = plan_refresh(self.data, self.repository, self.project, "python")
            self.assertEqual(result["dirty_paths"], expected)
            if change_key == "renamed":
                self.assertEqual(
                    [(item["from"], item["to"]) for item in result["changes"]["renamed"]],
                    [("sample.py", "renamed.py")],
                )
            else:
                self.assertEqual(result["changes"][change_key], expected)

    def test_import_call_rapid_overwrite_syntax_error_and_repair_use_content(self) -> None:
        mutations = (
            "import os\ndef value():\n    return 1\n",
            "def helper(): return 2\ndef value():\n    return helper()\n",
            "value = 2\n",
            "def broken(:\n",
            "def repaired():\n    return 3\n",
        )
        for content in mutations:
            self.publish_fixture(self.manifest())
            (self.repository / "sample.py").write_text(content)
            result = plan_refresh(self.data, self.repository, self.project, "python")
            self.assertEqual(result["dirty_paths"], ["sample.py"])
            git(self.repository, "reset", "--hard", "-q", "HEAD")

    def test_ignored_metadata_and_cross_language_inventory(self) -> None:
        (self.repository / "ignored").mkdir()
        (self.repository / "ignored/hidden.py").write_text("value = 2\n")
        (self.repository / ".codebase-atlas.toml").write_text("operational = true\n")
        manifest = self.manifest()
        self.assertEqual([item["path"] for item in manifest["files"]], ["sample.py"])

        (self.repository / "app.ts").write_text("export const value = 1;\n")
        typescript = self.manifest(language="typescript")
        self.assertEqual([item["path"] for item in typescript["files"]], ["app.ts"])

    def test_symlink_escape_is_rejected(self) -> None:
        outside = self.root / "outside.py"
        outside.write_text("value = 1\n")
        try:
            (self.repository / "linked.py").symlink_to(outside)
        except OSError:
            self.skipTest("symlinks are unavailable")
        git(self.repository, "add", "linked.py")
        with self.assertRaisesRegex(RefreshPlanError, "escapes repository boundary"):
            self.manifest()

    def test_foreign_invalid_and_duplicate_manifests_fail_closed(self) -> None:
        valid = self.manifest()
        mutations = (
            ("repository", str(self.root / "foreign"), "repository identity"),
            ("project", "foreign", "project identity"),
            ("schema_version", 99, "schema"),
        )
        for key, value, message in mutations:
            candidate = dict(valid)
            candidate[key] = value
            with self.assertRaisesRegex(RefreshPlanError, message):
                validate_generation_manifest(candidate, self.repository, self.project)
        duplicate = dict(valid)
        duplicate["files"] = [valid["files"][0], dict(valid["files"][0])]
        with self.assertRaisesRegex(RefreshPlanError, "duplicate"):
            validate_generation_manifest(duplicate, self.repository, self.project)

    def test_snapshot_race_is_rejected(self) -> None:
        stable = RepositorySnapshot("git", "a" * 64, "head", 0, "snapshot_complete")
        changed = RepositorySnapshot("git", "b" * 64, "head", 1, "snapshot_complete")
        with patch(
            "codebase_atlas.refresh_planner.repository_snapshot",
            side_effect=[stable, changed],
        ):
            with self.assertRaisesRegex(RefreshPlanError, "snapshot_changed_during_plan"):
                self.manifest()

    def test_planner_preserves_provider_sidecar_state_and_config_bytes(self) -> None:
        self.publish_fixture()
        provider = self.data / "provider.db"
        sidecar = self.data / "python-registrations.json"
        config = self.data / "atlas.toml"
        state = self.data / "index-state.json"
        for path, payload in (
            (provider, b"provider"),
            (sidecar, b"sidecar"),
            (config, b"config"),
            (state, b"state"),
        ):
            path.write_bytes(payload)
        before = {path: sha(path) for path in (provider, sidecar, config, state)}
        plan_refresh(self.data, self.repository, self.project, "python")
        self.assertEqual(
            {path: sha(path) for path in (provider, sidecar, config, state)}, before
        )

    def test_python_inventory_consistency_and_query_path_is_disconnected(self) -> None:
        from codebase_atlas.providers.python_inventory import python_source_files

        manifest_paths = [item["path"] for item in self.manifest()["files"]]
        provider_paths = [
            path.relative_to(self.repository.resolve()).as_posix()
            for path in python_source_files(self.repository)
        ]
        self.assertEqual(manifest_paths, provider_paths)
        package = Path(__file__).parents[1] / "src/codebase_atlas"
        for name in ("service.py", "mcp.py"):
            self.assertNotIn("refresh_planner", (package / name).read_text(encoding="utf-8"))

    def test_cli_plan_refresh_is_structured_and_read_only(self) -> None:
        for name in ("node", "cbm", "serena"):
            (self.root / name).touch()
        config = AtlasConfig(
            self.repository,
            "python",
            self.root / "node",
            self.root / "cbm",
            self.root / "serena",
            self.data,
            self.project,
        )
        config_path = self.root / "atlas.toml"
        config.write(config_path)
        record_index_state(self.data, self.repository, self.project, "fast")
        before = state_path(self.data).read_bytes()
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["plan-refresh", "--config", str(config_path)]), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "full_baseline_required")
        self.assertEqual(state_path(self.data).read_bytes(), before)
        self.assertFalse((self.data / MANIFEST_NAME).exists())


if __name__ == "__main__":
    unittest.main()
