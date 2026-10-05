"""Owned, product-disabled rust-analyzer T2 definition/reference session."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import signal
import stat
import subprocess
import threading
import tomllib
from time import monotonic, sleep
from typing import Any, BinaryIO, Callable
from urllib.parse import urlparse
from urllib.request import url2pathname

from ..contracts import EvidenceProvenance, Node, SourceRange, repository_path
from ..index_state import repository_snapshot
from ..rust_runtime import RustToolchainRuntime
from ..rust_owned_command import run_owned
from ..rust_scope import (
    RustScopeError,
    validate_rust_build_context,
    validate_rust_source_scope,
)


PROVIDER_NAME = "rust-analyzer-lsp"
PROVIDER_VERSION = "1.98.0"
MAX_FRAME_BYTES = 32 * 1024 * 1024
MAX_STDERR_BYTES = 1024 * 1024
DEFAULT_READINESS_SECONDS = 60.0
CLEANUP_GRACE_SECONDS = 10.0
VersionRunner = Callable[..., subprocess.CompletedProcess[str]]


class RustAnalyzerError(RuntimeError):
    """A bounded rust-analyzer operation failed closed."""


class RustAnalyzerResponseError(RustAnalyzerError):
    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(f"rust-analyzer error {code}: {message}")
        self.code = code
        self.message = message


def _read_lsp_frame(stream: BinaryIO) -> dict[str, Any]:
    length: int | None = None
    while True:
        line = stream.readline()
        if not line:
            raise EOFError("rust-analyzer stdout closed")
        if line in {b"\n", b"\r\n"}:
            break
        if len(line) > 8192:
            raise RustAnalyzerError("rust-analyzer returned an oversized header")
        name, separator, raw_value = line.partition(b":")
        if not separator:
            raise RustAnalyzerError("rust-analyzer returned malformed LSP framing")
        if name.strip().lower() == b"content-length":
            try:
                length = int(raw_value.strip())
            except ValueError as exc:
                raise RustAnalyzerError("rust-analyzer returned invalid Content-Length") from exc
    if length is None or length < 0 or length > MAX_FRAME_BYTES:
        raise RustAnalyzerError("rust-analyzer returned unsafe Content-Length")
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise EOFError("rust-analyzer closed during an LSP payload")
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RustAnalyzerError("rust-analyzer returned invalid LSP JSON") from exc
    if not isinstance(value, dict):
        raise RustAnalyzerError("rust-analyzer returned a non-object LSP message")
    return value


def _write_lsp_frame(stream: BinaryIO, message: dict[str, Any]) -> None:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    stream.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii"))
    stream.write(payload)
    stream.flush()


def _hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


class RustAnalyzerProvider:
    """One repository- and generation-bound rust-analyzer process tree."""

    def __init__(
        self,
        analyzer: Path,
        repository: Path,
        project: str,
        generation: dict[str, Any],
        *,
        arguments: tuple[str, ...] = (),
        version_runner: VersionRunner = run_owned,
        readiness_seconds: float = DEFAULT_READINESS_SECONDS,
        runtime: RustToolchainRuntime | None = None,
    ) -> None:
        try:
            self.analyzer = analyzer.resolve(strict=True)
        except OSError as exc:
            raise RustAnalyzerError("rust-analyzer is unavailable") from exc
        self.repository = repository.resolve()
        self.project = project
        self.generation = dict(generation)
        self.version_runner = version_runner
        self.arguments = tuple(arguments)
        self.runtime = runtime
        if runtime is not None and (
            arguments or self.analyzer != runtime.analyzer.path.absolute()
        ):
            raise RustAnalyzerError("Rust analyzer differs from verified runtime")
        if not 0 < readiness_seconds <= DEFAULT_READINESS_SECONDS:
            raise ValueError("Rust analyzer readiness timeout must be between 0 and 60 seconds")
        self.readiness_seconds = readiness_seconds
        if generation.get("repository") != str(self.repository):
            raise RustAnalyzerError("Rust analyzer generation repository mismatch")
        if generation.get("project") != project or generation.get("language") != "rust":
            raise RustAnalyzerError("Rust analyzer generation identity mismatch")
        try:
            self.scope = validate_rust_source_scope(generation.get("source_scope"))
            self.build_context = validate_rust_build_context(
                generation.get("build_context")
            )
        except RustScopeError as exc:
            raise RustAnalyzerError(str(exc)) from exc
        self._files = {
            entry["path"]: entry
            for entry in generation.get("files", [])
            if isinstance(entry, dict) and isinstance(entry.get("path"), str)
        }
        if set(self._files) != set(self.scope["source_paths"]):
            raise RustAnalyzerError("Rust analyzer generation file inventory mismatch")
        self._cargo_cfgs = self._package_feature_cfgs()
        generation_id = generation.get("generation_id")
        if not isinstance(generation_id, str) or not generation_id:
            raise RustAnalyzerError("Rust analyzer generation id is invalid")
        self._process: subprocess.Popen[bytes] | None = None
        self._messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr = bytearray()
        self._request_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._next_id = 1
        self._opened: dict[str, str] = {}
        self._semantic_ready = False
        self._cleanup_deadline: float | None = None

    def _package_feature_cfgs(self) -> list[str]:
        """Derive all workspace feature cfgs from generation-bound manifests."""
        features: set[str] = set()
        for entry in self.scope["manifests"]:
            path = self.repository / entry["path"]
            try:
                payload = path.read_bytes()
                document = tomllib.loads(payload.decode("utf-8"))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
                raise RustAnalyzerError("Rust Cargo manifest is unavailable") from exc
            if hashlib.sha256(payload).hexdigest() != entry["content_sha256"]:
                raise RustAnalyzerError("Rust Cargo manifest differs from the generation")
            raw = document.get("features", {})
            if not isinstance(raw, dict):
                raise RustAnalyzerError("Rust Cargo features table is invalid")
            if not all(isinstance(name, str) and name for name in raw):
                raise RustAnalyzerError("Rust Cargo feature name is invalid")
            features.update(raw)
        return [f"feature={name}" for name in sorted(features)]

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def stderr_text(self) -> str:
        return bytes(self._stderr).decode("utf-8", "replace")

    def _environment(self) -> dict[str, str]:
        if self.runtime is not None:
            return self.runtime.environment(self.repository)
        # Legacy internal qualification harness only. Normal Rust service
        # construction must supply a receipt-verified runtime.
        environment = os.environ.copy()
        environment.update({
            "CARGO_NET_OFFLINE": "true",
            "RUST_BACKTRACE": "0",
        })
        return environment

    def _drain_stdout(self, stream: BinaryIO) -> None:
        try:
            while True:
                self._messages.put(("message", _read_lsp_frame(stream)))
        except BaseException as exc:
            self._messages.put(("error", exc))

    def _drain_stderr(self, stream: BinaryIO) -> None:
        try:
            for chunk in iter(lambda: stream.read(8192), b""):
                remaining = MAX_STDERR_BYTES - len(self._stderr)
                if remaining > 0:
                    self._stderr.extend(chunk[:remaining])
        except (OSError, ValueError):
            return

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise RustAnalyzerError("rust-analyzer is not running")
        _write_lsp_frame(process.stdin, {
            "jsonrpc": "2.0", "method": method, "params": params or {},
        })

    def _reply_to_server(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            return
        method = message.get("method")
        result: Any = None
        if method == "workspace/configuration":
            items = message.get("params", {}).get("items", [])
            result = [{} for _ in items] if isinstance(items, list) else []
        _write_lsp_frame(process.stdin, {
            "jsonrpc": "2.0", "id": message.get("id"), "result": result,
        })

    @contextmanager
    def _request_ownership(self, timeout_seconds: float):
        deadline = monotonic() + timeout_seconds
        if not self._request_lock.acquire(timeout=max(0.0, deadline - monotonic())):
            self._terminate()
            raise TimeoutError("rust-analyzer request admission timed out")
        try:
            yield deadline
        finally:
            self._request_lock.release()

    def _request(
        self, method: str, params: dict[str, Any], timeout_seconds: float
    ) -> Any:
        with self._request_ownership(timeout_seconds) as deadline:
            process = self._process
            if process is None or process.stdin is None or process.poll() is not None:
                raise RustAnalyzerError("rust-analyzer is not running")
            request_id = self._next_id
            self._next_id += 1
            _write_lsp_frame(process.stdin, {
                "jsonrpc": "2.0", "id": request_id,
                "method": method, "params": params,
            })
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    self._terminate()
                    raise TimeoutError(f"rust-analyzer request timed out: {method}")
                try:
                    kind, raw = self._messages.get(timeout=remaining)
                except queue.Empty as exc:
                    self._terminate()
                    raise TimeoutError(
                        f"rust-analyzer request timed out: {method}"
                    ) from exc
                if kind == "error":
                    self._terminate()
                    raise RustAnalyzerError(f"rust-analyzer transport failed: {raw}")
                message = raw
                if not isinstance(message, dict):
                    continue
                if "method" in message and "id" in message:
                    self._reply_to_server(message)
                    continue
                if message.get("id") != request_id:
                    continue
                error = message.get("error")
                if isinstance(error, dict):
                    code = error.get("code")
                    raise RustAnalyzerResponseError(
                        code if isinstance(code, int) else None,
                        str(error.get("message", "unknown error")),
                    )
                return message.get("result")

    def start(self, *, timeout_seconds: float = 30.0) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Rust analyzer startup timeout must be finite and positive")
        deadline = monotonic() + timeout_seconds
        with self._startup_lock(deadline, maximum_wait=timeout_seconds):
            if self.running:
                return
            try:
                metadata = os.lstat(self.analyzer)
            except OSError as exc:
                raise RustAnalyzerError("rust-analyzer is unavailable") from exc
            if not stat.S_ISREG(metadata.st_mode):
                raise RustAnalyzerError("rust-analyzer binary is unsafe")
            try:
                environment = self._environment()
                version = self.version_runner(
                    [str(self.analyzer), *self.arguments, "--version"],
                    check=False,
                    capture_output=True,
                    text=True,
                    cwd=self.repository,
                    timeout=min(5.0, self._remaining_startup(deadline)),
                    env=environment,
                )
            except subprocess.TimeoutExpired as exc:
                raise TimeoutError("rust-analyzer version probe timed out") from exc
            if version.returncode != 0 or not version.stdout.startswith(
                f"rust-analyzer {PROVIDER_VERSION} "
            ):
                raise RustAnalyzerError("rust-analyzer version mismatch")
            environment = self._environment()
            self._remaining_startup(deadline)
            if os.name == "nt":
                from ..windows_owned_process import WindowsOwnedProcess
                process = WindowsOwnedProcess([str(self.analyzer), *self.arguments],
                                               cwd=self.repository, env=environment)
            else:
                process = subprocess.Popen(
                    [str(self.analyzer), *self.arguments], cwd=self.repository,
                    env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, bufsize=0, start_new_session=True)
            if process.stdin is None or process.stdout is None or process.stderr is None:
                process.kill()
                raise RustAnalyzerError("rust-analyzer lacks stdio pipes")
            self._process = process
            self._messages = queue.Queue()
            self._stderr = bytearray()
            self._opened = {}
            self._semantic_ready = False
            self._reader_thread = threading.Thread(
                target=self._drain_stdout, args=(process.stdout,), daemon=True,
                name="atlas-rust-analyzer-stdout",
            )
            self._stderr_thread = threading.Thread(
                target=self._drain_stderr, args=(process.stderr,), daemon=True,
                name="atlas-rust-analyzer-stderr",
            )
            self._reader_thread.start()
            self._stderr_thread.start()
            try:
                result = self._request("initialize", {
                    "processId": os.getpid(),
                    "clientInfo": {"name": "codebase-atlas", "version": "stage1-s4"},
                    "rootUri": self.repository.as_uri(),
                    "capabilities": {
                        "workspace": {"configuration": False},
                        "textDocument": {
                            "definition": {"linkSupport": True},
                            "references": {},
                        },
                    },
                    "initializationOptions": {
                        "cargo": {
                            "allTargets": False,
                            "buildScripts": {"enable": False},
                            "cfgs": self._cargo_cfgs,
                            "features": self.build_context["cargo_features"],
                            "noDeps": self.build_context["cargo_no_deps"],
                            # Pinned analyzer filters general extraArgs for
                            # metadata; its dedicated option must carry this.
                            "metadataExtraArgs": ["--offline"],
                        },
                        "procMacro": {"enable": False},
                        "cachePriming": {"enable": False},
                        "checkOnSave": False,
                    },
                }, self._remaining_startup(deadline))
                if not isinstance(result, dict) or not isinstance(
                    result.get("capabilities"), dict
                ):
                    raise RustAnalyzerError("rust-analyzer initialize result is invalid")
                self._notify("initialized")
                self._wait_ready(deadline=deadline)
            except BaseException:
                self._terminate()
                raise

    @staticmethod
    def _remaining_startup(deadline: float) -> float:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("rust-analyzer startup budget exceeded")
        return remaining

    @contextmanager
    def _startup_lock(self, deadline: float, *, maximum_wait: float):
        # Absolute-deadline subtraction can round slightly above the original
        # duration. Never enlarge even a very small caller's lock-wait budget.
        remaining = min(maximum_wait, self._remaining_startup(deadline))
        if not self._state_lock.acquire(timeout=remaining):
            raise TimeoutError("rust-analyzer startup lock timed out")
        try:
            yield
        finally:
            self._state_lock.release()

    def _wait_ready(self, *, deadline: float | None = None) -> None:
        readiness_deadline = monotonic() + self.readiness_seconds
        deadline = min(deadline, readiness_deadline) if deadline is not None else readiness_deadline
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("rust-analyzer workspace readiness timed out")
            status = self._request(
                "rust-analyzer/analyzerStatus", {}, min(2.0, remaining)
            )
            if (
                isinstance(status, str)
                and "Workspaces:" in status
                and "Loaded " in status
                and "No workspaces" not in status
            ):
                graph = self._request(
                    "rust-analyzer/viewCrateGraph",
                    {"full": False},
                    min(2.0, self._remaining_startup(deadline)),
                )
                if isinstance(graph, str):
                    body = graph.partition("{")[2].rpartition("}")[0].strip()
                    if body:
                        return
            sleep(min(0.1, max(0.0, deadline - monotonic())))

    def _assert_fresh(self) -> None:
        snapshot = repository_snapshot(self.repository)
        if (
            snapshot.kind != "git"
            or snapshot.fingerprint != self.generation.get("source_fingerprint")
            or snapshot.head != self.generation.get("source_head")
        ):
            raise RustAnalyzerError("Rust generation is stale")

    def _open(self, relative: str) -> str:
        relative = repository_path(relative)
        if relative not in self._files:
            raise RustAnalyzerError("Rust query path is outside the generation scope")
        path = self.repository / relative
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise RustAnalyzerError("Rust query source is unavailable") from exc
        digest = hashlib.sha256(payload).hexdigest()
        if digest != self._files[relative].get("content_sha256"):
            raise RustAnalyzerError("Rust query source differs from the generation")
        try:
            source = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RustAnalyzerError("Rust query source is not UTF-8") from exc
        if self._opened.get(relative) != digest:
            self._notify("textDocument/didOpen", {
                "textDocument": {
                    "uri": path.resolve().as_uri(),
                    "languageId": "rust",
                    "version": 1,
                    "text": source,
                }
            })
            self._opened[relative] = digest
        return source

    def _relative_uri(self, value: Any) -> str:
        if not isinstance(value, str):
            raise RustAnalyzerError("rust-analyzer result URI is invalid")
        parsed = urlparse(value)
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            raise RustAnalyzerError("rust-analyzer result URI is outside the repository")
        try:
            # file:///C:/... is not a native Windows path until URI conversion.
            candidate = Path(url2pathname(parsed.path)).resolve(strict=True)
        except (OSError, ValueError) as exc:
            raise RustAnalyzerError("rust-analyzer result path is unavailable") from exc
        try:
            relative = candidate.relative_to(self.repository).as_posix()
        except ValueError as exc:
            raise RustAnalyzerError(
                "rust-analyzer result path is outside the repository"
            ) from exc
        relative = repository_path(relative)
        if relative not in self._files:
            raise RustAnalyzerError(
                f"rust-analyzer result path is outside generation scope: {relative}"
            )
        return relative

    def _nodes(self, result: Any, query_type: str, symbol: str) -> tuple[Node, ...]:
        if result is None:
            return ()
        values = result if isinstance(result, list) else [result]
        nodes: list[Node] = []
        completeness = (
            "complete_exact"
            if self.scope["status"] == "complete_exact"
            else "exact_hits_partial_scope"
        )
        repository_identity = hashlib.sha256(
            str(self.repository).encode("utf-8")
        ).hexdigest()
        for raw in values:
            if not isinstance(raw, dict):
                raise RustAnalyzerError("rust-analyzer result location is invalid")
            uri = raw.get("targetUri", raw.get("uri"))
            raw_range = raw.get("targetSelectionRange", raw.get("range"))
            if not isinstance(raw_range, dict):
                raise RustAnalyzerError("rust-analyzer result range is invalid")
            start, end = raw_range.get("start"), raw_range.get("end")
            if not isinstance(start, dict) or not isinstance(end, dict):
                raise RustAnalyzerError("rust-analyzer result position is invalid")
            coordinates = (
                start.get("line"), start.get("character"),
                end.get("line"), end.get("character"),
            )
            if not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in coordinates):
                raise RustAnalyzerError("rust-analyzer result position is invalid")
            path = self._relative_uri(uri)
            source = self._open(path)
            lines = source.splitlines()
            if source.endswith("\n"):
                lines.append("")
            start_column = self._public_column(lines, coordinates[0], coordinates[1])
            end_column = self._public_column(lines, coordinates[2], coordinates[3])
            if (coordinates[2], end_column) < (coordinates[0], start_column):
                raise RustAnalyzerError("rust-analyzer result range is reversed")
            evidence = {
                "query_type": query_type,
                "symbol": symbol,
                "path": path,
                "range": raw_range,
                "generation_id": self.generation["generation_id"],
            }
            evidence_hash = _hash(evidence)
            nodes.append(Node(
                id=(
                    f"rust-ra:{self.generation['generation_id']}:{query_type}:"
                    f"{path}:{coordinates[0] + 1}:{start_column}:"
                    f"{evidence_hash[:16]}"
                ),
                kind="reference" if query_type == "references" else "definition",
                name=symbol,
                location=SourceRange(
                    path,
                    coordinates[0] + 1,
                    coordinates[2] + 1,
                    start_column,
                    end_column,
                ),
                provider=PROVIDER_NAME,
                confidence=1.0,
                evidence_hash=evidence_hash,
                attributes={"resolution": "semantic_exact", "fact_tier": "T2"},
                provenance=EvidenceProvenance(
                    repository_identity=repository_identity,
                    generation_id=self.generation["generation_id"],
                    fact_tier="T2",
                    provider=PROVIDER_NAME,
                    provider_version=PROVIDER_VERSION,
                    completeness=completeness,
                ),
            ))
        return tuple(nodes)

    @staticmethod
    def _public_column(lines: list[str], line: int, utf16_column: int) -> int:
        if line >= len(lines):
            raise RustAnalyzerError("rust-analyzer result position is outside the file")
        units = 0
        for offset, character in enumerate(lines[line]):
            if units == utf16_column:
                return offset + 1
            units += len(character.encode("utf-16-le")) // 2
            if units > utf16_column:
                raise RustAnalyzerError("rust-analyzer result splits a UTF-16 character")
        if units == utf16_column:
            return len(lines[line]) + 1
        raise RustAnalyzerError("rust-analyzer result position is outside the file")

    def query(
        self,
        query_type: str,
        symbol: str,
        *,
        source_path: str,
        source_line: int,
        source_column: int,
        timeout_ms: int = 30_000,
    ) -> tuple[Node, ...]:
        if query_type not in {"definition", "references"}:
            raise ValueError(f"unsupported rust-analyzer query: {query_type}")
        if not symbol:
            raise ValueError("Rust analyzer query symbol is required")
        if (
            not isinstance(source_line, int) or isinstance(source_line, bool)
            or not isinstance(source_column, int) or isinstance(source_column, bool)
            or source_line < 1 or source_column < 1
        ):
            raise ValueError("Rust analyzer source position must be positive")
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or not 1 <= timeout_ms <= 300_000:
            raise ValueError("timeout_ms must be between 1 and 300000")
        deadline = monotonic() + min(timeout_ms / 1000.0, self.readiness_seconds)
        self._assert_fresh()
        source = self._open(source_path)
        lines = source.splitlines()
        if source_line > len(lines) or source_column > len(lines[source_line - 1]) + 1:
            raise ValueError("Rust analyzer source position is outside the file")
        method = (
            "textDocument/definition"
            if query_type == "definition"
            else "textDocument/references"
        )
        params: dict[str, Any] = {
            "textDocument": {
                "uri": (self.repository / repository_path(source_path)).resolve().as_uri()
            },
            "position": {
                "line": source_line - 1,
                "character": len(lines[source_line - 1][:source_column - 1].encode("utf-16-le")) // 2,
            },
        }
        if query_type == "references":
            params["context"] = {"includeDeclaration": True}
        delay = 0.05
        saw_empty = False
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                if saw_empty:
                    self._semantic_ready = True
                    return ()
                self._terminate()
                raise TimeoutError("rust-analyzer readiness retry timed out")
            try:
                nodes = self._nodes(
                    self._request(method, params, remaining), query_type, symbol
                )
                if nodes or self._semantic_ready:
                    self._semantic_ready = True
                    return nodes
                # Workspace loading can briefly answer semantic queries with an
                # empty array even after initialize/analyzerStatus reports a
                # workspace.  Only the first semantic query receives this
                # bounded stabilization retry; later empty results are final.
                saw_empty = True
                sleep(min(delay, max(0.0, deadline - monotonic())))
                delay = min(delay * 2.0, 1.0)
            except RustAnalyzerResponseError as exc:
                retryable = exc.code == -32801 or "preload_file_not_found" in exc.message
                if not retryable:
                    raise
                sleep(min(delay, max(0.0, deadline - monotonic())))
                delay = min(delay * 2.0, 1.0)

    def _terminate(self) -> None:
        deadline = self._cleanup_deadline or (monotonic() + CLEANUP_GRACE_SECONDS)
        def budget(maximum: float) -> float:
            return min(maximum, max(0.0, deadline - monotonic()))
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                process.terminate()
            try:
                process.wait(timeout=budget(3))
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                try:
                    process.wait(timeout=budget(3))
                except subprocess.TimeoutExpired:
                    # Do not silently claim cleanup success. Close pipes and
                    # release reader ownership before reporting an unreaped PID.
                    cleanup_failed = True
                else:
                    cleanup_failed = False
            else:
                cleanup_failed = False
        else:
            cleanup_failed = False
        # The parent can exit before its workers. Its owned POSIX session still
        # needs cleanup even when poll()/wait() already reported parent exit.
        if process is not None and os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        job_error = None
        if process is not None and hasattr(process, "close_owned_job"):
            try:
                process.close_owned_job(budget(CLEANUP_GRACE_SECONDS))
            except (OSError, TimeoutError) as exc:
                job_error = exc
        for stream in (
            process.stdin if process is not None else None,
            process.stdout if process is not None else None,
            process.stderr if process is not None else None,
        ):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        for thread in (self._reader_thread, self._stderr_thread):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=budget(1))
        self._reader_thread = None
        self._stderr_thread = None
        self._opened = {}
        self._semantic_ready = False
        if cleanup_failed:
            raise RustAnalyzerError("rust-analyzer did not exit within cleanup grace")
        if job_error is not None:
            raise RustAnalyzerError("rust-analyzer Windows Job cleanup failed") from job_error

    def close(self) -> None:
        process = self._process
        if process is None:
            return
        self._cleanup_deadline = monotonic() + CLEANUP_GRACE_SECONDS
        try:
            if process.poll() is None:
                self._request("shutdown", {}, 1.0)
                self._notify("exit")
                process.wait(timeout=min(1.0, max(0.0, self._cleanup_deadline - monotonic())))
        except (OSError, RustAnalyzerError, TimeoutError, subprocess.TimeoutExpired):
            pass
        finally:
            try:
                self._terminate()
            finally:
                self._cleanup_deadline = None

    def __enter__(self) -> "RustAnalyzerProvider":
        self.start()
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()
