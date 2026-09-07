#!/usr/bin/env python3
"""Exercise an installed candidate wheel against one disposable real project."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import venv


def executable(environment: Path, name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    directory = "Scripts" if os.name == "nt" else "bin"
    return environment / directory / f"{name}{suffix}"


def require_executable(path: Path, label: str) -> Path:
    # Keep virtual-environment launchers intact; resolving their Python symlink
    # can switch execution to the base interpreter without Serena installed.
    selected = Path(os.path.abspath(path.expanduser()))
    if not selected.is_file() or not os.access(selected, os.X_OK):
        raise RuntimeError(f"{label} must be an executable file: {selected}")
    return selected


def run_json(
    command: list[str], environment: dict[str, str], *, expected: int = 0,
    timeout: float = 300,
) -> dict:
    completed = subprocess.run(
        command, env=environment, check=False, capture_output=True, text=True,
        timeout=timeout,
    )
    if completed.returncode != expected:
        raise RuntimeError(
            f"command returned {completed.returncode}, expected {expected}: {command}\n"
            f"stdout={completed.stdout[-4000:]}\nstderr={completed.stderr[-4000:]}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"command returned non-JSON: {completed.stdout[-4000:]}") from exc


def call_operation(
    python: Path, operation: str, repository: Path,
    environment: dict[str, str], arguments: list[str] | None = None,
) -> dict:
    program = (
        "import json,sys; from pathlib import Path; "
        f"from codebase_atlas.simple_cli import {operation}_project; "
        f"value,code={operation}_project(Path(sys.argv[1])"
        + (", " + ", ".join(arguments or []) if arguments else "")
        + "); print(json.dumps(value)); raise SystemExit(code)"
    )
    return run_json([str(python), "-c", program, str(repository)], environment)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--provider", type=Path, required=True)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--serena-python", type=Path, required=True)
    args = parser.parse_args()
    wheel = args.wheel.expanduser().resolve(strict=True)
    if wheel.is_dir():
        candidates = sorted(wheel.glob("codebase_atlas-*.whl"))
        if len(candidates) != 1:
            raise RuntimeError(
                f"candidate wheel directory must contain exactly one wheel: {wheel}"
            )
        wheel = candidates[0]
    provider = require_executable(args.provider, "Provider")
    node = require_executable(args.node, "Node.js")
    serena = require_executable(args.serena_python, "Serena Python")

    temporary_parent = "/private/tmp" if platform.system() == "Darwin" else None
    with tempfile.TemporaryDirectory(
        prefix="atlas-candidate-", dir=temporary_parent
    ) as raw:
        root = Path(raw).resolve()
        repository = root / "repo"
        environment_dir = root / "venv"
        home = root / "home"
        data_dir = root / "project-data"
        runtime = root / "runtime"
        repository.mkdir()
        home.mkdir()
        runtime.mkdir(mode=0o700)
        if platform.system() == "Darwin":
            subprocess.run(["chmod", "-N", str(runtime)], check=False, capture_output=True)
        source = repository / "sample.py"
        source.write_text(
            "def atlas_candidate_leaf():\n    return 41\n\n"
            "def atlas_candidate_target():\n    return atlas_candidate_leaf() + 1\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(repository), "add", "sample.py"], check=True)
        subprocess.run(
            ["git", "-C", str(repository), "-c", "user.name=Atlas Acceptance",
             "-c", "user.email=atlas@example.invalid", "commit", "-qm", "fixture"],
            check=True,
        )
        venv.EnvBuilder(with_pip=True).create(environment_dir)
        python = executable(environment_dir, "python")
        atlas = executable(environment_dir, "atlas")
        codebase_atlas = executable(environment_dir, "codebase-atlas")
        command_env = os.environ.copy()
        command_env.update({
            "HOME": str(home),
            "USERPROFILE": str(home),
            "XDG_DATA_HOME": str(root / "xdg"),
            "ATLAS_RUNTIME_DIR": str(runtime),
            "CBM_RUNTIME_DIR": str(runtime),
            "PYTHONNOUSERSITE": "1",
        })
        subprocess.run(
            [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
            check=True, env=command_env, capture_output=True, text=True,
        )
        enable_arguments = [
            "language='python'",
            f"node=Path({str(node)!r})",
            f"cbm_binary=Path({str(provider)!r})",
            f"serena_python=Path({str(serena)!r})",
            f"node_bin_dir=Path({str(node.parent)!r})",
            f"data_dir=Path({str(data_dir)!r})",
        ]
        enabled = call_operation(
            python, "enable", repository, command_env, enable_arguments
        )
        assert enabled["status"] == "ready"
        status = run_json(
            [str(atlas), "status", "--repo", str(repository), "--json"], command_env
        )
        assert Path(status["repository"]).resolve() == repository
        assert status["project_state"] == "ready"
        assert status["index_status"] == "fresh"
        config_path = repository / ".codebase-atlas.toml"
        doctor = run_json(
            [str(codebase_atlas), "doctor", "--config", str(config_path)], command_env
        )
        assert doctor["status"] == "ready", doctor
        verified = run_json(
            [str(atlas), "verify", "--repo", str(repository), "--json"], command_env
        )
        assert verified["status"] == "PASS", verified
        stopped = run_json(
            [str(atlas), "stop", "--repo", str(repository), "--json"], command_env
        )
        assert stopped["project_state"] == "stopped"
        blocked = run_json(
            [str(atlas), "verify", "--repo", str(repository), "--json"],
            command_env, expected=4,
        )
        assert blocked["status"] == "BLOCKED", blocked
        resumed = call_operation(
            python, "enable", repository, command_env, enable_arguments
        )
        assert resumed["status"] == "ready"
        update_program = (
            "import json,sys; from pathlib import Path; from types import SimpleNamespace; "
            "from codebase_atlas import __version__; "
            "from codebase_atlas.simple_cli import update_project; "
            "fail=lambda _release: (_ for _ in ()).throw(RuntimeError('installer called')); "
            "value,code=update_project(Path(sys.argv[1]), "
            "release_fetcher=lambda: SimpleNamespace(version=__version__), installer=fail); "
            "print(json.dumps(value)); raise SystemExit(code)"
        )
        current = run_json(
            [str(python), "-c", update_program, str(repository)], command_env
        )
        assert current["status"] == "current" and not current["mutates"]
        removed = run_json(
            [str(atlas), "remove", "--repo", str(repository), "--json"], command_env
        )
        removed_again = run_json(
            [str(atlas), "remove", "--repo", str(repository), "--json"], command_env
        )
        assert removed["status"] == removed_again["status"] == "removed"
        assert removed["mutates"] and not removed_again["mutates"]
        assert source.read_text(encoding="utf-8").startswith("def atlas_candidate_leaf")
        for relative in (
            ".codebase-atlas.toml", ".codex/config.toml", "AGENTS.md",
            ".agents/skills/codebase-atlas/SKILL.md",
        ):
            assert not (repository / relative).exists(), relative
        assert not (repository / ".agents").exists()
        assert not (repository / ".codex").exists()
        summary = {
            "status": "passed",
            "repository_identity": str(repository),
            "doctor": doctor["status"],
            "index": status["index_status"],
            "verify": verified["status"],
            "stop_degradation": blocked["status"],
            "update": current["status"],
            "remove": "idempotent",
            "routing_cleanup": removed.get("routing_cleanup"),
        }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
