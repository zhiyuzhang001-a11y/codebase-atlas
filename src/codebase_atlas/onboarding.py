"""Read-only-first onboarding plans composed from existing Atlas operations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shlex
import stat
from typing import Any, Callable

from .config import AtlasConfig, default_data_dir, diagnose
from .index_state import index_freshness, provider_database_health, record_index_state, repository_snapshot
from .runtime import required_checks_ok, runtime_checks


@dataclass(frozen=True)
class OnboardingInputs:
    repository: Path
    config_path: Path
    language: str | None
    node: Path | None
    cbm_binary: Path | None
    serena_python: Path | None
    node_bin_dir: Path | None
    tsconfig: Path | None
    data_dir: Path | None
    mode: str


def _safe_path(path: Path, *, label: str, file_target: bool, anchor: Path | None = None) -> str | None:
    absolute = path.absolute()
    trusted_ancestors = set((anchor.absolute(), *anchor.absolute().parents)) if anchor is not None else set()
    for candidate in (absolute, *absolute.parents):
        if candidate.is_symlink():
            # A repository selected through macOS's /var alias retains that
            # literal ancestor. Never trust a later child symlink merely
            # because its destination happens to contain the repository.
            if candidate in trusted_ancestors:
                continue
            return f"{label} or an ancestor is a symlink"
    existing = next((candidate for candidate in (absolute, *absolute.parents) if candidate.exists()), None)
    if existing is None:
        return f"{label} has no accessible parent"
    if existing == path:
        if file_target and not absolute.is_file():
            return f"{label} is not a regular file"
        if not file_target and not absolute.is_dir():
            return f"{label} is not a directory"
    elif not existing.is_dir():
        return f"{label} parent is not a directory"
    return None


def _content_fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_identity(path: Path) -> tuple[int, int]:
    value = os.lstat(path)
    if not stat.S_ISREG(value.st_mode):
        raise ValueError("config is not a regular file")
    return value.st_dev, value.st_ino


def _matches_config(path: Path, fingerprint: str, identity: tuple[int, int]) -> bool:
    try:
        return _file_identity(path) == identity and _content_fingerprint(path) == fingerprint
    except OSError:
        return False


def _action(identifier: str, *, mutates: bool, target: Path | str, reason: str, command: str) -> dict[str, object]:
    return {"id": identifier, "mutates": mutates, "target": str(target), "reason": reason, "command": command}


def _actions(repository: Path, config_path: Path, config: AtlasConfig, *, reusing: bool) -> list[dict[str, object]]:
    return [
        _action("check_runtime", mutates=False, target=repository, reason="verify local prerequisites", command="codebase-atlas setup"),
        _action("reuse_config" if reusing else "create_config", mutates=not reusing, target=config_path, reason="reuse visible config" if reusing else "create visible project config", command="codebase-atlas init"),
        _action("index", mutates=True, target=config.data_dir, reason="build or verify Atlas-owned index", command="codebase-atlas index"),
        _action("doctor", mutates=False, target=config_path, reason="verify readiness", command="codebase-atlas doctor"),
    ]


def _configuration_conflict(config: AtlasConfig, inputs: OnboardingInputs, requested_repo: Path) -> str | None:
    comparisons: tuple[tuple[str, object | None, object | None], ...] = (
        ("repository", requested_repo.resolve(), config.repository.resolve()),
        ("language", inputs.language, config.language),
        ("node", inputs.node, config.node),
        ("cbm_binary", inputs.cbm_binary, config.cbm_binary),
        ("serena_python", inputs.serena_python, config.serena_python),
        ("node_bin_dir", inputs.node_bin_dir, config.node_bin_dir),
        ("tsconfig", inputs.tsconfig, config.tsconfig),
        ("data_dir", inputs.data_dir, config.data_dir),
    )
    for name, requested, existing in comparisons:
        if requested is not None and existing is not None and isinstance(requested, Path) and isinstance(existing, Path):
            equal = requested.resolve() == existing.resolve()
        else:
            equal = requested == existing
        if requested is not None and not equal:
            return f"existing config differs for {name}; choose a new --config path"
    return None


def build_plan(inputs: OnboardingInputs) -> tuple[dict[str, object], AtlasConfig | None]:
    repo = inputs.repository.resolve()
    literal_anchor = inputs.repository.absolute()
    literal_config_path = inputs.config_path.absolute()
    path_error = _safe_path(literal_config_path, label="config path", file_target=literal_config_path.exists(), anchor=literal_anchor)
    # Validate the user's literal path before normalization so an untrusted
    # symlink cannot disappear during resolution. Once accepted, serialize one
    # canonical path everywhere the plan can execute or be replayed.
    config_path = literal_config_path if path_error else literal_config_path.resolve()
    configured: AtlasConfig | None = None
    if config_path.is_file() and not path_error:
        try:
            configured = AtlasConfig.load(config_path)
            path_error = _configuration_conflict(configured, inputs, repo)
        except (OSError, KeyError, ValueError) as error:
            path_error = f"existing config is unreadable: {error}"
    if configured and not path_error:
        repo, language = configured.repository, configured.language
        node, cbm, serena = configured.node, configured.cbm_binary, configured.serena_python
        node_bin, tsconfig, data_dir = configured.node_bin_dir, configured.tsconfig, configured.data_dir
    else:
        language = inputs.language or ("typescript" if inputs.tsconfig or (repo / "tsconfig.json").is_file() else "python")
        node, cbm, serena = inputs.node, inputs.cbm_binary, inputs.serena_python
        node_bin, tsconfig, data_dir = inputs.node_bin_dir, inputs.tsconfig, inputs.data_dir
    resolved_data_dir = data_dir or default_data_dir(repo)
    path_error = path_error or _safe_path(resolved_data_dir, label="data path", file_target=False, anchor=literal_anchor)
    checks = runtime_checks(repo, language=language, node=node, cbm_binary=cbm, serena_python=serena, node_bin_dir=node_bin, tsconfig=tsconfig)
    ready = not path_error and required_checks_ok(checks)
    config = None
    if ready:
        config = configured or AtlasConfig.discover(repo, language=language, node=node, cbm_binary=cbm, serena_python=serena, node_bin_dir=node_bin, tsconfig=tsconfig, data_dir=data_dir)
    apply_command = ""
    if ready:
        # Keep the approved plan replayable even when its runtime paths were
        # supplied explicitly rather than discovered from the environment.
        options = ["codebase-atlas", "onboard", "--apply", "--repo", str(repo), "--config", str(config_path)]
        for flag, value in (
            ("--language", language),
            ("--node", node),
            ("--cbm-binary", cbm),
            ("--serena-python", serena),
            ("--node-bin-dir", node_bin),
            ("--tsconfig", tsconfig),
            ("--data-dir", data_dir),
        ):
            if value:
                options += [flag, str(value)]
        if inputs.mode != "fast":
            options += ["--mode", inputs.mode]
        apply_command = " ".join(shlex.quote(value) for value in options)
    actions = _actions(repo, config_path, config, reusing=bool(configured)) if ready and config else [
        _action("check_runtime", mutates=False, target=repo, reason="verify local prerequisites", command="codebase-atlas setup")
    ]
    return ({
        "schema_version": 1,
        "status": "planned" if ready else "blocked",
        "mode": "read_only",
        "repository": str(repo), "path_anchor": str(repo), "language": language, "config": str(config_path),
        "data_dir": str(config.data_dir if config else resolved_data_dir),
        "checks": checks, "actions": actions, "error": path_error or "",
        "config_fingerprint": _content_fingerprint(config_path) if configured and not path_error else "",
        "config_identity": list(_file_identity(config_path)) if configured and not path_error else [],
        "apply_command": apply_command,
        "guidance": {"next_query": "codebase-atlas query definition <symbol> --config " + shlex.quote(str(config_path)), "mcp": "codebase-atlas mcp --config " + shlex.quote(str(config_path)), "repair": "codebase-atlas repair --config " + shlex.quote(str(config_path)), "remove": "codebase-atlas clean --config " + shlex.quote(str(config_path))},
    }, config)


def apply_plan(plan: dict[str, object], config: AtlasConfig | None, *, indexer: Callable[[AtlasConfig, str], dict[str, object]], mode: str) -> tuple[dict[str, object], int]:
    if plan["status"] == "blocked" or config is None:
        return plan, 2
    config_path = Path(str(plan["config"]))
    reusing = bool(plan.get("config_fingerprint"))
    expected_actions = _actions(config.repository, config_path, config, reusing=reusing)
    if plan.get("actions") != expected_actions:
        return plan | {"status": "blocked", "mode": "applied", "error": "action graph does not match the approved onboarding plan"}, 2
    created = False
    try:
        path_anchor = Path(str(plan.get("path_anchor", config.repository)))
        path_error = _safe_path(config_path, label="config path", file_target=config_path.exists(), anchor=path_anchor) or _safe_path(config.data_dir, label="data path", file_target=False, anchor=path_anchor)
        if path_error:
            return plan | {"status": "blocked", "mode": "applied", "error": path_error}, 2
        expected_fingerprint = str(plan.get("config_fingerprint", ""))
        raw_identity = plan.get("config_identity", [])
        expected_identity = tuple(raw_identity) if isinstance(raw_identity, list) and len(raw_identity) == 2 else ()
        if reusing:
            if len(expected_identity) != 2 or not _matches_config(config_path, expected_fingerprint, expected_identity):
                return plan | {"status": "failed", "mode": "applied", "error": "existing config changed since planning; rerun onboard"}, 2
        else:
            config.write_exclusive(config_path)
            created = True
            expected_fingerprint = _content_fingerprint(config_path)
            expected_identity = _file_identity(config_path)
        freshness = index_freshness(config.data_dir, config.repository, config.project)
        database = provider_database_health(config.cache_dir, config.project)
        current = freshness["status"] == "fresh" and freshness.get("mode") == mode and database["ok"]
        payload: dict[str, object] = {"route": "atlas_source_current", "status": "not_started"}
        indexed = config
        if not current:
            before = repository_snapshot(config.repository)
            payload = indexer(config, mode)
            after = repository_snapshot(config.repository)
            if before.kind == "git" and after.kind == "git" and before.fingerprint != after.fingerprint:
                return plan | {"status": "failed", "mode": "applied", "config_created": created, "error": "repository changed while indexing; rerun onboard"}, 2
            if _safe_path(config_path, label="config path", file_target=True, anchor=path_anchor) or not _matches_config(config_path, expected_fingerprint, expected_identity):
                return plan | {"status": "failed", "mode": "applied", "config_created": created, "error": "config changed while indexing; rerun onboard"}, 2
            indexed = config.with_project(str(payload["project"]))
            indexed.write_verified(config_path, expected_identity)
            if _file_identity(config_path) != expected_identity:
                return plan | {"status": "failed", "mode": "applied", "config_created": created, "error": "config changed while publishing; rerun onboard"}, 2
            # A custom config may live inside the repository. Record the final
            # source view after Atlas has published its own project metadata.
            # The snapshot routine excludes only a valid Atlas config for this
            # repository, so any late source change remains observable here.
            final_snapshot = repository_snapshot(indexed.repository)
            if after.kind == "git" and final_snapshot.kind == "git" and after.fingerprint != final_snapshot.fingerprint:
                return plan | {"status": "failed", "mode": "applied", "config_created": created, "error": "repository changed while publishing; rerun onboard"}, 2
            record_index_state(indexed.data_dir, indexed.repository, indexed.project, mode, snapshot=final_snapshot)
        checks = diagnose(indexed)
        status = "current" if current else "ready" if required_checks_ok(checks) else "incomplete"
        return plan | {"status": status, "mode": "applied", "config_created": created, "doctor": checks, "provider": payload}, 0 if status in {"ready", "current"} else 2
    except (OSError, RuntimeError, ValueError) as error:
        return plan | {"status": "failed", "mode": "applied", "config_created": created, "error": str(error), "resume": str(plan.get("apply_command", ""))}, 2
