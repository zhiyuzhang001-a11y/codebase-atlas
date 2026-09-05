"""Stable MCP bootstrap that swaps versioned project backends at request boundaries."""

from __future__ import annotations

import json
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from threading import Thread
from typing import Any, Callable, Protocol

from . import __version__
from .config import AtlasConfig
from .mcp import McpServer
from .project_discovery import ProjectResolution, resolve_project
from .project_lifecycle import operational_lifecycle_status
from .release_installation import load_versioned_installation


class BackendSession(Protocol):
    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None: ...
    def close(self) -> None: ...


class SubprocessBackendSession:
    def __init__(self, command: list[str], *, timeout_seconds: float = 65.0) -> None:
        self.timeout_seconds = timeout_seconds
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        if self.process.stdin is None or self.process.stdout is None:
            self.process.kill()
            raise RuntimeError("Atlas backend pipes are unavailable")
        self._responses: Queue[dict[str, Any] | BaseException | None] = Queue()

        def read_responses() -> None:
            try:
                assert self.process.stdout is not None
                for line in self.process.stdout:
                    if not line.strip():
                        continue
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise RuntimeError("Atlas backend response is not an object")
                    self._responses.put(value)
            except BaseException as exc:
                self._responses.put(exc)
            finally:
                self._responses.put(None)

        self._reader = Thread(
            target=read_responses,
            name="codebase-atlas-backend-reader",
            daemon=True,
        )
        self._reader.start()
        self._closed = False
        initialized = self.handle({
            "jsonrpc": "2.0",
            "id": "atlas-bootstrap-initialize",
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}},
        })
        if not isinstance(initialized, dict) or "result" not in initialized:
            self.close()
            raise RuntimeError("Atlas backend MCP initialization failed")
        self.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if self.process.poll() is not None:
            raise RuntimeError("Atlas backend exited unexpectedly")
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        if "id" not in message:
            return None
        try:
            response = self._responses.get(timeout=self.timeout_seconds)
        except Empty as exc:
            raise TimeoutError("timed out waiting for Atlas backend response") from exc
        if response is None:
            raise RuntimeError("Atlas backend closed before responding")
        if isinstance(response, BaseException):
            raise RuntimeError(f"Atlas backend response failed: {response}") from response
        if response.get("id") != message.get("id"):
            raise RuntimeError("Atlas backend response id mismatch")
        return response

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self.process
        if process.poll() is None:
            try:
                if process.stdin is not None:
                    process.stdin.close()
                process.wait(timeout=3.0)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.terminate()
                except OSError:
                    pass
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except OSError:
                        pass
                    process.wait(timeout=3.0)
        self._reader.join(timeout=1.0)
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        if process.stdout is not None and not process.stdout.closed:
            process.stdout.close()


BackendFactory = Callable[[Path, AtlasConfig, dict[str, Any]], BackendSession]


def _backend_command(
    config: Path,
    lifecycle: dict[str, Any],
    *,
    stale_policy: str,
    auto_update: str,
    auto_update_timeout: float,
    version_check: str,
) -> list[str]:
    version = str(lifecycle.get("atlas_version", ""))
    if version:
        try:
            executable = load_versioned_installation(version).atlas_executable
            prefix = [str(executable)]
        except RuntimeError:
            if version != __version__:
                raise
            prefix = [sys.executable, "-m", "codebase_atlas.cli"]
    else:
        prefix = [sys.executable, "-m", "codebase_atlas.cli"]
    return [
        *prefix, "mcp", "--config", str(config),
        "--stale-policy", stale_policy,
        "--auto-update", auto_update,
        "--auto-update-timeout", str(auto_update_timeout),
        "--version-check", version_check,
    ]


class ReloadingMcpServer:
    """One host connection with a dynamically selected exact-project backend."""

    def __init__(
        self,
        root: Path,
        *,
        stale_policy: str = "warn",
        auto_update: str = "on-query",
        auto_update_timeout: float = 60.0,
        version_check: str = "notify",
        resolver: Callable[[Path | None], ProjectResolution] = resolve_project,
        backend_factory: BackendFactory | None = None,
    ) -> None:
        initial = resolver(root)
        self.root = initial.root.resolve()
        self.stale_policy = stale_policy
        self.auto_update = auto_update
        self.auto_update_timeout = auto_update_timeout
        self.version_check = version_check
        self.resolver = resolver
        self.backend_factory = backend_factory or self._subprocess_backend
        self.backend: BackendSession | None = None
        self.backend_fingerprint: tuple[Any, ...] | None = None

    def _subprocess_backend(
        self, config_path: Path, config: AtlasConfig, lifecycle: dict[str, Any]
    ) -> BackendSession:
        return SubprocessBackendSession(_backend_command(
            config_path,
            lifecycle,
            stale_policy=self.stale_policy,
            auto_update=self.auto_update,
            auto_update_timeout=self.auto_update_timeout,
            version_check=self.version_check,
        ))

    def _close_backend(self) -> None:
        if self.backend is not None:
            self.backend.close()
        self.backend = None
        self.backend_fingerprint = None

    def close(self) -> None:
        self._close_backend()

    def _selection(
        self,
    ) -> tuple[ProjectResolution, AtlasConfig | None, dict[str, Any], tuple[Any, ...] | None]:
        resolution = self.resolver(self.root)
        if resolution.root.resolve() != self.root:
            status = resolution.operational_status() | {
                "status": "repository_mismatch",
                "ok": False,
                "reason": "bootstrap_repository_identity_changed",
            }
            return resolution, None, status, None
        if resolution.status != "configured" or resolution.config is None:
            return resolution, None, resolution.operational_status(), None
        try:
            metadata = resolution.config.stat()
            config = AtlasConfig.load(resolution.config)
            lifecycle = operational_lifecycle_status(
                config.data_dir, config.repository, config.project
            )
        except (OSError, KeyError, TypeError, ValueError) as exc:
            status = resolution.operational_status() | {
                "status": "invalid_config",
                "ok": False,
                "reason": "bootstrap_config_reload_failed",
                "detail": str(exc),
            }
            return resolution, None, status, None
        fingerprint = (
            str(resolution.config.resolve()), metadata.st_dev, metadata.st_ino,
            metadata.st_mtime_ns, metadata.st_size,
            lifecycle.get("operation_generation"), lifecycle.get("status"),
            lifecycle.get("atlas_version"), lifecycle.get("provider_version"),
            lifecycle.get("index_generation"),
        )
        return resolution, config, lifecycle, fingerprint

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        resolution, config, status, fingerprint = self._selection()
        if method in {"initialize", "ping", "tools/list", "notifications/initialized"}:
            if (
                config is None
                or not status.get("ok")
                or (
                    self.backend is not None
                    and fingerprint != self.backend_fingerprint
                )
            ):
                self._close_backend()
            return McpServer(
                None,
                status,
                "error",
                instructions=(
                    f"Codebase Atlas bootstrap is bound to {self.root}. "
                    "Call project_status before using repository facts."
                ),
            ).handle(message)
        lifecycle = status
        if config is None or not lifecycle.get("ok"):
            self._close_backend()
            return McpServer(
                None,
                lifecycle,
                "error",
                instructions=f"Codebase Atlas is unavailable for {self.root}.",
            ).handle(message)
        if self.backend is None or fingerprint != self.backend_fingerprint:
            self._close_backend()
            assert resolution.config is not None
            self.backend = self.backend_factory(resolution.config, config, lifecycle)
            self.backend_fingerprint = fingerprint
        try:
            return self.backend.handle(message)
        except Exception:
            self._close_backend()
            raise
