"""Fail-closed discovery for the repository-aware global MCP entry."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import stat
import subprocess
from typing import Any

from .config import AtlasConfig, CONFIG_NAME
from .index_state import index_freshness, provider_database_health


@dataclass(frozen=True)
class ProjectResolution:
    status: str
    root: Path
    reason: str
    config: Path | None = None
    detail: str = ""

    def operational_status(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "status": self.status,
            "ok": self.status == "configured",
            "reason": self.reason,
            "resolved_root": str(self.root),
            "mutates": False,
            "provider_started": False,
        }
        if self.config is not None:
            value["config"] = str(self.config)
        if self.detail:
            value["detail"] = self.detail
        if self.status != "configured":
            value["next_action"] = _next_action(self)
            value["setup_argv"] = _setup_argv(self)
            value["current_session_refresh_required"] = True
        return value


def _next_action(resolution: ProjectResolution) -> str:
    argv = _setup_argv(resolution)
    if argv:
        return shlex.join(argv)
    return "fix the reported Atlas project/config identity before retrying"


def _setup_argv(resolution: ProjectResolution) -> list[str]:
    if resolution.status == "not_configured":
        return ["codebase-atlas", "onboard", "--repo", str(resolution.root)]
    if resolution.status == "index_incomplete" and resolution.config is not None:
        return ["codebase-atlas", "index", "--config", str(resolution.config)]
    return []


def _git_root(start: Path) -> Path | None:
    completed = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    raw = completed.stdout.strip()
    return Path(raw).resolve() if raw else None


def _validated_start(start: Path) -> tuple[Path | None, ProjectResolution | None]:
    expanded = start.expanduser()
    try:
        metadata = os.lstat(expanded)
    except OSError as exc:
        root = expanded.absolute()
        return None, ProjectResolution(
            "invalid_project_root", root, "project_root_unavailable", detail=str(exc)
        )
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        return None, ProjectResolution(
            "invalid_project_root",
            expanded.absolute(),
            "project_root_must_be_real_directory",
        )
    return expanded.resolve(), None


def _candidate_paths(start: Path, boundary: Path) -> list[Path]:
    candidates: list[Path] = []
    current = start
    while True:
        candidate = current / CONFIG_NAME
        if os.path.lexists(candidate):
            candidates.append(candidate)
        if current == boundary:
            return candidates
        if current.parent == current or not current.is_relative_to(boundary):
            return candidates
        current = current.parent


def resolve_project(start: Path | None = None) -> ProjectResolution:
    """Resolve exactly one current-project Atlas config without cross-project fallback."""
    selected, invalid = _validated_start(start or Path.cwd())
    if invalid is not None:
        return invalid
    assert selected is not None
    git_root = _git_root(selected)
    root = git_root or selected
    candidates = _candidate_paths(selected, root) if git_root else [root / CONFIG_NAME]
    candidates = [candidate for candidate in candidates if os.path.lexists(candidate)]
    if not candidates:
        return ProjectResolution("not_configured", root, "atlas_config_missing")
    if len(candidates) > 1:
        return ProjectResolution(
            "ambiguous_project",
            root,
            "multiple_atlas_configs_in_project",
            detail=", ".join(str(path) for path in candidates),
        )
    config_path = candidates[0]
    try:
        metadata = os.lstat(config_path)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise ValueError("Atlas config must be a regular non-symlink file")
        config = AtlasConfig.load(config_path)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        return ProjectResolution(
            "invalid_config", root, "atlas_config_invalid", config_path, str(exc)
        )
    if config.repository != root:
        return ProjectResolution(
            "repository_mismatch",
            root,
            "config_repository_does_not_match_project_root",
            config_path,
            f"configured repository: {config.repository}",
        )
    provider = provider_database_health(config.cache_dir, config.project)
    source = index_freshness(config.data_dir, config.repository, config.project)
    incomplete_source_reasons = {
        "project_not_configured",
        "index_state_missing",
        "index_state_invalid",
        "index_state_schema_changed",
        "index_state_identity_mismatch",
    }
    if not provider["ok"] or source.get("reason") in incomplete_source_reasons:
        reason = str(provider["reason"] if not provider["ok"] else source["reason"])
        return ProjectResolution(
            "index_incomplete", root, reason, config_path,
            "configuration is valid but the first index is not complete",
        )
    return ProjectResolution("configured", root, "project_config_and_index_ready", config_path)
