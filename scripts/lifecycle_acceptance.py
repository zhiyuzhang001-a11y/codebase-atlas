#!/usr/bin/env python3
"""Clean install/upgrade/downgrade/uninstall acceptance for built wheels."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
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
        simple_atlas = executable(environment, "atlas")

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

        simple_version = subprocess.run(
            [str(simple_atlas), "--version"], check=True, env=command_env,
            capture_output=True, text=True,
        )
        assert simple_version.stdout.strip() == package_version(current)
        simple_help = subprocess.run(
            [str(simple_atlas), "--help"], check=True, env=command_env,
            capture_output=True, text=True,
        ).stdout
        assert all(command in simple_help for command in ("enable", "stop", "update", "remove"))
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

        config = root / "atlas.toml"
        create_config = (
            "import sys; from pathlib import Path; "
            "from codebase_atlas.config import AtlasConfig; "
            "python=Path(sys.executable); "
            "AtlasConfig(Path(sys.argv[1]), 'python', python, python, python, "
            "Path(sys.argv[3]), 'lifecycle', python.parent).write(Path(sys.argv[2]))"
        )
        subprocess.run(
            [str(python), "-c", create_config, str(repository), str(config), str(root / "ui-data")],
            check=True, env=command_env, capture_output=True, text=True,
        )
        ui = subprocess.Popen(
            [str(atlas), "ui", "--config", str(config), "--no-open"],
            env=command_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        output: queue.Queue[str] = queue.Queue()
        reader = threading.Thread(target=lambda: output.put(ui.stdout.readline()), daemon=True)
        reader.start()
        try:
            ready_line = output.get(timeout=5)
        except queue.Empty:
            ui.terminate()
            _stdout, stderr = ui.communicate(timeout=5)
            raise AssertionError(f"installed UI did not become ready: {stderr}")
        assert ready_line, ui.stderr.read()
        assert ui.poll() is None, ui.stderr.read()
        ui.terminate()
        _stdout, stderr = ui.communicate(timeout=5)
        assert ui.returncode is not None
        ready = json.loads(ready_line)
        assert ready["status"] == "ready"
        assert ready["mode"] == "read_only"
        assert ready["binding"].startswith("127.0.0.1:")
        assert stderr == ""
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
