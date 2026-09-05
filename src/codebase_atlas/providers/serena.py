"""Long-lived Serena semantic definition/reference provider."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
from typing import Any

from ..contracts import Node, SourceRange, repository_path


def _hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_result_path(value: str, repository: Path | None) -> str:
    try:
        return repository_path(value)
    except ValueError:
        if repository is None:
            raise
    candidate = (repository / value).resolve(strict=True)
    root = repository.resolve(strict=True)
    try:
        return repository_path(candidate.relative_to(root).as_posix())
    except ValueError:
        # Windows may expose the same directory through both an 8.3 short path
        # (RUNNER~1) and its long spelling.  Compare directory identities while
        # walking upward instead of trusting their textual prefixes.
        parts: list[str] = []
        current = candidate
        while current != current.parent:
            try:
                if current.samefile(root):
                    return repository_path(Path(*reversed(parts)).as_posix())
            except OSError:
                pass
            parts.append(current.name)
            current = current.parent
        raise


def normalize_serena_rows(
    rows: Any,
    *,
    query_type: str,
    symbol: str,
    repository: Path | None = None,
) -> tuple[Node, ...]:
    if not isinstance(rows, list):
        raise ValueError("Serena results must be a list")
    nodes: list[Node] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        path = _normalize_result_path(row["path"], repository)
        start = row["start_line"]
        end = row.get("end_line", start)
        start_column = row.get("start_column")
        end_column = row.get("end_column")
        provider_id = str(row.get("provider_id", symbol))
        occurrence = f":{start_column}" if start_column is not None else ""
        node_id = f"serena:{query_type}:{provider_id}:{path}:{start}{occurrence}"
        nodes.append(
            Node(
                id=node_id,
                kind="reference" if query_type == "references" else "definition",
                name=symbol,
                location=SourceRange(path, start, end, start_column, end_column),
                provider="serena-semantic",
                confidence=1.0,
                evidence_hash=_hash(row),
                attributes={"provider_id": provider_id, **dict(row.get("provenance", {}))},
            )
        )
    return tuple(nodes)


class SerenaSemanticProvider:
    name = "serena-semantic"

    def __init__(
        self,
        python: Path,
        runner: Path,
        repository: Path,
        serena_home: Path,
        metadata_root: Path,
        *,
        language: str,
        node_bin_dir: Path | None = None,
        timeout_seconds: float = 240.0,
    ) -> None:
        if language not in {"python", "typescript"}:
            raise ValueError(f"unsupported Serena language: {language}")
        self.python = python.absolute()
        self.runner = runner.resolve()
        self.repository = repository.resolve()
        self.serena_home = serena_home.resolve()
        self.metadata_root = metadata_root.resolve()
        self.language = language
        self.node_bin_dir = node_bin_dir.resolve() if node_bin_dir is not None else None
        self.timeout_seconds = timeout_seconds
        self._process: subprocess.Popen[str] | None = None
        self._stderr_handle: Any = None
        self.startup_ms = 0.0

    def _read(self, timeout_seconds: float | None = None) -> dict[str, Any]:
        if self._process is None or self._process.stdout is None:
            raise RuntimeError("Serena runner is not started")
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        ready, _, _ = select.select([self._process.stdout], [], [], timeout)
        if not ready:
            raise TimeoutError(f"Serena runner exceeded {timeout:.3f}s")
        line = self._process.stdout.readline()
        if not line:
            raise RuntimeError(f"Serena runner exited before responding (exit={self._process.poll()})")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("Serena runner response must be an object")
        return value

    def start(self, *, timeout_seconds: float | None = None) -> None:
        if self._process is not None:
            return
        self.serena_home.mkdir(parents=True, exist_ok=True)
        self.metadata_root.mkdir(parents=True, exist_ok=True)
        stderr_path = self.serena_home / "runner.stderr.log"
        self._stderr_handle = stderr_path.open("w", encoding="utf-8")
        environment = os.environ.copy()
        environment.update(
            {
                "SERENA_HOME": str(self.serena_home),
                "UV_CACHE_DIR": str(self.serena_home / "uv-cache"),
                "UV_TOOL_DIR": str(self.serena_home / "uv-tools"),
                "UV_PYTHON_INSTALL_DIR": str(self.serena_home / "uv-python"),
                "SERENA_USAGE_REPORTING": "false",
                "PYTHONUNBUFFERED": "1",
            }
        )
        if self.node_bin_dir is not None:
            environment["PATH"] = str(self.node_bin_dir) + os.pathsep + environment.get("PATH", "")
        self._process = subprocess.Popen(
            [
                str(self.python),
                str(self.runner),
                "--repo",
                str(self.repository),
                "--metadata-root",
                str(self.metadata_root),
                "--language",
                self.language,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr_handle,
            text=True,
            env=environment,
        )
        try:
            response = self._read(timeout_seconds)
            if response.get("status") != "ready":
                raise RuntimeError(f"Serena runner did not become ready: {response}")
            self.startup_ms = float(response.get("startup_ms", 0.0))
        except Exception:
            self._terminate()
            raise

    def query(
        self,
        query_type: str,
        symbol: str,
        *,
        target_path: str = "",
        target_owner: str = "",
        timeout_ms: int | None = None,
    ) -> tuple[Node, ...]:
        if query_type not in {"definition", "references"}:
            raise ValueError(f"unsupported Serena query: {query_type}")
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("Serena provider must be started before query")
        self._process.stdin.write(json.dumps({
            "query_type": query_type,
            "query": symbol,
            "target_path": target_path,
            "target_owner": target_owner,
        }) + "\n")
        self._process.stdin.flush()
        try:
            response = self._read(
                timeout_ms / 1000.0 if timeout_ms is not None else None
            )
        except TimeoutError:
            # A late response would corrupt the next request/response pairing.
            # Restart the Provider on the next query instead.
            self._terminate()
            raise
        if response.get("status") != "ok":
            raise RuntimeError(str(response.get("message", "unknown Serena error")))
        return normalize_serena_rows(
            response.get("results"),
            query_type=query_type,
            symbol=symbol,
            repository=self.repository,
        )

    def close(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            try:
                if process.stdin is not None:
                    process.stdin.write(json.dumps({"command": "shutdown"}) + "\n")
                    process.stdin.flush()
                self._read()
                process.wait(timeout=15)
            except (OSError, RuntimeError, TimeoutError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        self._process = None
        if self._stderr_handle is not None:
            self._stderr_handle.close()
            self._stderr_handle = None

    def _terminate(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self._process = None
        if self._stderr_handle is not None:
            self._stderr_handle.close()
            self._stderr_handle = None
