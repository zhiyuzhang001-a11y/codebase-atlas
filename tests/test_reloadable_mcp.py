from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from codebase_atlas import __version__
from codebase_atlas.config import AtlasConfig
from codebase_atlas.project_discovery import ProjectResolution
from codebase_atlas.reloadable_mcp import (
    ReloadingMcpServer,
    SubprocessBackendSession,
    _backend_command,
)


def call(request_id: int, name: str = "definition") -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": {}},
    }


class FakeBackend:
    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.closed = False

    def handle(self, message: dict) -> dict | None:
        if "id" not in message:
            return None
        return {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {"marker": self.marker},
        }

    def close(self) -> None:
        self.closed = True


class ReloadingMcpTests(unittest.TestCase):
    def test_live_subprocess_backend_is_replaced_at_request_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            config_path = root / ".codebase-atlas.toml"
            AtlasConfig(
                root, "python", root / "node", root / "cbm", root / "python",
                root / "data", "project",
            ).write(config_path)
            child = root / "backend.py"
            child.write_text(
                "import json, sys\n"
                "marker = sys.argv[1]\n"
                "for line in sys.stdin:\n"
                "    message = json.loads(line)\n"
                "    if 'id' not in message:\n"
                "        continue\n"
                "    result = ({'protocolVersion': '2025-11-25'} "
                "if message['method'] == 'initialize' else {'marker': marker})\n"
                "    print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], "
                "'result': result}), flush=True)\n",
                encoding="utf-8",
            )
            resolution = ProjectResolution(
                "configured", root, "project_config_and_index_ready", config_path
            )
            lifecycle = {
                "status": "ready", "ok": True, "reason": "project_ready",
                "operation_generation": 1, "atlas_version": __version__,
                "provider_version": "provider-1", "index_generation": "index-1",
            }
            sessions: list[SubprocessBackendSession] = []

            def factory(_path, _config, state):
                session = SubprocessBackendSession([
                    sys.executable, str(child), str(state["operation_generation"])
                ])
                sessions.append(session)
                return session

            with patch(
                "codebase_atlas.reloadable_mcp.operational_lifecycle_status",
                side_effect=lambda *_args: dict(lifecycle),
            ):
                server = ReloadingMcpServer(
                    root, resolver=lambda _root: resolution,
                    backend_factory=factory,
                )
                self.assertEqual(server.handle(call(1))["result"]["marker"], "1")
                first_process = sessions[0].process
                lifecycle["operation_generation"] = 2
                self.assertEqual(server.handle(call(2))["result"]["marker"], "2")
                self.assertIsNotNone(first_process.poll())
                server.close()
                self.assertIsNotNone(sessions[1].process.poll())

    def test_one_connection_tracks_enable_stop_resume_and_update(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            config_path = root / "custom-atlas.toml"
            data_dir = root / "data"
            configured = AtlasConfig(
                root, "python", root / "node", root / "cbm", root / "python",
                data_dir, "project",
            )
            configured.write(config_path)
            current_resolution = [
                ProjectResolution("not_configured", root, "atlas_config_missing")
            ]
            lifecycle = {
                "schema_version": 1,
                "status": "ready",
                "ok": True,
                "reason": "project_ready",
                "repository": str(root),
                "project": "project",
                "operation_generation": 1,
                "atlas_version": __version__,
                "provider_version": "provider-1",
            }
            sessions: list[FakeBackend] = []
            observed_paths: list[Path] = []

            def resolver(_start):
                return current_resolution[0]

            def factory(path, _config, state):
                observed_paths.append(path)
                backend = FakeBackend(
                    f'{state["atlas_version"]}:{state["operation_generation"]}'
                )
                sessions.append(backend)
                return backend

            with patch(
                "codebase_atlas.reloadable_mcp.operational_lifecycle_status",
                side_effect=lambda *_args: dict(lifecycle),
            ):
                server = ReloadingMcpServer(
                    root, resolver=resolver, backend_factory=factory
                )
                unavailable = server.handle(call(1, "project_status"))
                self.assertEqual(
                    unavailable["result"]["structuredContent"]["status"],
                    "not_configured",
                )
                self.assertEqual(sessions, [])

                current_resolution[0] = ProjectResolution(
                    "configured", root, "project_config_and_index_ready", config_path
                )
                first = server.handle(call(2))
                self.assertEqual(first["result"]["marker"], f"{__version__}:1")
                self.assertEqual(observed_paths, [config_path])

                lifecycle.update(status="stopped", ok=False, reason="project_stopped")
                stopped = server.handle(call(3))
                self.assertTrue(stopped["result"]["isError"])
                self.assertEqual(stopped["result"]["structuredContent"]["code"], "stopped")
                self.assertTrue(sessions[0].closed)

                lifecycle.update(
                    status="ready", ok=True, reason="project_ready",
                    operation_generation=2,
                )
                resumed = server.handle(call(4))
                self.assertEqual(resumed["result"]["marker"], f"{__version__}:2")

                lifecycle.update(atlas_version="9.9.9", operation_generation=3)
                listed = server.handle({
                    "jsonrpc": "2.0", "id": 5, "method": "tools/list"
                })
                self.assertIn("tools", listed["result"])
                self.assertTrue(sessions[1].closed)
                updated = server.handle(call(6))
                self.assertEqual(updated["result"]["marker"], "9.9.9:3")
                self.assertEqual(len(sessions), 3)
                server.close()
                self.assertTrue(sessions[2].closed)

    def test_repository_identity_change_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            current = [ProjectResolution("not_configured", root, "missing")]
            server = ReloadingMcpServer(root, resolver=lambda _root: current[0])
            other = root / "other"
            other.mkdir()
            current[0] = ProjectResolution("not_configured", other, "missing")
            response = server.handle(call(1))
            project = response["result"]["structuredContent"]["project"]
            self.assertEqual(project["status"], "repository_mismatch")
            self.assertEqual(project["reason"], "bootstrap_repository_identity_changed")

    def test_missing_requested_version_never_falls_back_to_current_code(self) -> None:
        config = Path("/tmp/custom-atlas.toml")
        lifecycle = {"atlas_version": "9.9.9"}
        with patch(
            "codebase_atlas.reloadable_mcp.load_versioned_installation",
            side_effect=RuntimeError("missing"),
        ):
            with self.assertRaisesRegex(RuntimeError, "missing"):
                _backend_command(
                    config, lifecycle, stale_policy="warn", auto_update="on-query",
                    auto_update_timeout=60.0, version_check="notify",
                )

    def test_current_development_version_can_use_current_interpreter(self) -> None:
        with patch(
            "codebase_atlas.reloadable_mcp.load_versioned_installation",
            side_effect=RuntimeError("not installed"),
        ):
            command = _backend_command(
                Path("/tmp/custom-atlas.toml"), {"atlas_version": __version__},
                stale_policy="warn", auto_update="on-query",
                auto_update_timeout=60.0, version_check="notify",
            )
        self.assertEqual(command[1:3], ["-m", "codebase_atlas.cli"])


if __name__ == "__main__":
    unittest.main()
