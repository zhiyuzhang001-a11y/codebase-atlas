from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codebase_atlas.cli import _index_repository, main
from codebase_atlas.config import AtlasConfig
from codebase_atlas.go_dependencies import (
    DEFAULT_GO_PROXY, GoDependencyError, dependency_manifest_path,
    dependency_plan, dependency_status, module_roots, prepare_dependencies,
    validate_proxy,
)
from codebase_atlas.go_environment import TELEMETRY_MODE, telemetry_mode_paths
from codebase_atlas.maintenance import apply_cleanup, cleanup_plan, inspect_installation, repair_plan


class Completed:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RecordingRunner:
    def __init__(
        self, *, fail_download: bool = False, mutate: Path | None = None,
        write_working_sum: bool = False,
    ) -> None:
        self.calls: list[tuple[list[str], Path, dict[str, str]]] = []
        self.fail_download = fail_download
        self.mutate = mutate
        self.write_working_sum = write_working_sum

    def __call__(self, command: list[str], **kwargs: object) -> Completed:
        cwd = Path(str(kwargs["cwd"]))
        env = dict(kwargs["env"])
        self.calls.append((command, cwd, env))
        if command[-1] == "version":
            return Completed(0, "go version go1.27.0 darwin/arm64\n")
        if command[1:] == ["work", "edit", "-json"]:
            return Completed(0, json.dumps({"Use": [{"DiskPath": "./app"}, {"DiskPath": "./lib"}]}))
        if command[1:4] == ["mod", "download", "-json"]:
            if self.mutate is not None:
                self.mutate.write_text(self.mutate.read_text() + "// changed\n")
                self.mutate = None
            if self.fail_download:
                return Completed(1, stderr="dependency missing")
            if self.write_working_sum:
                (cwd / "go.sum").write_text("download-added-sum\n", encoding="utf-8")
            cache = Path(env["GOMODCACHE"])
            module = cwd.name
            base = cache / "cache/download/example.test" / module / "@v"
            base.mkdir(parents=True, exist_ok=True)
            paths = {
                "Info": base / "v1.0.0.info",
                "GoMod": base / "v1.0.0.mod",
                "Zip": base / "v1.0.0.zip",
            }
            for name, path in paths.items():
                path.write_text(f"{module}-{name}\n", encoding="utf-8")
            return Completed(0, json.dumps({
                "Path": f"example.test/{module}", "Version": "v1.0.0",
                **{name: str(path) for name, path in paths.items()},
            }))
        return Completed(1, stderr="unexpected command")


class GoDependencyTests(unittest.TestCase):
    def config(self, root: Path, *, language: str = "go") -> AtlasConfig:
        repo = root / "repo"
        repo.mkdir()
        (repo / "go.mod").write_text("module example.test/app\n", encoding="utf-8")
        go = root / "go"
        gopls = root / "gopls"
        go.touch()
        gopls.touch()
        return AtlasConfig(
            repo, language, None, None, None, root / "data", "gopls",
            go=go, gopls=gopls, go_workspace=repo,
        )

    def test_dry_run_is_network_and_write_free(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.config(Path(raw))
            runner = RecordingRunner()
            result = dependency_plan(config, runner=runner)
            self.assertEqual(result["status"], "planned")
            self.assertEqual(result["network"], "apply_only")
            self.assertFalse(config.data_dir.exists())
            self.assertEqual(runner.calls, [])

    def test_proxy_credentials_are_rejected(self) -> None:
        with self.assertRaisesRegex(GoDependencyError, "go_proxy_credentials_rejected"):
            validate_proxy("https://user:secret@example.test,direct")
        self.assertEqual(validate_proxy(DEFAULT_GO_PROXY), DEFAULT_GO_PROXY)

    def test_apply_uses_only_contained_cache_and_publishes_verified_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.config(Path(raw))
            runner = RecordingRunner()
            result = prepare_dependencies(config, runner=runner)
            self.assertEqual(result["status"], "prepared")
            self.assertTrue(result["offline_verified"])
            self.assertTrue(dependency_manifest_path(config).is_file())
            root = config.data_dir / "go-provider"
            for _command, _cwd, env in runner.calls:
                for name in (
                    "HOME", "TMPDIR", "GOMODCACHE", "GOCACHE", "GOPATH",
                    "XDG_CONFIG_HOME", "APPDATA",
                ):
                    self.assertTrue(Path(env[name]).resolve().is_relative_to(root.resolve()))
                self.assertEqual(env["GOTOOLCHAIN"], "local")
                self.assertEqual(env["GOTELEMETRY"], "off")
                self.assertEqual(env["GOFLAGS"], "-mod=readonly")
            for path in telemetry_mode_paths(root):
                self.assertEqual(path.read_bytes(), TELEMETRY_MODE)
                self.assertFalse(path.is_symlink())
            self.assertTrue(dependency_status(config, runner=runner)["ok"])

    def test_apply_refuses_symlinked_go_environment_directory(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.config(root)
            provider = config.data_dir / "go-provider"
            provider.mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            (provider / "home").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(GoDependencyError, "go_environment_unsafe"):
                prepare_dependencies(config, runner=RecordingRunner())
            self.assertEqual(list(outside.iterdir()), [])

    def test_apply_repairs_regular_mode_bytes_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.config(Path(raw))
            runner = RecordingRunner()
            prepare_dependencies(config, runner=runner)
            modes = telemetry_mode_paths(config.data_dir / "go-provider")
            modes[0].write_bytes(b"local\n")
            prepare_dependencies(config, runner=runner)
            self.assertTrue(all(path.read_bytes() == TELEMETRY_MODE for path in modes))

    def test_apply_refuses_symlinked_telemetry_mode(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.config(root)
            runner = RecordingRunner()
            prepare_dependencies(config, runner=runner)
            mode = telemetry_mode_paths(config.data_dir / "go-provider")[0]
            outside = root / "outside-mode"
            outside.write_bytes(b"local\n")
            mode.unlink()
            mode.symlink_to(outside)
            with self.assertRaisesRegex(GoDependencyError, "go_environment_unsafe"):
                prepare_dependencies(config, runner=runner)
            self.assertEqual(outside.read_bytes(), b"local\n")

    def test_download_failure_is_explicit_and_does_not_publish_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.config(Path(raw))
            with self.assertRaisesRegex(GoDependencyError, "go_dependency_download_failed"):
                prepare_dependencies(config, runner=RecordingRunner(fail_download=True))
            self.assertFalse(dependency_manifest_path(config).exists())

    def test_source_change_aborts_publication(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.config(Path(raw))
            go_mod = config.repository / "go.mod"
            with self.assertRaisesRegex(GoDependencyError, "source_changed"):
                prepare_dependencies(config, runner=RecordingRunner(mutate=go_mod))
            self.assertFalse(dependency_manifest_path(config).exists())

    def test_download_writes_only_staged_metadata_not_source_go_sum(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.config(Path(raw))
            source_sum = config.repository / "go.sum"
            self.assertFalse(source_sum.exists())
            prepare_dependencies(config, runner=RecordingRunner(write_working_sum=True))
            self.assertFalse(source_sum.exists())

    def test_manifest_becomes_stale_when_module_input_changes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.config(Path(raw))
            runner = RecordingRunner()
            prepare_dependencies(config, runner=runner)
            (config.repository / "go.mod").write_text("module example.test/changed\n")
            state = dependency_status(config, runner=runner)
            self.assertFalse(state["ok"])
            self.assertEqual(state["reason"], "go_dependency_manifest_stale")

    def test_ordinary_source_change_does_not_require_dependency_download(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.config(Path(raw))
            runner = RecordingRunner()
            prepare_dependencies(config, runner=runner)
            (config.repository / "main.go").write_text("package app\n")
            self.assertTrue(dependency_status(config, runner=runner)["ok"])

    def test_incomplete_cache_is_detected_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.config(Path(raw))
            runner = RecordingRunner()
            prepare_dependencies(config, runner=runner)
            manifest = json.loads(dependency_manifest_path(config).read_text())
            Path(manifest["artifacts"][0]["path"]).unlink()
            call_count = len(runner.calls)
            state = dependency_status(config, runner=runner)
            self.assertFalse(state["ok"])
            self.assertEqual(state["reason"], "go_dependency_cache_incomplete")
            self.assertEqual(len(runner.calls), call_count)

    def test_go_work_modules_must_be_contained(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.config(root)
            (config.repository / "go.mod").unlink()
            (config.repository / "go.work").write_text("go 1.27\nuse (\n ./app\n ./lib\n)\n")
            for name in ("app", "lib"):
                module = config.repository / name
                module.mkdir()
                (module / "go.mod").write_text(f"module example.test/{name}\n")
            runner = RecordingRunner()
            env = {"GOMODCACHE": str(root / "cache")}
            self.assertEqual(
                [path.name for path in module_roots(config, env=env, runner=runner)],
                ["app", "lib"],
            )

    def test_non_go_project_is_explicitly_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.config(Path(raw), language="python")
            with self.assertRaisesRegex(GoDependencyError, "go_dependencies_unsupported"):
                dependency_plan(config)

    def test_index_refuses_unprepared_dependencies_before_provider_start(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.config(Path(raw))
            with patch("codebase_atlas.cli.direct_provider_for") as provider:
                with self.assertRaisesRegex(RuntimeError, "go_dependencies_not_prepared"):
                    _index_repository(config, "fast")
            provider.assert_not_called()

    def test_cli_prepare_requires_apply_for_network_work(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.config(Path(raw))
            path = Path(raw) / "atlas.toml"
            config.write(path)
            output = StringIO()
            with patch("codebase_atlas.cli.dependency_plan", return_value={"status": "planned"}) as plan, patch(
                "codebase_atlas.cli.prepare_dependencies"
            ) as apply, redirect_stdout(output):
                self.assertEqual(main(["prepare-dependencies", "--config", str(path)]), 0)
            plan.assert_called_once()
            apply.assert_not_called()

    def test_cli_apply_reports_stable_failure_code(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.config(Path(raw))
            path = Path(raw) / "atlas.toml"
            config.write(path)
            output = StringIO()
            with patch(
                "codebase_atlas.cli.prepare_dependencies",
                side_effect=GoDependencyError("go_dependency_download_failed", "missing"),
            ), redirect_stdout(output):
                self.assertEqual(main(["prepare-dependencies", "--config", str(path), "--apply"]), 2)
            self.assertEqual(json.loads(output.getvalue())["code"], "go_dependency_download_failed")

    def test_inspect_repair_and_cleanup_classify_dependency_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.config(Path(raw))
            report = inspect_installation(config)
            self.assertFalse(report["go_dependencies"]["ok"])
            self.assertEqual(repair_plan(report)["action"], "prepare_go_dependencies")
            temporary = config.data_dir / "go-provider/.go-dependencies.json.deadbeef.tmp"
            temporary.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text("partial")
            plan = cleanup_plan(config)
            self.assertIn(str(temporary), {item["path"] for item in plan["targets"]})
            result = apply_cleanup(config, plan)
            self.assertEqual(result["removed_count"], 1)
            self.assertFalse(temporary.exists())


if __name__ == "__main__":
    unittest.main()
