from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from codebase_atlas.config import LEGACY_PROVIDER_LAYOUT, SHARED_PROVIDER_LAYOUT
from codebase_atlas.session_update import _graceful_run, session_start_update


class SessionUpdateTests(unittest.TestCase):
    def test_timeout_requests_graceful_termination_before_kill(self) -> None:
        process = unittest.mock.Mock()
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(["atlas"], 1),
            ('{"status":"failed"}', ""),
        ]
        with patch("codebase_atlas.session_update.subprocess.Popen", return_value=process):
            with self.assertRaises(subprocess.TimeoutExpired):
                _graceful_run(["atlas"], timeout=1)
        process.terminate.assert_called_once_with()
        process.kill.assert_not_called()

    def configured(
        self,
        language: str = "typescript",
        provider_layout: str = LEGACY_PROVIDER_LAYOUT,
    ):
        configured = unittest.mock.Mock()
        configured.data_dir = Path("data")
        configured.repository = Path("repo")
        configured.project = "project"
        configured.cache_dir = Path("cache")
        configured.language = language
        configured.provider_layout = provider_layout
        return configured

    def test_fresh_index_bypasses_subprocess_and_provider(self) -> None:
        def runner(*_args, **_kwargs):
            raise AssertionError("fresh session start must not spawn update")

        with (
            patch(
                "codebase_atlas.session_update.AtlasConfig.load",
                return_value=self.configured(),
            ),
            patch("codebase_atlas.session_update.index_freshness", return_value={
                "status": "fresh", "mode": "fast"
            }),
            patch("codebase_atlas.session_update.provider_database_health", return_value={
                "ok": True, "status": "ready"
            }),
        ):
            result = session_start_update(Path("config.toml"), runner=runner)
        self.assertEqual(result["reason"], "index_current")
        self.assertEqual(result["provider"]["status"], "not_started")

    def test_stale_index_reports_busy_without_waiting_or_spawning(self) -> None:
        def runner(*_args, **_kwargs):
            raise AssertionError("busy provider must not spawn update")

        lock = unittest.mock.Mock()
        lock.acquire.side_effect = TimeoutError("busy")
        with (
            patch(
                "codebase_atlas.session_update.AtlasConfig.load",
                return_value=self.configured(),
            ),
            patch("codebase_atlas.session_update.index_freshness", return_value={
                "status": "stale", "mode": "fast"
            }),
            patch("codebase_atlas.session_update.provider_database_health", return_value={
                "ok": True, "status": "ready"
            }),
            patch(
                "codebase_atlas.session_update.GlobalCbmLock",
                return_value=lock,
            ) as lock_factory,
        ):
            result = session_start_update(Path("config.toml"), runner=runner)
        lock_factory.assert_called_once_with(timeout_seconds=0.02)
        self.assertEqual(result["reason"], "provider_busy")
        self.assertTrue(result["previous_index_preserved"])

    def test_shared_stale_index_skips_global_busy_probe_and_runs_update(self) -> None:
        def runner(argv, **_kwargs):
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps({"status": "updated", "provider": {"status": "indexed"}}),
                "",
            )

        with (
            patch(
                "codebase_atlas.session_update.AtlasConfig.load",
                return_value=self.configured(provider_layout=SHARED_PROVIDER_LAYOUT),
            ),
            patch("codebase_atlas.session_update.index_freshness", return_value={
                "status": "stale", "mode": "fast"
            }),
            patch("codebase_atlas.session_update.provider_database_health", return_value={
                "ok": True, "status": "ready"
            }),
            patch(
                "codebase_atlas.session_update.GlobalCbmLock",
                side_effect=AssertionError("shared layout must not use the legacy global lock"),
            ),
            patch("codebase_atlas.session_update.plan_refresh", return_value={
                "status": "planned", "dirty_paths": ["sample.py"]
            }),
        ):
            result = session_start_update(Path("config.toml"), runner=runner)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "updated")

    def test_shared_fresh_index_with_stale_generation_runs_update(self) -> None:
        calls = []

        def runner(argv, **_kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps({"status": "updated", "provider": {"status": "indexed"}}),
                "",
            )

        with (
            patch(
                "codebase_atlas.session_update.AtlasConfig.load",
                return_value=self.configured(provider_layout=SHARED_PROVIDER_LAYOUT),
            ),
            patch("codebase_atlas.session_update.index_freshness", return_value={
                "status": "fresh", "mode": "fast", "source_fingerprint": "a" * 64
            }),
            patch("codebase_atlas.session_update.provider_database_health", return_value={
                "ok": True, "status": "ready"
            }),
            patch("codebase_atlas.session_update.plan_refresh", return_value={
                "status": "planned", "dirty_paths": ["sample.py"]
            }),
        ):
            result = session_start_update(Path("config.toml"), runner=runner)

        self.assertTrue(result["ok"])
        self.assertEqual(len(calls), 1)

    def test_current_fast_path_is_reported(self) -> None:
        def runner(argv, **kwargs):
            self.assertEqual(kwargs["timeout"], 12)
            return subprocess.CompletedProcess(
                argv, 0,
                json.dumps({
                    "status": "current",
                    "provider": {"status": "not_started"},
                }),
                "",
            )

        configured = unittest.mock.Mock()
        configured.data_dir = Path("data")
        configured.repository = Path("repo")
        configured.project = "project"
        configured.cache_dir = Path("cache")
        configured.language = "typescript"
        with (
            patch("codebase_atlas.session_update.AtlasConfig.load", return_value=configured),
            patch("codebase_atlas.session_update.index_freshness", return_value={
                "status": "unknown", "mode": "fast"
            }),
            patch("codebase_atlas.session_update.provider_database_health", return_value={
                "ok": True
            }),
        ):
            result = session_start_update(Path("config.toml"), timeout_seconds=12, runner=runner)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "current")
        self.assertEqual(result["provider"]["status"], "not_started")

    def test_timeout_preserves_previous_index(self) -> None:
        def runner(_argv, **_kwargs):
            raise subprocess.TimeoutExpired(["atlas"], 3)

        configured = unittest.mock.Mock()
        configured.data_dir = Path("data")
        configured.repository = Path("repo")
        configured.project = "project"
        configured.cache_dir = Path("cache")
        configured.language = "typescript"
        with (
            patch("codebase_atlas.session_update.AtlasConfig.load", return_value=configured),
            patch("codebase_atlas.session_update.index_freshness", return_value={
                "status": "unknown", "mode": "fast"
            }),
            patch("codebase_atlas.session_update.provider_database_health", return_value={
                "ok": True
            }),
        ):
            result = session_start_update(Path("config.toml"), timeout_seconds=3, runner=runner)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(result["previous_index_preserved"])

    def test_failure_is_explicit(self) -> None:
        def runner(argv, **_kwargs):
            return subprocess.CompletedProcess(argv, 2, '{"status":"blocked","error":"busy"}', "")

        configured = unittest.mock.Mock()
        configured.data_dir = Path("data")
        configured.repository = Path("repo")
        configured.project = "project"
        configured.cache_dir = Path("cache")
        configured.language = "typescript"
        with (
            patch("codebase_atlas.session_update.AtlasConfig.load", return_value=configured),
            patch("codebase_atlas.session_update.index_freshness", return_value={
                "status": "unknown", "mode": "fast"
            }),
            patch("codebase_atlas.session_update.provider_database_health", return_value={
                "ok": True
            }),
        ):
            result = session_start_update(Path("config.toml"), runner=runner)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "busy")


if __name__ == "__main__":
    unittest.main()
