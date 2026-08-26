"""Explicit, contained preparation of Go module dependencies."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Callable
from urllib.parse import urlsplit
import uuid

from .config import AtlasConfig
from .providers.go import source_fingerprint


DEFAULT_GO_PROXY = "https://proxy.golang.org,direct"
MANIFEST_SCHEMA = 1
Runner = Callable[..., Any]


class GoDependencyError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code
        self.detail = detail or code


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contained(root: Path, candidate: Path) -> Path:
    root = root.resolve()
    candidate = candidate.resolve()
    if candidate != root and root not in candidate.parents:
        raise GoDependencyError("go_workspace_unsupported", str(candidate))
    return candidate


def validate_proxy(value: str) -> str:
    proxy = value.strip()
    if not proxy:
        raise GoDependencyError("go_proxy_invalid", "proxy cannot be empty")
    for entry in proxy.split(","):
        entry = entry.strip()
        if entry in {"direct", "off"}:
            continue
        parsed = urlsplit(entry)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise GoDependencyError("go_proxy_invalid", entry)
        if parsed.username is not None or parsed.password is not None:
            raise GoDependencyError("go_proxy_credentials_rejected", entry)
    return proxy


def dependency_root(config: AtlasConfig) -> Path:
    return config.data_dir / "go-provider"


def dependency_manifest_path(config: AtlasConfig) -> Path:
    return dependency_root(config) / "go-dependencies.json"


def _environment(config: AtlasConfig, proxy: str, *, create: bool) -> dict[str, str]:
    proxy = validate_proxy(proxy)
    root = dependency_root(config)
    paths = {
        "HOME": root / "home",
        "TMPDIR": root / "tmp",
        "GOMODCACHE": root / "gomodcache",
        "GOCACHE": root / "gocache",
        "GOPATH": root / "gopath",
    }
    if create:
        for path in paths.values():
            path.mkdir(parents=True, exist_ok=True)
    go = config.go
    if go is None:
        raise GoDependencyError("go_toolchain_unavailable", "Go is not configured")
    return {
        "PATH": f"{go.parent}:/usr/bin:/bin:/usr/sbin:/sbin",
        **{name: str(path) for name, path in paths.items()},
        "GOTOOLCHAIN": "local",
        "GOTELEMETRY": "off",
        "GOENV": "off",
        "GOFLAGS": "-mod=readonly",
        "GOPROXY": proxy,
        "GOSUMDB": "sum.golang.org" if proxy != "off" else "off",
    }


def _run(
    command: list[str], *, cwd: Path, env: dict[str, str], runner: Runner,
    timeout: float = 600.0,
) -> Any:
    try:
        return runner(
            command, cwd=cwd, env=env, check=False,
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GoDependencyError("go_dependency_command_failed", str(exc)) from exc


def _json_stream(text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    offset = 0
    values: list[dict[str, Any]] = []
    while offset < len(text):
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset >= len(text):
            break
        value, offset = decoder.raw_decode(text, offset)
        if isinstance(value, dict):
            values.append(value)
    return values


def module_roots(
    config: AtlasConfig, *, env: dict[str, str], runner: Runner = subprocess.run,
) -> list[Path]:
    if config.language != "go" or config.go is None or config.go_workspace is None:
        raise GoDependencyError("go_dependencies_unsupported", config.language)
    repository = config.repository.resolve()
    workspace = _contained(repository, config.go_workspace)
    if (workspace / "go.mod").is_file():
        return [workspace]
    if not (workspace / "go.work").is_file():
        raise GoDependencyError("go_build_context_incomplete", str(workspace))
    completed = _run(
        [str(config.go), "work", "edit", "-json"], cwd=workspace,
        env=env | {"GOWORK": str(workspace / "go.work")}, runner=runner,
        timeout=30.0,
    )
    if completed.returncode != 0:
        raise GoDependencyError(
            "go_workspace_unavailable", (completed.stderr or completed.stdout).strip(),
        )
    try:
        value = json.loads(completed.stdout)
        uses = value.get("Use", []) or []
        roots = [
            _contained(repository, (workspace / str(item["DiskPath"])).resolve())
            for item in uses
        ]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise GoDependencyError("go_workspace_unavailable", "invalid go work edit output") from exc
    if not roots or any(not (root / "go.mod").is_file() for root in roots):
        raise GoDependencyError("go_workspace_unavailable", "go.work has no valid contained modules")
    return sorted(set(roots), key=str)


def dependency_input_fingerprint(config: AtlasConfig, modules: list[Path]) -> str:
    paths = set()
    for root in modules:
        paths.update(path for name in ("go.mod", "go.sum") if (path := root / name).is_file())
    if config.go_workspace and (config.go_workspace / "go.work").is_file():
        paths.add(config.go_workspace / "go.work")
        if (config.go_workspace / "go.work.sum").is_file():
            paths.add(config.go_workspace / "go.work.sum")
    payload = [
        (str(path.resolve().relative_to(config.repository.resolve())), _sha256(path))
        for path in sorted(paths, key=str)
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _stage_metadata(
    config: AtlasConfig, modules: list[Path],
) -> tuple[tempfile.TemporaryDirectory[str], list[tuple[Path, Path]], Path | None]:
    staging_parent = dependency_root(config) / "tmp"
    staging_parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix="dependency-metadata-", dir=staging_parent)
    staged_repository = Path(temporary.name) / "repository"
    pairs: list[tuple[Path, Path]] = []
    for module in modules:
        relative = module.resolve().relative_to(config.repository.resolve())
        staged = staged_repository / relative
        staged.mkdir(parents=True, exist_ok=True)
        shutil.copy2(module / "go.mod", staged / "go.mod")
        if (module / "go.sum").is_file():
            shutil.copy2(module / "go.sum", staged / "go.sum")
        pairs.append((module, staged))
    staged_work = None
    workspace = config.go_workspace
    if workspace is not None and (workspace / "go.work").is_file():
        relative = workspace.resolve().relative_to(config.repository.resolve())
        staged_workspace = staged_repository / relative
        staged_workspace.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workspace / "go.work", staged_workspace / "go.work")
        if (workspace / "go.work.sum").is_file():
            shutil.copy2(workspace / "go.work.sum", staged_workspace / "go.work.sum")
        staged_work = staged_workspace / "go.work"
    return temporary, pairs, staged_work


def dependency_plan(
    config: AtlasConfig, *, proxy: str = DEFAULT_GO_PROXY,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    proxy = validate_proxy(proxy)
    env = _environment(config, proxy, create=False)
    modules = module_roots(config, env=env, runner=runner)
    return {
        "schema_version": MANIFEST_SCHEMA, "status": "planned", "mode": "dry_run",
        "language": config.language, "repository": str(config.repository),
        "workspace": str(config.go_workspace), "modules": [str(path) for path in modules],
        "destination": str(dependency_root(config) / "gomodcache"),
        "manifest": str(dependency_manifest_path(config)), "go_proxy": proxy,
        "network": "apply_only",
        "source_fingerprint": source_fingerprint(config.repository),
        "input_fingerprint": dependency_input_fingerprint(config, modules),
        "commands": [
            {"cwd": str(path), "argv": [str(config.go), "mod", "download", "-json", "all"]}
            for path in modules
        ],
    }


def _artifact(root: Path, raw: str) -> dict[str, str] | None:
    if not raw:
        return None
    path = Path(raw).resolve()
    if path != root and root not in path.parents:
        raise GoDependencyError("go_dependency_cache_escape", str(path))
    if not path.is_file() or path.is_symlink():
        raise GoDependencyError("go_dependency_cache_incomplete", str(path))
    return {"path": str(path), "sha256": _sha256(path)}


def prepare_dependencies(
    config: AtlasConfig, *, proxy: str = DEFAULT_GO_PROXY,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    plan = dependency_plan(config, proxy=proxy, runner=runner)
    before = str(plan["source_fingerprint"])
    env = _environment(config, str(plan["go_proxy"]), create=True)
    modules = [Path(value) for value in plan["modules"]]
    go = config.go
    assert go is not None
    version = _run([str(go), "version"], cwd=config.repository, env=env, runner=runner, timeout=5.0)
    if version.returncode != 0 or "go version go1.27.0 " not in version.stdout:
        raise GoDependencyError("go_toolchain_unavailable", (version.stderr or version.stdout).strip())
    downloaded: list[dict[str, Any]] = []
    verified: list[dict[str, Any]] = []
    temporary, staged_modules, staged_work = _stage_metadata(config, modules)
    try:
        work_value = str(staged_work) if staged_work is not None else "off"
        for _source_module, staged_module in staged_modules:
            completed = _run(
                [str(go), "mod", "download", "-json", "all"], cwd=staged_module,
                env=env | {"GOWORK": work_value}, runner=runner,
            )
            if completed.returncode != 0:
                raise GoDependencyError(
                    "go_dependency_download_failed", (completed.stderr or completed.stdout).strip(),
                )
            values = _json_stream(completed.stdout)
            errors = [item.get("Error") for item in values if item.get("Error")]
            if errors:
                raise GoDependencyError("go_dependency_download_failed", "; ".join(map(str, errors)))
            downloaded.extend(values)
        after_download = source_fingerprint(config.repository)
        if after_download != before:
            raise GoDependencyError("source_changed", "repository changed during dependency download")

        offline_env = env | {"GOPROXY": "off", "GOSUMDB": "off", "GOWORK": work_value}
        for _source_module, staged_module in staged_modules:
            completed = _run(
                [str(go), "mod", "download", "-json", "all"], cwd=staged_module,
                env=offline_env, runner=runner,
            )
            if completed.returncode != 0:
                raise GoDependencyError(
                    "go_dependencies_unavailable", (completed.stderr or completed.stdout).strip(),
                )
            values = _json_stream(completed.stdout)
            errors = [item.get("Error") for item in values if item.get("Error")]
            if errors:
                raise GoDependencyError("go_dependencies_unavailable", "; ".join(map(str, errors)))
            verified.extend(values)
    finally:
        temporary.cleanup()
    cache = Path(env["GOMODCACHE"]).resolve()
    artifacts: list[dict[str, str]] = []
    seen = set()
    for item in verified or downloaded:
        for key in ("Info", "GoMod", "Zip"):
            record = _artifact(cache, str(item.get(key, "")))
            if record and record["path"] not in seen:
                seen.add(record["path"])
                artifacts.append(record)
    final_source = source_fingerprint(config.repository)
    if final_source != before:
        raise GoDependencyError("source_changed", "repository changed during offline verification")
    manifest = {
        "schema_version": MANIFEST_SCHEMA, "status": "ready",
        "repository": str(config.repository), "workspace": str(config.go_workspace),
        "modules": [str(path) for path in modules], "go_version": version.stdout.strip(),
        "go_proxy": plan["go_proxy"], "source_fingerprint": before,
        "input_fingerprint": dependency_input_fingerprint(config, modules),
        "artifacts": sorted(artifacts, key=lambda item: item["path"]),
    }
    destination = dependency_manifest_path(config)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, destination)
    return plan | {
        "status": "prepared", "mode": "applied", "offline_verified": True,
        "downloaded_modules": len(downloaded), "artifact_count": len(artifacts),
        "manifest_sha256": _sha256(destination),
    }


def dependency_status(config: AtlasConfig, *, runner: Runner = subprocess.run) -> dict[str, Any]:
    if config.language != "go":
        return {"status": "not_applicable", "ok": True, "required": False}
    destination = dependency_manifest_path(config)
    remediation = "run 'codebase-atlas prepare-dependencies --config <config-path> --apply'"
    try:
        if destination.is_symlink() or not destination.is_file():
            raise GoDependencyError("go_dependencies_not_prepared", str(destination))
        manifest = json.loads(destination.read_text(encoding="utf-8"))
        env = _environment(config, str(manifest.get("go_proxy", DEFAULT_GO_PROXY)), create=False)
        modules = module_roots(config, env=env, runner=runner)
        if manifest.get("schema_version") != MANIFEST_SCHEMA:
            raise GoDependencyError("go_dependency_manifest_invalid", "schema")
        if manifest.get("repository") != str(config.repository) or manifest.get("workspace") != str(config.go_workspace):
            raise GoDependencyError("go_dependency_manifest_stale", "identity")
        if manifest.get("modules") != [str(path) for path in modules]:
            raise GoDependencyError("go_dependency_manifest_stale", "module roots")
        if manifest.get("input_fingerprint") != dependency_input_fingerprint(config, modules):
            raise GoDependencyError("go_dependency_manifest_stale", "module inputs")
        cache = dependency_root(config) / "gomodcache"
        for item in manifest.get("artifacts", []):
            path = _contained(cache, Path(str(item["path"])))
            if path.is_symlink() or not path.is_file() or _sha256(path) != item["sha256"]:
                raise GoDependencyError("go_dependency_cache_incomplete", str(path))
        return {
            "status": "ready", "ok": True, "required": True,
            "path": str(destination), "reason": "go_dependencies_verified",
            "artifact_count": len(manifest.get("artifacts", [])), "remediation": "",
        }
    except (GoDependencyError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        code = exc.code if isinstance(exc, GoDependencyError) else "go_dependency_manifest_invalid"
        return {
            "status": "unavailable", "ok": False, "required": True,
            "path": str(destination), "reason": code, "detail": str(exc),
            "remediation": remediation,
        }


def dependency_check(config: AtlasConfig, *, runner: Runner = subprocess.run) -> dict[str, object]:
    state = dependency_status(config, runner=runner)
    return {
        "name": "go_dependencies", "ok": state["ok"],
        "required": state["required"], "path": state.get("path", ""),
        "version": "", "detail": state.get("reason", state["status"]),
        "remediation": state.get("remediation", ""),
    }
