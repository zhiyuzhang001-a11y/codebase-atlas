from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from codebase_atlas.cli import main
from codebase_atlas.project_discovery import resolve_project


def write_config(root: Path, repository: Path | None = None, project: str = "") -> Path:
    data = root / ".atlas-data"
    config = root / ".codebase-atlas.toml"
    config.write_text(
        "schema_version = 1\n\n[project]\n"
        f'repository = {json.dumps(str(repository or root))}\n'
        'language = "python"\n'
        f'data_dir = {json.dumps(str(data))}\n'
        f'cbm_project = "{project}"\n'
        'tsconfig = ""\n\n[runtime]\n'
        f'node = {json.dumps(str(root / "node"))}\n'
        f'node_bin_dir = {json.dumps(str(root))}\n'
        f'cbm_binary = {json.dumps(str(root / "cbm"))}\n'
        f'serena_python = {json.dumps(str(root / "python"))}\n',
        encoding="utf-8",
    )
    return config


def make_ready(root: Path, project: str) -> None:
    data = root / ".atlas-data"
    cache = data / "codebase-memory"
    cache.mkdir(parents=True)
    (cache / f"{project}.db").write_bytes(b"ready")
    (data / "index-state.json").write_text(json.dumps({
        "schema_version": 1,
        "repository": str(root.resolve()),
        "project": project,
        "mode": "incremental",
        "source_kind": "unknown",
        "source_fingerprint": None,
        "source_head": None,
        "changed_paths": None,
        "updated_at": "2026-08-29T00:00:00+00:00",
    }), encoding="utf-8")


class ProjectDiscoveryTests(unittest.TestCase):
    def test_non_git_missing_and_incomplete_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.assertEqual(resolve_project(root).status, "not_configured")
            write_config(root)
            resolution = resolve_project(root)
            self.assertEqual(resolution.status, "index_incomplete")
            self.assertEqual(resolution.reason, "project_not_configured")

    def test_ready_non_git_config_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            write_config(root, project="ready-project")
            make_ready(root, "ready-project")
            resolution = resolve_project(root)
            self.assertEqual(resolution.status, "configured")
            self.assertEqual(resolution.root, root.resolve())

    def test_git_subdirectory_finds_root_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            write_config(root)
            nested = root / "src/deep"
            nested.mkdir(parents=True)
            resolution = resolve_project(nested)
            self.assertEqual(resolution.status, "index_incomplete")
            self.assertEqual(resolution.root, root.resolve())

    def test_ancestor_above_git_root_is_not_used(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            write_config(workspace)
            repository = workspace / "repo"
            repository.mkdir()
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            self.assertEqual(resolve_project(repository).status, "not_configured")

    def test_multiple_configs_are_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            write_config(root)
            nested = root / "nested"
            nested.mkdir()
            write_config(nested, repository=root)
            self.assertEqual(resolve_project(nested).status, "ambiguous_project")

    def test_invalid_symlink_and_repository_mismatch_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "target"
            target.write_text("not toml", encoding="utf-8")
            (root / ".codebase-atlas.toml").symlink_to(target)
            self.assertEqual(resolve_project(root).status, "invalid_config")
            (root / ".codebase-atlas.toml").unlink()
            write_config(root, repository=root / "other")
            self.assertEqual(resolve_project(root).status, "repository_mismatch")

    def test_explicit_root_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            real = root / "real"
            real.mkdir()
            link = root / "link"
            link.symlink_to(real, target_is_directory=True)
            self.assertEqual(resolve_project(link).status, "invalid_project_root")

    def test_discovery_does_not_change_dirty_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.py"
            source.write_text("dirty = True\n", encoding="utf-8")
            before = source.read_bytes()
            resolve_project(root)
            self.assertEqual(source.read_bytes(), before)

    def test_mcp_auto_unconfigured_builds_status_only_server(self) -> None:
        with tempfile.TemporaryDirectory(prefix="项目 with spaces ") as raw:
            root = Path(raw)
            with patch("codebase_atlas.cli._run_mcp_with_graceful_termination") as run:
                self.assertEqual(main(["mcp-auto", "--root", str(root)]), 0)
            server = run.call_args.args[0]
            self.assertIsNone(server.service)
            self.assertEqual(server.index_status["status"], "not_configured")
            self.assertEqual(
                server.index_status["setup_argv"],
                ["codebase-atlas", "onboard", "--repo", str(root.resolve())],
            )


if __name__ == "__main__":
    unittest.main()
