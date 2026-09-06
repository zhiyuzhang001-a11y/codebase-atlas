from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from codebase_atlas.config import AtlasConfig
from codebase_atlas.project_discovery import ProjectResolution
from codebase_atlas.project_lifecycle import (
    ProjectLifecycleState,
    load_lifecycle_state,
    load_removal_marker,
    publish_lifecycle_state,
)
from codebase_atlas.simple_cli import (
    _enable_runtime_installation,
    _verification_candidate,
    _verification_query,
    enable_project,
    main,
    remove_project,
    stop_project,
)
from codebase_atlas.simple_cli import update_project
from codebase_atlas.release_installation import VersionedInstallation


def git_repository(root: Path) -> Path:
    repository = root / "repo"
    repository.mkdir()
    subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
    return repository


def configured_project(root: Path) -> tuple[Path, AtlasConfig, Path]:
    repository = git_repository(root)
    for name in ("node", "cbm", "serena"):
        (root / name).touch()
    config = AtlasConfig(
        repository, "python", root / "node", root / "cbm", root / "serena",
        root / "data", "project-a",
    )
    path = repository / ".codebase-atlas.toml"
    config.write(path)
    return repository, config, path


class SimpleCliTests(unittest.TestCase):
    def test_enable_runtime_prefers_latest_verified_stable_release(self) -> None:
        installation = VersionedInstallation(
            "0.25.1", "test", Path("/installation"), Path("/python"),
            Path("/atlas"), Path("/provider"), "provider-test", "a" * 64, "b" * 64,
        )
        release = SimpleNamespace(version="0.25.1")
        with (
            patch("codebase_atlas.simple_cli.fetch_stable_release", return_value=release),
            patch(
                "codebase_atlas.simple_cli.install_stable_release",
                return_value=(installation, True),
            ) as installer,
            patch("codebase_atlas.simple_cli.load_versioned_installation") as fallback,
        ):
            self.assertEqual(_enable_runtime_installation(), installation)
        installer.assert_called_once_with(release)
        fallback.assert_not_called()

    def test_verification_skips_hidden_sources_and_accepts_location_schema(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository, config, path = configured_project(Path(raw))
            hidden = repository / ".support" / "first.py"
            hidden.parent.mkdir()
            hidden.write_text("def hidden_target():\n    pass\n", encoding="utf-8")
            visible = repository / "visible.py"
            visible.write_text("def visible_target():\n    pass\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(repository), "add", ".support/first.py", "visible.py"],
                check=True,
            )
            self.assertEqual(
                _verification_candidate(config)[:2],
                ("visible_target", "visible.py"),
            )
            responses = iter((
                {"nodes": [{"location": {"path": "visible.py"}}]},
                {"nodes": []},
            ))
            with patch(
                "codebase_atlas.simple_cli._query_payload",
                side_effect=lambda *_args, **_kwargs: next(responses),
            ):
                result = _verification_query(config, path)
            self.assertEqual(result["target_path"], "visible.py")

    def test_verification_prefers_product_source_over_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository, config, _path = configured_project(Path(raw))
            fixture = repository / "fixtures" / "first.py"
            fixture.parent.mkdir()
            fixture.write_text("class FixtureTarget:\n    pass\n", encoding="utf-8")
            product = repository / "src" / "package" / "service.py"
            product.parent.mkdir(parents=True)
            product.write_text("def product_target():\n    pass\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(repository), "add", "fixtures/first.py", "src/package/service.py"],
                check=True,
            )
            self.assertEqual(
                _verification_candidate(config)[:2],
                ("product_target", "src/package/service.py"),
            )

    def test_stop_is_stateful_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository, config, path = configured_project(Path(raw))
            resolution = ProjectResolution(
                "configured", repository, "ready", path
            )
            with patch("codebase_atlas.simple_cli.resolve_project", return_value=resolution):
                first, first_code = stop_project(repository, timeout_seconds=0)
                second, second_code = stop_project(repository, timeout_seconds=0)
            self.assertEqual((first_code, second_code), (0, 0))
            self.assertTrue(first["mutates"])
            self.assertFalse(second["mutates"])
            self.assertEqual(
                load_lifecycle_state(
                    config.data_dir, config.repository, config.project
                ).status,
                "stopped",
            )

    def test_enable_composes_existing_onboarding_codex_and_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository, config, path = configured_project(Path(raw))
            resolution = ProjectResolution(
                "configured", repository, "ready", path
            )
            onboarding = {
                "status": "planned", "config": str(path),
                "config_created": False,
            }
            ready_checks = [{"name": "all", "ok": True, "required": True}]
            with (
                patch("codebase_atlas.simple_cli.resolve_project", return_value=resolution),
                patch("codebase_atlas.simple_cli.build_plan", return_value=(onboarding, config)),
                patch(
                    "codebase_atlas.simple_cli.apply_plan",
                    return_value=(onboarding | {"status": "current"}, 0),
                ),
                patch(
                    "codebase_atlas.simple_cli.codex_plan",
                    return_value={"status": "planned"},
                ),
                patch(
                    "codebase_atlas.simple_cli.codex_apply",
                    return_value={"status": "ready", "mutates": True},
                ),
                patch("codebase_atlas.simple_cli.diagnose", return_value=ready_checks),
                patch(
                    "codebase_atlas.simple_cli.index_freshness",
                    return_value={"status": "fresh", "source_fingerprint": "fingerprint"},
                ),
                patch(
                    "codebase_atlas.simple_cli.inspect_installation",
                    return_value={"ok": True},
                ),
                patch(
                    "codebase_atlas.simple_cli._verification_query",
                    side_effect=lambda *_args, **_kwargs: (
                        self.assertEqual(
                            load_lifecycle_state(
                                config.data_dir, config.repository, config.project
                            ).status,
                            "ready",
                        )
                        or {"symbol": "target", "cross_project_negative": "pass"}
                    ),
                ),
            ):
                result, code = enable_project(repository)
            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["connection_status"], "configured_task_start_required")
            self.assertTrue(result["mutates"])
            self.assertEqual(
                load_lifecycle_state(
                    config.data_dir, config.repository, config.project
                ).status,
                "ready",
            )

    def test_failed_enable_restores_prior_stopped_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository, config, path = configured_project(Path(raw))
            publish_lifecycle_state(
                config.data_dir,
                ProjectLifecycleState.initial(
                    repository, config.project
                ).transition("stopped"),
            )
            resolution = ProjectResolution("configured", repository, "ready", path)
            onboarding = {"status": "planned", "config": str(path)}
            with (
                patch("codebase_atlas.simple_cli.resolve_project", return_value=resolution),
                patch("codebase_atlas.simple_cli.build_plan", return_value=(onboarding, config)),
                patch(
                    "codebase_atlas.simple_cli.apply_plan",
                    return_value=({"status": "failed", "error": "injected"}, 2),
                ),
            ):
                result, code = enable_project(repository)
            self.assertEqual(code, 2)
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(
                load_lifecycle_state(
                    config.data_dir, config.repository, config.project
                ).status,
                "stopped",
            )

    def test_main_emits_versioned_json(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = git_repository(Path(raw))
            output = StringIO()
            with patch(
                "codebase_atlas.simple_cli.stop_project",
                return_value=({
                    "schema_version": 1,
                    "operation": "stop",
                    "status": "not_enabled",
                    "repository": str(repository),
                    "atlas_version": "test",
                    "mutates": False,
                }, 0),
            ), redirect_stdout(output):
                self.assertEqual(main(["stop", "--repo", str(repository), "--json"]), 0)
            self.assertEqual(json.loads(output.getvalue())["schema_version"], 1)

    def test_main_enable_uses_the_versioned_provider_without_path_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = git_repository(root)
            installation = VersionedInstallation(
                "0.25.1", "test", root,
                root / "python",
                root / "codebase-atlas", root / "provider",
                "provider-test", "a" * 64, "b" * 64,
            )
            captured = {}

            def enabled(_repository, **kwargs):
                captured.update(kwargs)
                return ({
                    "schema_version": 1,
                    "operation": "enable",
                    "status": "ready",
                    "repository": str(repository),
                    "atlas_version": "test",
                    "mutates": False,
                }, 0)

            with (
                patch(
                    "codebase_atlas.simple_cli._enable_runtime_installation",
                    return_value=installation,
                ),
                patch("codebase_atlas.simple_cli._same_executable", return_value=True),
                patch("codebase_atlas.simple_cli.enable_project", side_effect=enabled),
                redirect_stdout(StringIO()),
            ):
                code = main(["enable", "--repo", str(repository), "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(captured["cbm_binary"], installation.provider_binary)

    def test_update_is_noop_when_latest_stable_is_current(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository, config, path = configured_project(Path(raw))
            publish_lifecycle_state(
                config.data_dir,
                ProjectLifecycleState.initial(
                    repository, config.project, atlas_version="0.24.0"
                ),
            )
            resolution = ProjectResolution("configured", repository, "ready", path)
            installer_calls = []
            with patch("codebase_atlas.simple_cli.resolve_project", return_value=resolution):
                result, code = update_project(
                    repository,
                    release_fetcher=lambda: SimpleNamespace(version="0.24.0"),
                    installer=lambda _release: installer_calls.append(True),
                )
            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "current")
            self.assertFalse(result["mutates"])
            self.assertEqual(installer_calls, [])

    def test_update_switches_verified_installation_and_preserves_ready_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository, config, path = configured_project(root)
            publish_lifecycle_state(
                config.data_dir,
                ProjectLifecycleState.initial(
                    repository, config.project, atlas_version="0.24.0"
                ),
            )
            environment = root / "installation"
            environment.mkdir()
            for name in ("python", "atlas", "provider"):
                (environment / name).touch()
            installation = VersionedInstallation(
                "0.25.0", "test", environment,
                environment / "python", environment / "atlas",
                environment / "provider", "provider-2", "a" * 64, "b" * 64,
            )
            resolution = ProjectResolution("configured", repository, "ready", path)
            codex_target = repository / ".codex/config.toml"
            with (
                patch("codebase_atlas.simple_cli.resolve_project", return_value=resolution),
                patch(
                    "codebase_atlas.simple_cli.codex_plan",
                    return_value={"status": "planned", "target": str(codex_target)},
                ),
                patch("codebase_atlas.simple_cli.codex_apply"),
                patch(
                    "codebase_atlas.simple_cli._external_doctor",
                    return_value={"status": "ready"},
                ),
                patch(
                    "codebase_atlas.simple_cli.inspect_installation",
                    return_value={"ok": True},
                ),
                patch(
                    "codebase_atlas.simple_cli._verification_query",
                    return_value={"symbol": "target", "cross_project_negative": "pass"},
                ),
            ):
                result, code = update_project(
                    repository,
                    release_fetcher=lambda: SimpleNamespace(version="0.25.0"),
                    installer=lambda _release: (installation, True),
                )
            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "updated")
            self.assertEqual(AtlasConfig.load(path).cbm_binary, installation.provider_binary)
            state = load_lifecycle_state(
                config.data_dir, config.repository, config.project
            )
            self.assertEqual(state.status, "ready")
            self.assertEqual(state.atlas_version, "0.25.0")

    def test_failed_update_restores_config_codex_bytes_and_stopped_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository, config, path = configured_project(root)
            publish_lifecycle_state(
                config.data_dir,
                ProjectLifecycleState.initial(
                    repository, config.project, atlas_version="0.24.0"
                ).transition("stopped"),
            )
            environment = root / "installation"
            environment.mkdir()
            for name in ("python", "atlas", "provider"):
                (environment / name).touch()
            installation = VersionedInstallation(
                "0.25.0", "test", environment,
                environment / "python", environment / "atlas",
                environment / "provider", "provider-2", "a" * 64, "b" * 64,
            )
            codex_target = repository / ".codex/config.toml"
            codex_target.parent.mkdir()
            codex_target.write_text('model = "preserved"\n', encoding="utf-8")
            config_before = path.read_bytes()
            codex_before = codex_target.read_bytes()
            resolution = ProjectResolution("configured", repository, "ready", path)
            with (
                patch("codebase_atlas.simple_cli.resolve_project", return_value=resolution),
                patch(
                    "codebase_atlas.simple_cli.codex_plan",
                    return_value={"status": "planned", "target": str(codex_target)},
                ),
                patch("codebase_atlas.simple_cli.codex_apply"),
                patch(
                    "codebase_atlas.simple_cli._external_doctor",
                    side_effect=RuntimeError("injected acceptance failure"),
                ),
            ):
                result, code = update_project(
                    repository,
                    release_fetcher=lambda: SimpleNamespace(version="0.25.0"),
                    installer=lambda _release: (installation, True),
                )
            self.assertEqual(code, 2)
            self.assertIn("injected acceptance failure", result["error"])
            self.assertEqual(path.read_bytes(), config_before)
            self.assertEqual(codex_target.read_bytes(), codex_before)
            self.assertEqual(
                load_lifecycle_state(
                    config.data_dir, config.repository, config.project
                ).status,
                "stopped",
            )

    def test_remove_moves_owned_config_and_data_to_recovery_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository, config, path = configured_project(root)
            publish_lifecycle_state(
                config.data_dir,
                ProjectLifecycleState.initial(repository, config.project),
            )
            (config.data_dir / "owned-index").write_text("data", encoding="utf-8")
            resolution = ProjectResolution("configured", repository, "ready", path)
            codex_target = repository / ".codex/config.toml"
            with (
                patch.dict(os.environ, {"XDG_DATA_HOME": str(root / "xdg")}),
                patch(
                    "codebase_atlas.simple_cli.resolve_project",
                    return_value=resolution,
                ),
                patch(
                    "codebase_atlas.simple_cli.codex_plan",
                    return_value={"status": "planned", "target": str(codex_target)},
                ),
                patch(
                    "codebase_atlas.simple_cli.codex_remove",
                    return_value={"status": "absent", "mutates": False},
                ),
            ):
                first, first_code = remove_project(repository, timeout_seconds=0)
                second, second_code = remove_project(repository, timeout_seconds=0)
            self.assertEqual((first_code, second_code), (0, 0))
            self.assertTrue(first["mutates"])
            self.assertFalse(second["mutates"])
            self.assertFalse(path.exists())
            self.assertFalse(config.data_dir.exists())
            receipt = Path(first["receipt"])
            self.assertTrue(receipt.is_file())
            recovered = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertTrue(Path(recovered["recovered_config"]).is_file())
            self.assertTrue(
                (Path(recovered["recovered_data_dir"]) / "owned-index").is_file()
            )

    def test_remove_failure_restores_project_and_clears_incomplete_marker(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository, config, path = configured_project(root)
            publish_lifecycle_state(
                config.data_dir,
                ProjectLifecycleState.initial(repository, config.project),
            )
            (config.data_dir / "owned-index").write_text("data", encoding="utf-8")
            resolution = ProjectResolution("configured", repository, "ready", path)
            codex_target = repository / ".codex/config.toml"
            from codebase_atlas import simple_cli

            real_publish = simple_cli.publish_removal_marker
            calls = 0

            def fail_final_marker(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected final marker failure")
                return real_publish(*args, **kwargs)

            with (
                patch.dict(os.environ, {"XDG_DATA_HOME": str(root / "xdg")}),
                patch(
                    "codebase_atlas.simple_cli.resolve_project",
                    return_value=resolution,
                ),
                patch(
                    "codebase_atlas.simple_cli.codex_plan",
                    return_value={"status": "planned", "target": str(codex_target)},
                ),
                patch(
                    "codebase_atlas.simple_cli.codex_remove",
                    return_value={"status": "absent", "mutates": False},
                ),
                patch(
                    "codebase_atlas.simple_cli.publish_removal_marker",
                    side_effect=fail_final_marker,
                ),
            ):
                result, code = remove_project(repository, timeout_seconds=0)
                marker = simple_cli.load_removal_marker(repository)
            self.assertEqual(code, 2)
            self.assertIn("injected final marker failure", result["error"])
            self.assertTrue(path.is_file())
            self.assertTrue((config.data_dir / "owned-index").is_file())
            self.assertIsNone(marker)
            self.assertEqual(
                load_lifecycle_state(
                    config.data_dir, config.repository, config.project
                ).status,
                "ready",
            )

    def test_enable_restores_a_removed_project_before_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository, config, path = configured_project(root)
            publish_lifecycle_state(
                config.data_dir,
                ProjectLifecycleState.initial(repository, config.project),
            )
            (config.data_dir / "owned-index").write_text("data", encoding="utf-8")
            resolution = ProjectResolution("configured", repository, "ready", path)
            codex_target = repository / ".codex/config.toml"
            onboarding = {"status": "planned", "config": str(path)}
            ready_checks = [{"name": "all", "ok": True, "required": True}]
            with (
                patch.dict(os.environ, {"XDG_DATA_HOME": str(root / "xdg")}),
                patch(
                    "codebase_atlas.simple_cli.resolve_project",
                    return_value=resolution,
                ),
                patch(
                    "codebase_atlas.simple_cli.codex_plan",
                    return_value={"status": "planned", "target": str(codex_target)},
                ),
                patch(
                    "codebase_atlas.simple_cli.codex_remove",
                    return_value={"status": "absent", "mutates": False},
                ),
            ):
                removed, remove_code = remove_project(repository, timeout_seconds=0)
                self.assertEqual(remove_code, 0)
                with (
                    patch(
                        "codebase_atlas.simple_cli.build_plan",
                        return_value=(onboarding, config),
                    ),
                    patch(
                        "codebase_atlas.simple_cli.apply_plan",
                        return_value=(onboarding | {"status": "current"}, 0),
                    ),
                    patch(
                        "codebase_atlas.simple_cli.codex_apply",
                        return_value={"status": "ready", "mutates": True},
                    ),
                    patch(
                        "codebase_atlas.simple_cli.diagnose",
                        return_value=ready_checks,
                    ),
                    patch(
                        "codebase_atlas.simple_cli.index_freshness",
                        return_value={
                            "status": "fresh", "source_fingerprint": "fingerprint"
                        },
                    ),
                    patch(
                        "codebase_atlas.simple_cli.inspect_installation",
                        return_value={"ok": True},
                    ),
                    patch(
                        "codebase_atlas.simple_cli._verification_query",
                        return_value={
                            "symbol": "target", "cross_project_negative": "pass"
                        },
                    ),
                ):
                    enabled, enable_code = enable_project(repository)
                marker = load_removal_marker(repository)
            self.assertEqual(enable_code, 0)
            self.assertTrue(enabled["mutates"])
            self.assertTrue(path.is_file())
            self.assertTrue((config.data_dir / "owned-index").is_file())
            self.assertIsNone(marker)
            self.assertEqual(
                load_lifecycle_state(
                    config.data_dir, config.repository, config.project
                ).status,
                "ready",
            )


if __name__ == "__main__":
    unittest.main()
