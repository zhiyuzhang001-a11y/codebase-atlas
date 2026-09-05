"""Bounded persistent MCP stdio transport for the managed Provider."""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import threading
from time import monotonic_ns
from typing import Any, BinaryIO, Callable

from .lifecycle import GlobalCbmLock
from .provider_layout import configure_managed_provider_cache, provider_environment


MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_STDERR_BYTES = 1024 * 1024
EOF_CLOSE_TIMEOUT_SECONDS = 10.0
TERMINATE_TIMEOUT_SECONDS = 3.0
KILL_TIMEOUT_SECONDS = 3.0


class ProviderInitializeTimeout(TimeoutError):
    """The Provider child acquired admission but did not initialize in time."""


def _read_frame(stream: BinaryIO) -> dict[str, Any]:
    first = stream.readline()
    if not first:
        raise EOFError("Provider stdout closed")
    if not first.lower().startswith(b"content-length:"):
        if len(first) > MAX_FRAME_BYTES:
            raise ValueError("Provider returned oversized MCP line")
        try:
            value = json.loads(first)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Provider returned malformed MCP framing") from exc
        if not isinstance(value, dict):
            raise ValueError("Provider returned a non-object MCP response")
        return value
    try:
        length = int(first.split(b":", 1)[1].strip())
    except ValueError as exc:
        raise ValueError("Provider returned malformed Content-Length") from exc
    if length < 0 or length > MAX_FRAME_BYTES:
        raise ValueError("Provider returned oversized Content-Length")
    while True:
        line = stream.readline()
        if not line:
            raise EOFError("Provider closed during MCP headers")
        if line in {b"\n", b"\r\n"}:
            break
    payload = stream.read(length)
    if len(payload) != length:
        raise EOFError("Provider closed during MCP payload")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Provider returned malformed MCP JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Provider returned a non-object MCP response")
    return value


class CodebaseMemoryMcpTransport:
    """Own one repository-bound Provider child and serialize MCP requests."""

    def __init__(
        self,
        binary: Path,
        repository: Path,
        cache_dir: Path,
        *,
        exclusive: bool,
        client_version: str,
        lock: GlobalCbmLock | None = None,
        observer: Callable[[dict[str, Any]], None] | None = None,
        arguments: tuple[str, ...] = (),
        managed_cache: bool = False,
    ) -> None:
        self.binary = binary.resolve()
        self.arguments = tuple(arguments)
        self.repository = repository.resolve()
        self.cache_dir = cache_dir.resolve()
        self.exclusive = exclusive
        self.client_version = client_version
        self.lock = lock or GlobalCbmLock()
        self.managed_cache = managed_cache
        self.observer = observer
        self.process: subprocess.Popen[bytes] | None = None
        self._next_id = 1
        self._responses: queue.Queue[tuple[str, object]] = queue.Queue()
        self._request_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr = bytearray()
        self._event_lock = threading.Lock()
        self._event_sequence = 0

    @property
    def stderr_text(self) -> str:
        return bytes(self._stderr).decode("utf-8", "replace")

    def _process_state(self) -> str:
        process = self.process
        if process is None:
            return "absent"
        returncode = process.poll()
        return "running" if returncode is None else f"exited:{returncode}"

    def _event(self, event: str, request_id: int | None = None, **details: Any) -> None:
        observer = self.observer
        if observer is None:
            return
        with self._event_lock:
            self._event_sequence += 1
            payload = {
                "sequence": self._event_sequence,
                "monotonic_ns": monotonic_ns(),
                "component": "provider_transport",
                "event": event,
                "request_id": request_id,
                "process_state": self._process_state(),
                **details,
            }
        try:
            observer(payload)
        except BaseException:
            # Diagnostics must never alter transport behavior.
            return

    def _drain_stdout(self, stream: BinaryIO) -> None:
        try:
            while True:
                self._responses.put(("response", _read_frame(stream)))
        except BaseException as exc:
            self._responses.put(("error", exc))

    def _drain_stderr(self, stream: BinaryIO) -> None:
        try:
            for chunk in iter(lambda: stream.read(8192), b""):
                remaining = MAX_STDERR_BYTES - len(self._stderr)
                if remaining > 0:
                    self._stderr.extend(chunk[:remaining])
        except (OSError, ValueError):
            return

    @staticmethod
    def _write(stream: BinaryIO, message: dict[str, Any]) -> None:
        payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
        if os.name == "nt":
            stream.write(payload + b"\n")
        else:
            stream.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii"))
            stream.write(payload)
        stream.flush()

    def _request(self, method: str, params: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        with self._request_lock:
            process = self.process
            if process is None or process.stdin is None or process.poll() is not None:
                raise RuntimeError("Provider MCP child is not running")
            request_id = self._next_id
            self._next_id += 1
            try:
                self._write(process.stdin, {
                    "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
                })
                self._event("transport_request_write", request_id, method=method)
                kind, value = self._responses.get(timeout=timeout_seconds)
                if kind == "error":
                    raise RuntimeError(f"Provider MCP transport failed: {value}")
                response = value
                if not isinstance(response, dict):
                    raise RuntimeError("Provider MCP returned an invalid response")
                self._event(
                    "transport_response_read", request_id,
                    method=method, response_id=response.get("id"),
                )
                if response.get("id") != request_id:
                    raise RuntimeError("Provider MCP response id mismatch")
                if "error" in response:
                    raise RuntimeError(f"Provider MCP tool error: {response['error']}")
                result = response.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError("Provider MCP response lacks an object result")
                if method == "tools/call":
                    self._event(
                        "provider_payload_received", request_id,
                        structured_payload=result.get("structuredContent"),
                        is_error=bool(result.get("isError")),
                    )
                return result
            except queue.Empty as exc:
                self._event(
                    "transport_request_error", request_id,
                    exception_type="TimeoutError", exception_message="request timed out",
                )
                self._shutdown()
                timeout_type = ProviderInitializeTimeout if method == "initialize" else TimeoutError
                raise timeout_type(f"Provider MCP request {request_id} timed out") from exc
            except (BrokenPipeError, OSError, RuntimeError) as exc:
                self._event(
                    "transport_request_error", request_id,
                    exception_type=type(exc).__name__, exception_message=str(exc),
                )
                self._shutdown()
                raise

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        process = self.process
        if process is None or process.stdin is None:
            raise RuntimeError("Provider MCP child is not running")
        self._write(process.stdin, {"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _start(self, *, lock_timeout_seconds: float, initialize_timeout_seconds: float) -> bool:
        with self._state_lock:
            if self.process is not None and self.process.poll() is None:
                return False
            admission_started = monotonic_ns()
            self.lock.acquire(timeout_seconds=lock_timeout_seconds)
            self._event(
                "provider_admission_acquired",
                wait_ms=(monotonic_ns() - admission_started) / 1_000_000.0,
            )
            try:
                environment = provider_environment(self.cache_dir, self.repository)
                if self.managed_cache:
                    configure_managed_provider_cache(
                        self.binary, self.cache_dir, self.repository
                    )
                process = subprocess.Popen(
                    [str(self.binary), *self.arguments], cwd=self.repository, env=environment,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    bufsize=0,
                )
                if process.stdin is None or process.stdout is None or process.stderr is None:
                    raise RuntimeError("Provider MCP child lacks stdio pipes")
                self.process = process
                self._responses = queue.Queue()
                self._stderr = bytearray()
                self._reader_thread = threading.Thread(
                    target=self._drain_stdout, args=(process.stdout,), daemon=True,
                    name="atlas-provider-stdout",
                )
                self._stderr_thread = threading.Thread(
                    target=self._drain_stderr, args=(process.stderr,), daemon=True,
                    name="atlas-provider-stderr",
                )
                self._reader_thread.start()
                self._stderr_thread.start()
                self._event("transport_start", child_pid=process.pid)
                result = self._request("initialize", {
                    "protocolVersion": "2025-11-25", "capabilities": {},
                    "clientInfo": {"name": "codebase-atlas", "version": self.client_version},
                }, initialize_timeout_seconds)
                if not isinstance(result.get("serverInfo"), dict):
                    raise RuntimeError("Provider initialize response lacks serverInfo")
                self._notify("notifications/initialized")
                if not self.exclusive:
                    self.lock.release()
                return True
            except BaseException:
                self._shutdown()
                raise

    def start(self, *, timeout_seconds: float | None = None) -> bool:
        """Start with one compatibility timeout for admission and initialization."""
        timeout = 30.0 if timeout_seconds is None else timeout_seconds
        return self._start(
            lock_timeout_seconds=timeout,
            initialize_timeout_seconds=timeout,
        )

    def start_for_request(
        self, *, lock_timeout_seconds: float, initialize_timeout_seconds: float
    ) -> bool:
        """Start with independent admission and MCP initialize deadlines."""
        return self._start(
            lock_timeout_seconds=lock_timeout_seconds,
            initialize_timeout_seconds=initialize_timeout_seconds,
        )

    def call(
        self, tool: str, arguments: dict[str, Any], *, timeout_ms: int
    ) -> dict[str, Any]:
        self.start(timeout_seconds=timeout_ms / 1000.0)
        result = self._request("tools/call", {
            "name": tool, "arguments": arguments,
        }, timeout_ms / 1000.0)
        if result.get("isError"):
            raise RuntimeError(f"Provider tool failed: {result}")
        payload = result.get("structuredContent")
        if not isinstance(payload, dict):
            raise RuntimeError("Provider result lacks structuredContent")
        return payload

    def _shutdown(self) -> None:
        with self._state_lock:
            process = self.process
            self.process = None
            try:
                if process is not None:
                    if process.stdin is not None:
                        try:
                            process.stdin.close()
                        except (BrokenPipeError, OSError):
                            pass
                    try:
                        process.wait(timeout=EOF_CLOSE_TIMEOUT_SECONDS)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        try:
                            process.wait(timeout=TERMINATE_TIMEOUT_SECONDS)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=KILL_TIMEOUT_SECONDS)
                    for stream in (process.stdout, process.stderr):
                        if stream is not None:
                            try:
                                stream.close()
                            except OSError:
                                pass
                    for thread in (self._reader_thread, self._stderr_thread):
                        if thread is not None and thread is not threading.current_thread():
                            thread.join(timeout=1.0)
            finally:
                self._reader_thread = None
                self._stderr_thread = None
                self.lock.release()
                self._event(
                    "cleanup_complete",
                    child_returncode=None if process is None else process.returncode,
                    stderr_tail=self.stderr_text,
                )

    def close(self) -> None:
        self._shutdown()

    def __enter__(self) -> "CodebaseMemoryMcpTransport":
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()
