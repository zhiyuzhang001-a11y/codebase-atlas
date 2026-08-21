#!/usr/bin/env python3
"""Clean install/upgrade/downgrade/uninstall acceptance for built wheels."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import venv


def executable(environment: Path, name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    directory = "Scripts" if os.name == "nt" else "bin"
    return environment / directory / f"{name}{suffix}"


def package_version(wheel: Path) -> str:
    stem = wheel.name.removesuffix("-py3-none-any.whl")
    return stem.rsplit("-", 1)[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous-wheel", type=Path, required=True)
    parser.add_argument("--current-wheel", type=Path, required=True)
    args = parser.parse_args()
    previous = args.previous_wheel.resolve()
    current = args.current_wheel.resolve()
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        environment = root / "venv"
        repository = root / "repo"
        repository.mkdir()
        (repository / "sample.py").write_text("value = 1\n", encoding="utf-8")
        isolated_home = root / "home"
        isolated_home.mkdir()
        command_env = os.environ.copy()
        command_env.update(
            HOME=str(isolated_home),
            USERPROFILE=str(isolated_home),
            XDG_DATA_HOME=str(root / "data"),
        )
        venv.EnvBuilder(with_pip=True).create(environment)
        python = executable(environment, "python")
        atlas = executable(environment, "codebase-atlas")

        def pip_install(wheel: Path) -> None:
            subprocess.run(
                [str(python), "-m", "pip", "install", "--no-deps", "--force-reinstall", str(wheel)],
                check=True, env=command_env, capture_output=True, text=True,
            )

        def assert_version(expected: str) -> None:
            completed = subprocess.run(
                [str(atlas), "--version"], check=True, env=command_env,
                capture_output=True, text=True,
            )
            assert json.loads(completed.stdout)["version"] == expected

        pip_install(previous)
        assert_version(package_version(previous))
        pip_install(current)
        assert_version(package_version(current))
        pip_install(previous)
        assert_version(package_version(previous))
        pip_install(current)
        assert_version(package_version(current))

        preflight = subprocess.run(
            [str(atlas), "setup", "--repo", str(repository)],
            check=False, env=command_env, capture_output=True, text=True,
        )
        assert preflight.returncode in (0, 2)
        assert json.loads(preflight.stdout)["mode"] == "read_only"
        assert not (repository / ".codebase-atlas.toml").exists()
        assert sorted(path.name for path in repository.iterdir()) == ["sample.py"]

        subprocess.run(
            [str(python), "-m", "pip", "uninstall", "-y", "codebase-atlas"],
            check=True, env=command_env, capture_output=True, text=True,
        )
        absent = subprocess.run(
            [str(python), "-c", "import importlib.util; raise SystemExit(importlib.util.find_spec('codebase_atlas') is not None)"],
            check=False, env=command_env,
        )
        assert absent.returncode == 0
        assert not (root / "data").exists()
    print("install/upgrade/downgrade/uninstall lifecycle: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
