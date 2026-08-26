"""Atlas-owned, offline and lifecycle-bounded gopls product provider."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import threading
from time import monotonic
from typing import Any
from urllib.parse import unquote, urlparse


CONTRACT_VERSION = "m27-go-v1"
PROVIDER = "gopls-0.23.0"
DEFAULT_TIMEOUT_SECONDS = 30.0
DECLARATION = re.compile(
    r"^\s*func\s+(?:\((?P<receiver>[^)]*)\)\s*)?(?P<function>[A-Za-z_]\w*)"
    r"|^\s*type\s+(?P<type>[A-Za-z_]\w*)\s*(?P<alias>=)?"
)
PACKAGE = re.compile(r"^\s*package\s+([A-Za-z_]\w*)")
MODULE = re.compile(r"^\s*module\s+(\S+)")
TEST_FUNCTION = re.compile(
    r"^\s*func\s+(?P<name>(?:Test|Benchmark|Fuzz|Example)[A-Za-z0-9_]*)\s*\("
)


class GoAdapterError(RuntimeError):
    """A stable contract failure."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code
        self.detail = detail or code


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest(value: object) -> str:
    payload = value if isinstance(value, bytes) else canonical_json(value)
    return hashlib.sha256(payload).hexdigest()


def contained(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise GoAdapterError("target_out_of_scope", str(candidate))
    return resolved


def path_from_uri(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise GoAdapterError("provider_protocol_error", f"unsupported URI: {uri}")
    return Path(unquote(parsed.path))


def range_start(value: dict[str, Any]) -> tuple[int, int]:
    start = value["start"]
    return int(start["line"]) + 1, int(start["character"]) + 1


def source_fingerprint(repository: Path) -> str:
    """Hash Git identity cheaply, or all relevant source for a standalone copy."""

    repository = repository.resolve()
    try:
        top = Path(subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "--show-toplevel"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()).resolve()
    except (subprocess.CalledProcessError, FileNotFoundError):
        top = Path()
    if top == repository and (repository / ".git").exists():
        head = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        index = subprocess.run(
            ["git", "-C", str(repository), "ls-files", "-s", "--", "*.go",
             "go.mod", "go.sum", "go.work", "go.work.sum", "vendor/modules.txt"],
            check=True, capture_output=True, text=True,
        ).stdout
        status = subprocess.run(
            ["git", "-C", str(repository), "status", "--porcelain=v1",
             "--untracked-files=all", "--", "*.go", "go.mod", "go.sum",
             "go.work", "go.work.sum", "vendor/modules.txt"],
            check=True, capture_output=True, text=True,
        ).stdout
        changed: list[tuple[str, str]] = []
        for row in status.splitlines():
            raw = row[3:].split(" -> ")[-1]
            path = repository / raw
            if path.is_file():
                changed.append((raw, hashlib.sha256(path.read_bytes()).hexdigest()))
        return digest({"head": head, "index": index, "status": status, "changed": changed})

    relevant = []
    for path in repository.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        relative = path.relative_to(repository)
        if (
            path.suffix == ".go"
            or path.name in {"go.mod", "go.sum", "go.work", "go.work.sum"}
            or str(relative) == "vendor/modules.txt"
        ):
            relevant.append((str(relative), hashlib.sha256(path.read_bytes()).hexdigest()))
    return digest(sorted(relevant))


def parse_declaration(line_text: str, fallback_kind: str = "") -> dict[str, str]:
    match = DECLARATION.match(line_text)
    if match is None:
        return {
            "symbol": "", "owner": "", "receiver_mode": "",
            "kind": fallback_kind or "symbol", "signature": line_text.strip(),
        }
    receiver = (match.group("receiver") or "").strip()
    owner = ""
    receiver_mode = ""
    if receiver:
        receiver_type = receiver.split()[-1]
        receiver_mode = "pointer" if receiver_type.startswith("*") else "value"
        owner = receiver_type.lstrip("*").split("[")[0]
    symbol = match.group("function") or match.group("type") or ""
    if match.group("function"):
        kind = "method" if receiver else "function"
    elif match.group("alias"):
        kind = "type_alias"
    else:
        kind = "type"
    return {
        "symbol": symbol, "owner": owner, "receiver_mode": receiver_mode,
        "kind": kind, "signature": line_text.strip(),
    }


def declaration_metadata(path: Path, line: int, fallback_kind: str = "") -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if line < 1 or line > len(lines):
        raise GoAdapterError("provider_protocol_error", f"bad declaration line: {path}:{line}")
    return parse_declaration(lines[line - 1], fallback_kind)


def package_name(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        match = PACKAGE.match(line)
        if match:
            return match.group(1)
    raise GoAdapterError("go_build_context_incomplete", f"package missing: {path}")


def nearest_module(path: Path, repository: Path) -> tuple[Path, str]:
    current = path.resolve().parent
    repository = repository.resolve()
    while current == repository or repository in current.parents:
        go_mod = current / "go.mod"
        if go_mod.is_file():
            for line in go_mod.read_text(encoding="utf-8").splitlines():
                match = MODULE.match(line)
                if match:
                    return current, match.group(1)
            raise GoAdapterError("go_build_context_incomplete", f"module missing: {go_mod}")
        if current == repository:
            break
        current = current.parent
    raise GoAdapterError("go_build_context_incomplete", f"no module for {path}")


def generated_file(path: Path) -> bool:
    return any(
        line.startswith("// Code generated ") and line.rstrip().endswith("DO NOT EDIT.")
        for line in path.read_text(encoding="utf-8").splitlines()[:20]
    )


def select_workspace(
    repository: Path, *, target_path: str = "", explicit_root: str = "",
) -> Path:
    """Apply the frozen deterministic module/workspace selection rules."""

    repository = repository.resolve()
    if explicit_root:
        selected = contained(repository, repository / explicit_root)
        if not (selected / "go.work").is_file() and not (selected / "go.mod").is_file():
            raise GoAdapterError("go_build_context_incomplete", str(selected))
        return selected
    if target_path:
        target = contained(repository, repository / target_path)
        module_root, _ = nearest_module(target, repository)
        return module_root
    workspaces = sorted(
        path.parent for path in repository.rglob("go.work")
        if ".git" not in path.parts and "vendor" not in path.parts
    )
    if len(workspaces) == 1:
        return workspaces[0]
    if len(workspaces) > 1:
        raise GoAdapterError("go_workspace_ambiguous", "multiple go.work files")
    modules = sorted(
        path.parent for path in repository.rglob("go.mod")
        if ".git" not in path.parts and "vendor" not in path.parts
    )
    if len(modules) == 1:
        return modules[0]
    if len(modules) > 1:
        raise GoAdapterError("go_workspace_ambiguous", "multiple go.mod files")
    raise GoAdapterError("go_build_context_incomplete", "no go.work or go.mod")


class LspClient:
    """Small synchronous JSON-RPC client with a reader thread and owned process."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        settings: dict[str, Any],
        start_timeout: float,
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.env = env
        self.settings = settings
        self.start_timeout = start_timeout
        self.process: subprocess.Popen[bytes] | None = None
        self._next_id = 1
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self.stderr: deque[str] = deque(maxlen=400)
        self.unhealthy = False
        self.reader_error: GoAdapterError | None = None
        self.started_at = 0.0
        self.ready_at = 0.0
        self.request_elapsed_ms = 0.0

    def start(self) -> None:
        if self.process is not None:
            return
        self.started_at = monotonic()
        try:
            self.process = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                env=self.env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=os.name != "nt",
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                ),
            )
        except FileNotFoundError as exc:
            raise GoAdapterError("gopls_unavailable", str(exc)) from exc
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._stderr_reader = threading.Thread(target=self._stderr_loop, daemon=True)
        self._reader.start()
        self._stderr_reader.start()
        result = self.request("initialize", {
            "processId": os.getpid(),
            "clientInfo": {"name": "codebase-atlas", "version": "m27-go-v1"},
            "rootUri": self.cwd.as_uri(),
            "workspaceFolders": [{"uri": self.cwd.as_uri(), "name": self.cwd.name}],
            "capabilities": {
                "workspace": {"configuration": True, "workspaceFolders": True},
                "textDocument": {
                    "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                    "callHierarchy": {"dynamicRegistration": False},
                },
                "window": {"workDoneProgress": False},
            },
            "initializationOptions": {"settings": self.settings},
            "trace": "off",
        }, timeout=self.start_timeout, timeout_code="provider_start_timeout")
        if not isinstance(result, dict) or "capabilities" not in result:
            raise GoAdapterError("provider_protocol_error", "invalid initialize result")
        self.notify("initialized", {})
        self.ready_at = monotonic()

    def _stderr_loop(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        for raw in iter(self.process.stderr.readline, b""):
            self.stderr.append(raw.decode("utf-8", errors="replace").rstrip())

    def _read_message(self) -> dict[str, Any] | None:
        assert self.process is not None and self.process.stdout is not None
        headers: dict[str, str] = {}
        while True:
            line = self.process.stdout.readline()
            if not line:
                return None
            if line in {b"\r\n", b"\n"}:
                break
            decoded = line.decode("ascii", errors="strict").strip()
            if ":" not in decoded:
                raise GoAdapterError("provider_protocol_error", decoded)
            name, value = decoded.split(":", 1)
            headers[name.lower()] = value.strip()
        length = int(headers.get("content-length", "-1"))
        if length < 0 or length > 64 * 1024 * 1024:
            raise GoAdapterError("provider_protocol_error", "invalid Content-Length")
        payload = self.process.stdout.read(length)
        if len(payload) != length:
            raise GoAdapterError("provider_protocol_error", "short JSON-RPC body")
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise GoAdapterError("provider_protocol_error", "non-object JSON-RPC message")
        return value

    def _read_loop(self) -> None:
        try:
            while True:
                message = self._read_message()
                if message is None:
                    break
                if "method" in message and "id" in message:
                    self._handle_server_request(message)
                elif "id" in message:
                    with self._lock:
                        pending = self._pending.get(int(message["id"]))
                    if pending is not None:
                        pending.put(message)
        except Exception as exc:  # reader failure is translated by waiting request
            self.stderr.append(f"adapter-reader: {type(exc).__name__}: {exc}")
            self.reader_error = (
                exc if isinstance(exc, GoAdapterError)
                else GoAdapterError("provider_protocol_error", str(exc))
            )
            self.unhealthy = True
        finally:
            self.unhealthy = True
            with self._lock:
                pending = list(self._pending.values())
            for waiter in pending:
                waiter.put({
                    "error": {"code": -32099, "message": "provider exited"},
                    "adapter_code": self.reader_error.code if self.reader_error else "provider_crashed",
                    "adapter_detail": self.reader_error.detail if self.reader_error else "provider exited",
                })

    def _handle_server_request(self, message: dict[str, Any]) -> None:
        method = str(message["method"])
        if method == "workspace/configuration":
            items = message.get("params", {}).get("items", [])
            result = [self.settings if item.get("section") in {None, "", "gopls"} else None for item in items]
        elif method == "workspace/workspaceFolders":
            result = [{"uri": self.cwd.as_uri(), "name": self.cwd.name}]
        else:
            result = None
        self._send({"jsonrpc": "2.0", "id": message["id"], "result": result})

    def _send(self, message: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.poll() is not None:
            raise GoAdapterError("provider_crashed", "gopls is not running")
        payload = canonical_json(message)
        framed = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload
        with self._write_lock:
            try:
                process.stdin.write(framed)
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self.unhealthy = True
                raise GoAdapterError("provider_crashed", str(exc)) from exc

    def notify(self, method: str, params: object) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(
        self,
        method: str,
        params: object,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        timeout_code: str = "provider_query_timeout",
    ) -> Any:
        request_started = monotonic()
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            waiter: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[request_id] = waiter
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        try:
            response = waiter.get(timeout=timeout)
        except queue.Empty as exc:
            try:
                self.notify("$/cancelRequest", {"id": request_id})
            except GoAdapterError as cancel_exc:
                self.unhealthy = True
                raise GoAdapterError("provider_cancel_failed", cancel_exc.detail) from cancel_exc
            self.unhealthy = True
            raise GoAdapterError(timeout_code, method) from exc
        finally:
            self.request_elapsed_ms += (monotonic() - request_started) * 1000
            with self._lock:
                self._pending.pop(request_id, None)
        if "error" in response:
            if "adapter_code" in response:
                raise GoAdapterError(
                    str(response["adapter_code"]), str(response.get("adapter_detail", ""))
                )
            error = response["error"]
            message = str(error.get("message", "provider error"))
            if message == "provider exited":
                raise GoAdapterError("provider_crashed", message)
            raise GoAdapterError("provider_protocol_error", message)
        return response.get("result")

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        try:
            if process.poll() is None and not self.unhealthy:
                try:
                    self.request("shutdown", None, timeout=2.0)
                    self.notify("exit", None)
                    process.wait(timeout=2.0)
                except (GoAdapterError, subprocess.TimeoutExpired):
                    pass
            if process.poll() is None:
                if os.name == "nt":
                    process.terminate()
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        process.kill()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=1.0)
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            self.process = None


@dataclass(frozen=True)
class Target:
    path: str
    line: int
    column: int
    symbol: str
    owner: str
    kind: str
    raw_symbol: dict[str, Any]


class _GoAdapter:
    """Normalize a single workspace/build identity through one owned gopls."""

    def __init__(
        self,
        *,
        repository: Path,
        workspace_root: Path,
        data_root: Path,
        go: Path,
        gopls: Path,
        goos: str = "darwin",
        goarch: str = "arm64",
        cgo_enabled: str = "0",
        build_tags: tuple[str, ...] = (),
        packages_driver: str = "",
        start_timeout: float = 20.0,
        command: list[str] | None = None,
    ) -> None:
        self.repository = repository.resolve()
        self.workspace_root = contained(self.repository, workspace_root)
        self.data_root = data_root.resolve()
        self.go = go.resolve()
        self.gopls = gopls.resolve()
        self.goos = goos
        self.goarch = goarch
        self.cgo_enabled = cgo_enabled
        self.build_tags = tuple(sorted(build_tags))
        self.module_mode = (
            "vendor" if (self.workspace_root / "vendor" / "modules.txt").is_file()
            else "readonly"
        )
        self.packages_driver = packages_driver
        self.start_timeout = start_timeout
        self.command = command or [str(self.gopls), "serve"]
        self.client: LspClient | None = None
        self.go_version = ""
        self.gopls_version = ""
        self.build_context_fingerprint = ""
        self.initial_source_fingerprint = ""
        self.env: dict[str, str] = {}
        self._dependency_checked: set[Path] = set()
        self.dependency_elapsed_ms = 0.0
        self._lines_cache: dict[Path, tuple[str, ...]] = {}
        self._package_cache: dict[Path, str] = {}
        self._module_cache: dict[Path, tuple[Path, str]] = {}
        self._file_hash_cache: dict[Path, str] = {}
        self._function_ranges_cache: dict[Path, tuple[dict[str, Any], ...]] = {}

    def _environment(self) -> dict[str, str]:
        for directory in (
            self.data_root / "home", self.data_root / "tmp",
            self.data_root / "gomodcache", self.data_root / "gocache",
            self.data_root / "gopath", self.data_root / "logs",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        go_work = self.workspace_root / "go.work"
        return {
            "PATH": f"{self.go.parent}:{self.gopls.parent}:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(self.data_root / "home"),
            "TMPDIR": str(self.data_root / "tmp"),
            "GOMODCACHE": str(self.data_root / "gomodcache"),
            "GOCACHE": str(self.data_root / "gocache"),
            "GOPATH": str(self.data_root / "gopath"),
            "GOTOOLCHAIN": "local",
            "GOPROXY": "off",
            "GOSUMDB": "off",
            "GOTELEMETRY": "off",
            "GOENV": "off",
            "GOOS": self.goos,
            "GOARCH": self.goarch,
            "CGO_ENABLED": self.cgo_enabled,
            "GOWORK": str(go_work) if go_work.is_file() else "off",
        }

    def _preflight(self, env: dict[str, str]) -> None:
        if not self.go.is_file():
            raise GoAdapterError("go_toolchain_unavailable", str(self.go))
        if not self.gopls.is_file() and self.command == [str(self.gopls), "serve"]:
            raise GoAdapterError("gopls_unavailable", str(self.gopls))
        go_result = subprocess.run(
            [str(self.go), "version"], env=env, capture_output=True, text=True,
            timeout=5, check=False,
        )
        if go_result.returncode != 0:
            raise GoAdapterError("go_toolchain_unavailable", go_result.stderr[-500:])
        self.go_version = go_result.stdout.strip()
        if self.command == [str(self.gopls), "serve"]:
            result = subprocess.run(
                [str(self.gopls), "version"], env=env, capture_output=True,
                text=True, timeout=5, check=False,
            )
            if result.returncode != 0:
                raise GoAdapterError("gopls_unavailable", result.stderr[-500:])
            self.gopls_version = result.stdout.strip()
            if not self.gopls_version.split() or self.gopls_version.split()[-1] != "v0.23.0":
                raise GoAdapterError("gopls_version_unsupported", self.gopls_version)
        else:
            self.gopls_version = "fault-injection"

    def start(self) -> None:
        if self.packages_driver:
            raise GoAdapterError("go_workspace_unsupported", "GOPACKAGESDRIVER")
        env = self._environment()
        self.env = env
        self._preflight(env)
        self.initial_source_fingerprint = source_fingerprint(self.repository)
        build_flags = [f"-mod={self.module_mode}"]
        if self.build_tags:
            build_flags.append(f"-tags={','.join(self.build_tags)}")
        settings = {
            "buildFlags": build_flags,
            "env": {
                "GOOS": self.goos, "GOARCH": self.goarch,
                "CGO_ENABLED": self.cgo_enabled, "GOTOOLCHAIN": "local",
                "GOPROXY": "off", "GOSUMDB": "off", "GOTELEMETRY": "off",
                "GOMODCACHE": env["GOMODCACHE"], "GOCACHE": env["GOCACHE"],
                "GOPATH": env["GOPATH"], "GOWORK": env["GOWORK"],
            },
            "directoryFilters": ["-**/node_modules", "-**/vendor"],
        }
        self.build_context_fingerprint = digest({
            "contract": CONTRACT_VERSION,
            "source": self.initial_source_fingerprint,
            "workspace": str(self.workspace_root.relative_to(self.repository)),
            "go": self.go_version,
            "gopls": self.gopls_version,
            "goos": self.goos, "goarch": self.goarch,
            "cgo": self.cgo_enabled, "tags": self.build_tags,
            "module_mode": self.module_mode,
            "settings": {
                "buildFlags": settings["buildFlags"],
                "env": {
                    name: value for name, value in settings["env"].items()
                    if name not in {"GOMODCACHE", "GOCACHE", "GOPATH"}
                },
                "directoryFilters": settings["directoryFilters"],
                "cache_policy": "atlas-data-root-contained",
            },
        })
        self.client = LspClient(
            self.command, cwd=self.workspace_root, env=env, settings=settings,
            start_timeout=self.start_timeout,
        )
        try:
            self.client.start()
        except Exception:
            self.client.close()
            raise

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
        self.client = None

    def __enter__(self) -> "_GoAdapter":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _require_client(self) -> LspClient:
        if self.client is None:
            raise GoAdapterError("provider_crashed", "adapter not started")
        return self.client

    def _document_uri(self, relative: str) -> tuple[Path, str]:
        path = contained(self.repository, self.repository / relative)
        if "vendor" in path.relative_to(self.repository).parts:
            raise GoAdapterError("target_out_of_scope", relative)
        return path, path.as_uri()

    def _lines(self, path: Path) -> tuple[str, ...]:
        resolved = path.resolve()
        lines = self._lines_cache.get(resolved)
        if lines is None:
            lines = tuple(resolved.read_text(encoding="utf-8").splitlines())
            self._lines_cache[resolved] = lines
        return lines

    def _metadata(self, path: Path, line: int, fallback_kind: str = "") -> dict[str, str]:
        lines = self._lines(path)
        if line < 1 or line > len(lines):
            raise GoAdapterError("provider_protocol_error", f"bad declaration line: {path}:{line}")
        return parse_declaration(lines[line - 1], fallback_kind)

    def _package(self, path: Path) -> str:
        directory = path.resolve().parent
        cached = self._package_cache.get(directory)
        if cached is not None:
            return cached
        for line in self._lines(path):
            match = PACKAGE.match(line)
            if match:
                self._package_cache[directory] = match.group(1)
                return match.group(1)
        raise GoAdapterError("go_build_context_incomplete", f"package missing: {path}")

    def _module(self, path: Path) -> tuple[Path, str]:
        directory = path.resolve().parent
        for cached_directory, value in self._module_cache.items():
            if directory == cached_directory or cached_directory in directory.parents:
                return value
        value = nearest_module(path, self.repository)
        self._module_cache[value[0]] = value
        return value

    def _file_hash(self, path: Path) -> str:
        resolved = path.resolve()
        cached = self._file_hash_cache.get(resolved)
        if cached is None:
            cached = hashlib.sha256(resolved.read_bytes()).hexdigest()
            self._file_hash_cache[resolved] = cached
        return cached

    def _check_dependencies(self, path: Path) -> None:
        package_dir = path.parent.resolve()
        if package_dir in self._dependency_checked:
            return
        started = monotonic()
        try:
            completed = subprocess.run(
                [str(self.go), "list", "-e", "-json", f"-mod={self.module_mode}", "."],
                cwd=package_dir, env=self.env, capture_output=True, text=True,
                timeout=30, check=False,
            )
        finally:
            self.dependency_elapsed_ms += (monotonic() - started) * 1000
        package: dict[str, Any] = {}
        try:
            package = json.loads(completed.stdout) if completed.stdout else {}
        except json.JSONDecodeError:
            pass
        if completed.returncode != 0 or package.get("Error") or package.get("DepsErrors"):
            detail = "\n".join((completed.stderr or completed.stdout).splitlines()[-10:])
            raise GoAdapterError("go_dependencies_unavailable", detail)
        self._dependency_checked.add(package_dir)

    def _open(self, path: Path) -> None:
        client = self._require_client()
        client.notify("textDocument/didOpen", {
            "textDocument": {
                "uri": path.as_uri(), "languageId": "go", "version": 1,
                "text": path.read_text(encoding="utf-8"),
            }
        })

    @staticmethod
    def _flatten_symbols(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in items:
            result.append(item)
            children = item.get("children", [])
            if isinstance(children, list):
                result.extend(_GoAdapter._flatten_symbols(children))
        return result

    def resolve_target(self, request: dict[str, Any], timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Target:
        path, uri = self._document_uri(str(request.get("path", "")))
        self._check_dependencies(path)
        self._open(path)
        raw = self._require_client().request(
            "textDocument/documentSymbol", {"textDocument": {"uri": uri}}, timeout=timeout,
        )
        if not isinstance(raw, list):
            raise GoAdapterError("provider_protocol_error", "documentSymbol is not a list")
        candidates = []
        symbol = str(request.get("symbol", ""))
        owner = str(request.get("owner", ""))
        requested_line = int(request.get("line", 0) or 0)
        for item in self._flatten_symbols(raw):
            provider_name = str(item.get("name", ""))
            leaf_name = provider_name.rsplit(".", 1)[-1]
            if leaf_name != symbol:
                continue
            selection = item.get("selectionRange") or item.get("range")
            if not isinstance(selection, dict):
                continue
            line, column = range_start(selection)
            metadata = self._metadata(path, line)
            if requested_line and line != requested_line:
                continue
            if owner and metadata["owner"] != owner:
                continue
            candidates.append((item, line, column, metadata))
        if not candidates:
            raise GoAdapterError("target_not_found", f"{request.get('path')}:{symbol}")
        if len(candidates) != 1:
            owners = sorted({item[3]["owner"] for item in candidates})
            raise GoAdapterError("target_ambiguous", json.dumps(owners))
        item, line, column, metadata = candidates[0]
        return Target(
            path=str(path.relative_to(self.repository)), line=line, column=column,
            symbol=symbol, owner=metadata["owner"], kind=metadata["kind"], raw_symbol=item,
        )

    def _relative_location(self, uri: str, value_range: dict[str, Any]) -> dict[str, Any] | None:
        path = path_from_uri(uri).resolve()
        if path != self.repository and self.repository not in path.parents:
            return None
        relative = path.relative_to(self.repository)
        if "vendor" in relative.parts:
            return None
        line, column = range_start(value_range)
        end = value_range["end"]
        return {
            "path": str(relative), "line": line, "column": column,
            "end_line": int(end["line"]) + 1, "end_column": int(end["character"]) + 1,
        }

    def _declaration_node(
        self, path: str, line: int, column: int, *, symbol: str = "", owner: str = "",
    ) -> dict[str, Any]:
        absolute = contained(self.repository, self.repository / path)
        metadata = self._metadata(absolute, line)
        symbol = symbol or metadata["symbol"]
        owner = owner or metadata["owner"]
        module_root, module_path = self._module(absolute)
        package = self._package(absolute)
        relative_package = absolute.parent.relative_to(module_root)
        import_path = module_path if str(relative_package) == "." else f"{module_path}/{relative_package}"
        identity = {
            "contract_version": CONTRACT_VERSION,
            "module_path": module_path,
            "package_import_path": import_path,
            "package_name": package,
            "symbol_kind": metadata["kind"],
            "owner_named_origin": owner,
            "declared_receiver_mode": metadata["receiver_mode"],
            "symbol_name": symbol,
            "repository_relative_declaration_path": path,
            "declaration_range": [line, column],
            "generic_origin_signature": metadata["signature"],
            "build_context_fingerprint": self.build_context_fingerprint,
        }
        return {
            "id": f"go:v1:{digest(identity)}",
            "kind": metadata["kind"], "name": symbol,
            "location": {"path": path, "line": line, "column": column},
            "provider": PROVIDER, "confidence": 1.0,
            "evidence_hash": digest({"identity": identity, "source_sha256": self._file_hash(absolute)}),
            "attributes": {
                **identity,
                "generated": any(
                    item.startswith("// Code generated ") and item.rstrip().endswith("DO NOT EDIT.")
                    for item in self._lines(absolute)[:20]
                ),
            },
        }

    def _item_node(self, item: dict[str, Any]) -> dict[str, Any] | None:
        selection = item.get("selectionRange") or item.get("range")
        location = self._relative_location(str(item.get("uri", "")), selection)
        if location is None:
            return None
        provider_name = str(item.get("name", ""))
        return self._declaration_node(
            location["path"], location["line"], location["column"],
            symbol=provider_name.rsplit(".", 1)[-1],
        )

    def _reference_node(self, target: dict[str, Any], location: dict[str, Any]) -> dict[str, Any]:
        absolute = self.repository / location["path"]
        owner = self._enclosing_function(absolute, int(location["line"]))
        identity = {
            "contract_version": CONTRACT_VERSION,
            "symbol_kind": "reference", "symbol_name": target["name"],
            "owner_named_origin": owner["name"] if owner else "",
            "repository_relative_declaration_path": location["path"],
            "declaration_range": [location["line"], location["column"]],
            "target_id": target["id"],
            "build_context_fingerprint": self.build_context_fingerprint,
        }
        target_signature = str(target.get("attributes", {}).get("generic_origin_signature", ""))
        return {
            "id": f"go:v1:{digest(identity)}", "kind": "reference",
            "name": target["name"], "location": location,
            "provider": PROVIDER, "confidence": 1.0,
            "evidence_hash": digest(identity),
            "attributes": {
                **identity,
                "generic_origin": "[" in target_signature,
                "instantiation_is_declaration": False,
            },
        }

    def _definition_locations(
        self, uri: str, site_range: dict[str, Any], timeout: float,
    ) -> set[tuple[str, int]]:
        start = site_range["start"]
        raw = self._require_client().request(
            "textDocument/definition",
            {
                "textDocument": {"uri": uri},
                "position": {"line": int(start["line"]), "character": int(start["character"])},
            },
            timeout=timeout,
        )
        if raw is None:
            return set()
        items = raw if isinstance(raw, list) else [raw]
        locations: set[tuple[str, int]] = set()
        for item in items:
            target_uri = item.get("targetUri") or item.get("uri")
            target_range = item.get("targetSelectionRange") or item.get("targetRange") or item.get("range")
            if not isinstance(target_uri, str) or not isinstance(target_range, dict):
                continue
            location = self._relative_location(target_uri, target_range)
            if location is not None:
                locations.add((location["path"], int(location["line"])))
        return locations

    def _incoming_call_is_exact(
        self, call: dict[str, Any], target: Target, timeout: float,
    ) -> bool:
        caller = call.get("from", {})
        uri = caller.get("uri")
        ranges = call.get("fromRanges", [])
        if not isinstance(uri, str) or not isinstance(ranges, list):
            raise GoAdapterError("provider_protocol_error", "invalid incoming call")
        expected = (target.path, target.line)
        return any(
            self._site_is_direct_call(uri, item)
            and expected in self._definition_locations(uri, item, timeout)
            for item in ranges
        )

    def _site_is_direct_call(self, uri: str, site_range: dict[str, Any]) -> bool:
        path = path_from_uri(uri).resolve()
        if path != self.repository and self.repository not in path.parents:
            return False
        start = site_range["start"]
        end = site_range["end"]
        if int(start["line"]) != int(end["line"]):
            return False
        line = self._lines(path)[int(end["line"])]
        tail = line[int(end["character"]):].lstrip()
        if tail.startswith("("):
            return True
        if not tail.startswith("["):
            return False
        depth = 0
        for index, char in enumerate(tail):
            if char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    return tail[index + 1:].lstrip().startswith("(")
        return False

    def _has_non_call_reference(
        self, target: Target, exact_call_sites: set[tuple[str, int]], timeout: float,
    ) -> bool:
        _, uri = self._document_uri(target.path)
        raw = self._require_client().request(
            "textDocument/references",
            {
                "textDocument": {"uri": uri},
                "position": {"line": target.line - 1, "character": target.column - 1},
                "context": {"includeDeclaration": False},
            },
            timeout=timeout,
        )
        if not isinstance(raw, list):
            raise GoAdapterError("provider_protocol_error", "references is not a list")
        reference_sites = set()
        for item in raw:
            location = self._relative_location(str(item["uri"]), item["range"])
            if location is not None:
                reference_sites.add((location["path"], int(location["line"])))
        return bool(reference_sites - exact_call_sites)

    def _enclosing_function(self, path: Path, site_line: int) -> dict[str, Any] | None:
        resolved = path.resolve()
        ranges = self._function_ranges_cache.get(resolved)
        if ranges is None:
            lines = self._lines(resolved)
            built: list[dict[str, Any]] = []
            for start, line in enumerate(lines, 1):
                match = DECLARATION.match(line)
                if not match or not match.group("function"):
                    continue
                depth = 0
                seen = False
                end = len(lines)
                for number in range(start, len(lines) + 1):
                    for char in lines[number - 1]:
                        if char == "{":
                            depth += 1
                            seen = True
                        elif char == "}":
                            depth -= 1
                    if seen and depth == 0:
                        end = number
                        break
                built.append({"name": match.group("function"), "line": start, "end_line": end})
            ranges = tuple(built)
            self._function_ranges_cache[resolved] = ranges
        candidates = [item for item in ranges if item["line"] <= site_line <= item["end_line"]]
        return max(candidates, key=lambda item: item["line"]) if candidates else None

    def _test_node(self, reference: dict[str, Any], target: dict[str, Any]) -> dict[str, Any] | None:
        path = self.repository / reference["path"]
        enclosing = self._enclosing_function(path, int(reference["line"]))
        if enclosing is None or TEST_FUNCTION.match(
            self._lines(path)[enclosing["line"] - 1]
        ) is None:
            return None
        node = self._declaration_node(
            reference["path"], int(enclosing["line"]), 6,
            symbol=str(enclosing["name"]),
        )
        node["kind"] = "test"
        node["attributes"]["symbol_kind"] = "test"
        node["attributes"]["target_id"] = target["id"]
        return node

    def _prepare_item(self, target: Target, timeout: float) -> dict[str, Any]:
        _, uri = self._document_uri(target.path)
        raw = self._require_client().request(
            "textDocument/prepareCallHierarchy",
            {"textDocument": {"uri": uri}, "position": {"line": target.line - 1, "character": target.column - 1}},
            timeout=timeout,
        )
        if not isinstance(raw, list) or len(raw) != 1:
            raise GoAdapterError("provider_protocol_error", "call hierarchy target unavailable")
        return raw[0]

    def _interface_partial(self, target: Target, timeout: float) -> bool:
        if target.kind != "method":
            return False
        _, uri = self._document_uri(target.path)
        result = self._require_client().request(
            "textDocument/implementation",
            {"textDocument": {"uri": uri}, "position": {"line": target.line - 1, "character": target.column - 1}},
            timeout=timeout,
        )
        return isinstance(result, list) and bool(result)

    def query(self, case: dict[str, Any]) -> dict[str, Any]:
        started = monotonic()
        client = self._require_client()
        provider_before_ms = client.request_elapsed_ms
        dependency_before_ms = self.dependency_elapsed_ms
        source_started = monotonic()
        before = source_fingerprint(self.repository)
        source_guard_ms = (monotonic() - source_started) * 1000
        if before != self.initial_source_fingerprint:
            raise GoAdapterError("source_changed", "source differs from session identity")
        parameters = case.get("parameters", {})
        requested_context = parameters.get("build_context_fingerprint")
        if requested_context is not None and requested_context != self.build_context_fingerprint:
            raise GoAdapterError("build_context_changed", "new session identity required")
        timeout_ms = int(parameters.get("timeout_ms", 30_000))
        timeout = timeout_ms / 1000.0
        target = self.resolve_target(case["target"], timeout=timeout)
        target_node = self._declaration_node(
            target.path, target.line, target.column, symbol=target.symbol, owner=target.owner,
        )
        query_type = str(case["query_type"])
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        if query_type == "definition":
            nodes = [target_node]
        elif query_type in {"references", "related_tests"}:
            _, uri = self._document_uri(target.path)
            raw = self._require_client().request(
                "textDocument/references",
                {
                    "textDocument": {"uri": uri},
                    "position": {"line": target.line - 1, "character": target.column - 1},
                    "context": {"includeDeclaration": False},
                },
                timeout=timeout,
            )
            if not isinstance(raw, list):
                raise GoAdapterError("provider_protocol_error", "references is not a list")
            locations = []
            external = 0
            for item in raw:
                location = self._relative_location(str(item["uri"]), item["range"])
                if location is None:
                    external += 1
                elif not (location["path"] == target.path and location["line"] == target.line):
                    locations.append(location)
            if external:
                warnings.append({"code": "external_results_omitted", "count": external})
            if query_type == "references":
                nodes = [self._reference_node(target_node, item) for item in locations]
            else:
                tests = [self._test_node(item, target_node) for item in locations if item["path"].endswith("_test.go")]
                nodes = [item for item in tests if item is not None]
                for node in nodes:
                    edges.append(self._edge(node, target_node, "tests"))
        elif query_type in {"callers", "callees", "impact"}:
            root_item = self._prepare_item(target, timeout)
            if query_type == "impact":
                nodes, edges, traversal_warnings = self._impact(
                    target_node, root_item, parameters, timeout,
                )
                warnings.extend(traversal_warnings)
            else:
                method = "callHierarchy/incomingCalls" if query_type == "callers" else "callHierarchy/outgoingCalls"
                raw = self._require_client().request(method, {"item": root_item}, timeout=timeout)
                if not isinstance(raw, list):
                    raise GoAdapterError("provider_protocol_error", f"{method} is not a list")
                external = 0
                exact_call_sites: set[tuple[str, int]] = set()
                for call in raw:
                    if query_type == "callers" and not self._incoming_call_is_exact(call, target, timeout):
                        continue
                    if query_type == "callers":
                        caller_uri = str(call["from"]["uri"])
                        for site_range in call.get("fromRanges", []):
                            site = self._relative_location(caller_uri, site_range)
                            if site is not None:
                                exact_call_sites.add((site["path"], int(site["line"])))
                    item = call["from"] if query_type == "callers" else call["to"]
                    node = self._item_node(item)
                    if node is None:
                        external += 1
                        continue
                    nodes.append(node)
                    edges.append(
                        self._edge(node, target_node, "calls")
                        if query_type == "callers" else self._edge(target_node, node, "calls")
                    )
                if external:
                    warnings.append({"code": "external_results_omitted", "count": external})
            if query_type == "callers":
                interface_partial = self._interface_partial(target, timeout)
                if interface_partial:
                    warnings.append({"code": "interface_dispatch_partial", "count": 1})
                elif target.kind == "function" and self._has_non_call_reference(
                    target, exact_call_sites, timeout
                ):
                    warnings.append({"code": "dynamic_dispatch_partial", "count": 1})
        else:
            raise GoAdapterError("provider_protocol_error", f"unknown query: {query_type}")

        nodes = self._dedupe_nodes(nodes)
        edges = self._dedupe_edges(edges)
        observed_nodes = len(nodes)
        observed_edges = len(edges)
        max_nodes = int(parameters.get("max_nodes", 100))
        max_edges = int(parameters.get("max_edges", 200))
        if observed_nodes > max_nodes:
            warnings.append({"code": "node_budget_exceeded", "count": observed_nodes - max_nodes})
            nodes = nodes[:max_nodes]
            allowed = {node["id"] for node in nodes} | {target_node["id"]}
            edges = [edge for edge in edges if edge["source_id"] in allowed and edge["target_id"] in allowed]
        if len(edges) > max_edges:
            warnings.append({"code": "edge_budget_exceeded", "count": len(edges) - max_edges})
            edges = edges[:max_edges]
        source_started = monotonic()
        after = source_fingerprint(self.repository)
        source_guard_ms += (monotonic() - source_started) * 1000
        if before != after:
            raise GoAdapterError("source_changed", "source changed during query")
        partial = bool(warnings)
        elapsed_ms = (monotonic() - started) * 1000
        provider_wait_ms = client.request_elapsed_ms - provider_before_ms
        dependency_ms = self.dependency_elapsed_ms - dependency_before_ms
        normalization_ms = max(
            0.0, elapsed_ms - provider_wait_ms - dependency_ms - source_guard_ms
        )
        return {
            "status": "partial" if partial else "ok",
            "capability": "static_partial" if partial else "complete",
            "warnings": sorted(warnings, key=lambda item: (item["code"], item.get("count", 0))),
            "provider": PROVIDER,
            "adapter_contract": CONTRACT_VERSION,
            "build_context_fingerprint": self.build_context_fingerprint,
            "source_fingerprint": before,
            "nodes": nodes,
            "edges": edges,
            "truncation": {
                "reasons": sorted({item["code"] for item in warnings if item["code"].endswith("_exceeded")}),
                "observed": {"nodes": observed_nodes, "edges": observed_edges},
                "returned": {"nodes": len(nodes), "edges": len(edges)},
            },
            "elapsed_ms": round(elapsed_ms, 3),
            "provider_wait_ms": round(provider_wait_ms, 3),
            "adapter_overhead_ms": round(max(0.0, elapsed_ms - provider_wait_ms), 3),
            "source_guard_ms": round(source_guard_ms, 3),
            "dependency_check_ms": round(dependency_ms, 3),
            "normalization_ms": round(normalization_ms, 3),
        }

    @staticmethod
    def _dedupe_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted({item["id"]: item for item in nodes}.values(), key=lambda item: (
            item["location"]["path"], item["location"]["line"],
            item["location"].get("column", 0), item["id"],
        ))

    @staticmethod
    def _dedupe_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted({(
            item["source_id"], item["target_id"], item["relation"]
        ): item for item in edges}.values(), key=lambda item: (
            item["source_id"], item["target_id"], item["relation"],
        ))

    @staticmethod
    def _edge(source: dict[str, Any], target: dict[str, Any], relation: str) -> dict[str, Any]:
        identity = {"source": source["id"], "target": target["id"], "relation": relation}
        return {
            "source_id": source["id"], "target_id": target["id"],
            "relation": relation, "provider": PROVIDER, "confidence": 1.0,
            "resolution": "exact", "evidence_hash": digest(identity),
            "attributes": {},
        }

    def _impact(
        self,
        root_node: dict[str, Any],
        root_item: dict[str, Any],
        parameters: dict[str, Any],
        timeout: float,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        direction = str(parameters.get("direction", "upstream"))
        depth_limit = int(parameters.get("depth", 1))
        method = "callHierarchy/incomingCalls" if direction == "upstream" else "callHierarchy/outgoingCalls"
        frontier = [(root_item, root_node, 0)]
        visited = {root_node["id"]}
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        deadline = monotonic() + timeout
        while frontier:
            item, parent_node, depth = frontier.pop(0)
            if depth >= depth_limit:
                continue
            remaining = deadline - monotonic()
            if remaining <= 0:
                warnings.append({"code": "time_budget_exceeded", "count": 1})
                break
            try:
                raw = self._require_client().request(method, {"item": item}, timeout=remaining)
            except GoAdapterError as exc:
                if exc.code == "provider_query_timeout":
                    warnings.append({"code": "time_budget_exceeded", "count": 1})
                    break
                raise
            if not isinstance(raw, list):
                raise GoAdapterError("provider_protocol_error", f"{method} is not a list")
            for call in raw:
                child_item = call["from"] if direction == "upstream" else call["to"]
                child = self._item_node(child_item)
                if child is None:
                    warnings.append({"code": "external_results_omitted", "count": 1})
                    continue
                edge = self._edge(child, parent_node, "calls") if direction == "upstream" else self._edge(parent_node, child, "calls")
                edges.append(edge)
                if child["id"] not in visited:
                    visited.add(child["id"])
                    nodes.append(child)
                    frontier.append((child_item, child, depth + 1))
        return nodes, edges, warnings


def error_response(exc: GoAdapterError) -> dict[str, Any]:
    status = {
        "target_ambiguous": "ambiguous",
        "target_out_of_scope": "unsupported",
        "go_workspace_unsupported": "unsupported",
        "source_changed": "stale",
        "build_context_changed": "stale",
    }.get(exc.code, "unavailable" if exc.code.endswith("_unavailable") else "error")
    return {
        "status": status, "capability": "unsupported",
        "warnings": [{"code": exc.code, "count": 1}],
        "provider": PROVIDER, "adapter_contract": CONTRACT_VERSION,
        "nodes": [], "edges": [], "truncation": {"reasons": []},
    }


class GoSemanticProvider:
    """Product-facing adapter over the frozen M27 Go contract."""

    name = PROVIDER

    def __init__(
        self,
        repository: Path,
        data_root: Path,
        go: Path,
        gopls: Path,
        workspace_root: Path,
    ) -> None:
        self.repository = repository.resolve()
        self._adapter = _GoAdapter(
            repository=self.repository,
            workspace_root=workspace_root,
            data_root=data_root,
            go=go,
            gopls=gopls,
        )

    @property
    def build_context_fingerprint(self) -> str:
        return self._adapter.build_context_fingerprint

    def start(self, timeout_seconds: float = 30.0) -> None:
        self._adapter.start_timeout = timeout_seconds
        self._adapter.start()

    def close(self) -> None:
        self._adapter.close()

    def query_product(
        self,
        query_type: str,
        symbol: str,
        *,
        target_path: str = "",
        target_owner: str = "",
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not target_path:
            raise GoAdapterError(
                "target_path_required",
                "Go queries require --target-path to preserve exact package/receiver identity",
            )
        values = dict(parameters or {})
        return self._adapter.query({
            "query_type": query_type,
            "target": {
                "path": target_path,
                "symbol": symbol,
                "owner": target_owner,
            },
            "parameters": values,
        })
