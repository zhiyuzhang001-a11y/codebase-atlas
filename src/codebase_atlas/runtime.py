"""Read-only runtime compatibility checks with actionable remediation."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Callable, Any

from .config import _asset
from .languages import capability, go_workspace_root


Runner = Callable[..., Any]
SUPPORTED_PYTHON = ">=3.11,<3.15"
MINIMUM_NODE_MAJOR = 18


def _candidate(explicit: Path | None, environment: str, command: str) -> Path | None:
    value = explicit or (Path(os.environ[environment]) if os.environ.get(environment) else None)
    if value is None:
        found = shutil.which(command)
        value = Path(found) if found else None
    return value.absolute() if value else None


def _run_version(
    command: list[str], *, runner: Runner, timeout: float = 5.0
) -> tuple[bool, str]:
    try:
        completed = runner(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = (completed.stdout or completed.stderr or "").strip()
    return completed.returncode == 0, output


def _check(
    name: str,
    ok: bool,
    *,
    path: Path | None = None,
    version: str = "",
    detail: str,
    remediation: str = "",
    required: bool = True,
) -> dict[str, object]:
    return {
        "name": name,
        "ok": ok,
        "required": required,
        "path": str(path or ""),
        "version": version,
        "detail": detail,
        "remediation": "" if ok else remediation,
    }


def runtime_checks(
    repository: Path,
    *,
    language: str,
    node: Path | None = None,
    cbm_binary: Path | None = None,
    serena_python: Path | None = None,
    node_bin_dir: Path | None = None,
    tsconfig: Path | None = None,
    go: Path | None = None,
    gopls: Path | None = None,
    go_workspace: Path | None = None,
    runner: Runner = subprocess.run,
) -> list[dict[str, object]]:
    """Inspect required runtimes without installing software or changing config."""
    repo = repository.resolve()
    selected = capability(language)
    node_path = _candidate(node, "ATLAS_NODE", "node")
    cbm_path = _candidate(cbm_binary, "ATLAS_CBM_BINARY", "codebase-memory-mcp")
    serena_path = serena_python or (
        Path(os.environ["ATLAS_SERENA_PYTHON"]).absolute()
        if os.environ.get("ATLAS_SERENA_PYTHON")
        else None
    )
    checks: list[dict[str, object]] = []

    host_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    python_ok = (3, 11) <= sys.version_info[:2] < (3, 15)
    checks.append(_check(
        "atlas_python", python_ok, path=Path(sys.executable), version=host_version,
        detail=f"supported range {SUPPORTED_PYTHON}",
        remediation="install and run Atlas with Python 3.11, 3.12, 3.13, or 3.14",
    ))
    checks.append(_check(
        "repository", repo.is_dir(), path=repo,
        detail="repository directory is accessible" if repo.is_dir() else "repository directory is missing",
        remediation=f"create or select an existing repository: codebase-atlas setup --repo {repo}",
    ))

    node_ok = False
    node_version = ""
    node_detail = "Node.js was not discovered"
    if node_path:
        ran, output = _run_version([str(node_path), "--version"], runner=runner)
        match = re.search(r"v?(\d+)(?:\.\d+){0,2}", output)
        node_version = match.group(0).lstrip("v") if match else output
        node_ok = bool(ran and match and int(match.group(1)) >= MINIMUM_NODE_MAJOR)
        node_detail = (
            f"Node.js {node_version}; minimum {MINIMUM_NODE_MAJOR}"
            if ran else f"Node.js could not execute: {output}"
        )
    checks.append(_check(
        "node", node_ok, path=node_path, version=node_version, detail=node_detail,
        remediation="install Node.js 18 or newer, or pass --node /absolute/path/to/node",
        required=selected.requires_node,
    ))

    cbm_ok = False
    cbm_version = ""
    cbm_detail = "Codebase Memory was not discovered"
    if cbm_path:
        cbm_ok, cbm_version = _run_version([str(cbm_path), "--version"], runner=runner)
        cbm_detail = (
            f"Codebase Memory executable responded: {cbm_version or 'version not reported'}"
            if cbm_ok else f"Codebase Memory could not execute: {cbm_version}"
        )
    checks.append(_check(
        "codebase_memory", cbm_ok, path=cbm_path, version=cbm_version,
        detail=cbm_detail,
        remediation=(
            "run 'python -m pip install codebase-memory-mcp', or pass "
            "--cbm-binary /absolute/path/to/codebase-memory-mcp"
        ),
        required=selected.requires_cbm,
    ))

    serena_ok = False
    serena_version = ""
    serena_detail = "Serena Python was not configured"
    if serena_path:
        probe = (
            "import importlib.metadata, serena; "
            "print(importlib.metadata.version('serena-agent'))"
        )
        serena_ok, serena_version = _run_version(
            [str(serena_path), "-c", probe], runner=runner
        )
        serena_detail = (
            f"Serena import succeeded: {serena_version}"
            if serena_ok else f"Serena import failed: {serena_version}"
        )
    checks.append(_check(
        "serena_python", serena_ok, path=serena_path, version=serena_version,
        detail=serena_detail,
        remediation=(
            "run 'uv tool install -p 3.13 serena-agent', then pass a Python interpreter "
            "that can import serena with --serena-python or ATLAS_SERENA_PYTHON"
        ),
        required=selected.requires_serena,
    ))

    analyzer = _asset("ts_test_analyzer.mjs")
    runner_asset = _asset("serena_runner.py")
    checks.append(_check(
        "packaged_assets", analyzer.is_file() and runner_asset.is_file(),
        detail=f"analyzer={analyzer}; serena_runner={runner_asset}",
        remediation="reinstall the Codebase Atlas wheel; packaged runtime assets are missing",
    ))

    if language == "typescript":
        selected_tsconfig = repo / (tsconfig or Path("tsconfig.json"))
        checks.append(_check(
            "tsconfig", selected_tsconfig.is_file(), path=selected_tsconfig,
            detail="TypeScript project boundary is available" if selected_tsconfig.is_file() else "tsconfig is missing",
            remediation="pass --tsconfig with a repository-relative TypeScript project path",
        ))
        bin_dir = (node_bin_dir or (node_path.parent if node_path else None))
        search_path = os.pathsep.join(
            part for part in (str(bin_dir) if bin_dir else "", os.environ.get("PATH", ""))
            if part
        )
        language_server_found = shutil.which("typescript-language-server", path=search_path)
        npm_found = shutil.which("npm", path=search_path)
        language_server = Path(language_server_found).absolute() if language_server_found else None
        npm = Path(npm_found).absolute() if npm_found else None
        checks.append(_check(
            "typescript_language_server", language_server is not None,
            path=language_server,
            detail=(
                "explicit language server available"
                if language_server
                else "not explicit; Serena may install its pinned managed server on first use"
            ),
            remediation="",
            required=False,
        ))
        semantic_runtime_ok = language_server is not None or npm is not None
        checks.append(_check(
            "typescript_semantic_runtime", semantic_runtime_ok,
            path=language_server or npm,
            detail=(
                "explicit TypeScript language server is available"
                if language_server else
                "npm is available for Serena's pinned managed TypeScript language server"
                if npm else
                "neither typescript-language-server nor npm is available"
            ),
            remediation=(
                "install npm beside the configured Node.js runtime, or provide a "
                "node bin directory containing typescript-language-server"
            ),
        ))
    if selected.requires_go:
        go_path = _candidate(go, "ATLAS_GO", "go")
        gopls_path = _candidate(gopls, "ATLAS_GOPLS", "gopls")
        go_ok = False
        go_version = ""
        if go_path:
            ran, output = _run_version([str(go_path), "version"], runner=runner)
            match = re.search(r"go version go(\d+\.\d+(?:\.\d+)?)", output)
            go_version = match.group(1) if match else output
            go_ok = bool(ran and match and match.group(1) == "1.27.0")
        checks.append(_check(
            "go_toolchain", go_ok, path=go_path, version=go_version,
            detail=("Go 1.27.0 contract runtime is available" if go_ok else "Go 1.27.0 is required exactly"),
            remediation="provide Go 1.27.0 with --go or ATLAS_GO; Atlas never installs it",
        ))
        gopls_ok = False
        gopls_version = ""
        if gopls_path:
            ran, output = _run_version([str(gopls_path), "version"], runner=runner)
            gopls_version = output
            gopls_ok = bool(ran and output.split() and output.split()[-1] == "v0.23.0")
        checks.append(_check(
            "gopls", gopls_ok, path=gopls_path, version=gopls_version,
            detail=("gopls v0.23.0 contract Provider is available" if gopls_ok else "gopls v0.23.0 is required exactly"),
            remediation="provide gopls v0.23.0 with --gopls or ATLAS_GOPLS; Atlas never installs it",
        ))
        try:
            workspace = go_workspace_root(repo, go_workspace)
            workspace_ok, workspace_detail = True, "deterministic Go workspace selected"
        except ValueError as exc:
            workspace, workspace_ok, workspace_detail = None, False, str(exc)
        checks.append(_check(
            "go_workspace", workspace_ok, path=workspace,
            detail=workspace_detail,
            remediation="pass --go-workspace for repositories with multiple modules or workspaces",
        ))
    return checks


def required_checks_ok(checks: list[dict[str, object]]) -> bool:
    return all(bool(item["ok"]) for item in checks if bool(item["required"]))
