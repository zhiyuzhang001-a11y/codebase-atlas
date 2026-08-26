"""Small, authenticated loopback UI for the shared Atlas query service."""

from __future__ import annotations

from dataclasses import asdict
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import secrets
import threading
from typing import Any
from urllib.parse import urlsplit

from .operations import attach_operational_status, stale_policy_error
from .service import AtlasService, QueryRequest


MAX_REQUEST_BYTES = 64 * 1024
MAX_SYMBOL_LENGTH = 1024
MAX_TEXT_PARAMETER_LENGTH = 4096
ASSETS = {
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


def _payload(response, index_status: dict[str, Any], stale_policy: str) -> dict[str, Any]:
    return attach_operational_status({
        "schema_version": 1,
        "query_type": response.query_type,
        "nodes": [asdict(node) for node in response.nodes],
        "edges": [asdict(edge) for edge in response.edges],
        "depths": response.depths,
        "paths": {
            node_id: [asdict(edge) for edge in path]
            for node_id, path in response.paths.items()
        },
        "truncated": response.truncated,
        "truncation": response.truncation,
    }, index_status, stale_policy)


class LocalUiServer:
    """Own one loopback HTTP server and serialize access to one AtlasService."""

    def __init__(
        self,
        service: AtlasService,
        *,
        repository: str,
        language: str,
        index_status: dict[str, Any],
        stale_policy: str = "warn",
        port: int = 0,
    ) -> None:
        self.service = service
        self.repository = repository
        self.language = language
        self.index_status = index_status
        self.stale_policy = stale_policy
        self.token = secrets.token_urlsafe(32)
        self._query_lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "CodebaseAtlasUI/1"
            sys_version = ""

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def _host_ok(self) -> bool:
                return self.headers.get("Host", "") == outer.authority

            def _token_ok(self) -> bool:
                supplied = self.headers.get("X-Atlas-Token", "")
                return secrets.compare_digest(supplied, outer.token)

            def _origin_ok(self) -> bool:
                return self.headers.get("Origin", "") == outer.origin

            def _headers(self, content_type: str, length: int) -> None:
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")

            def _send(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self._headers(content_type, len(body))
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status: int, value: dict[str, Any]) -> None:
                self._send(
                    status,
                    json.dumps(value, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                )

            def _reject_host(self) -> bool:
                if self._host_ok():
                    return False
                self._json(HTTPStatus.FORBIDDEN, {"status": "error", "message": "invalid_host"})
                return True

            def do_GET(self) -> None:
                if self._reject_host():
                    return
                parsed = urlsplit(self.path)
                if parsed.path == "/":
                    body = outer.index_html().encode("utf-8")
                    self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")
                    return
                if parsed.path == "/api/status":
                    if not self._token_ok():
                        self._json(HTTPStatus.FORBIDDEN, {"status": "error", "message": "invalid_token"})
                        return
                    self._json(HTTPStatus.OK, outer.status_payload())
                    return
                asset = ASSETS.get(parsed.path)
                if asset is not None:
                    name, content_type = asset
                    body = files("codebase_atlas.ui_assets").joinpath(name).read_bytes()
                    self._send(HTTPStatus.OK, body, content_type)
                    return
                self._json(HTTPStatus.NOT_FOUND, {"status": "error", "message": "not_found"})

            def do_POST(self) -> None:
                if self._reject_host():
                    return
                if urlsplit(self.path).path != "/api/query":
                    self._json(HTTPStatus.NOT_FOUND, {"status": "error", "message": "not_found"})
                    return
                if not self._token_ok() or not self._origin_ok():
                    self._json(HTTPStatus.FORBIDDEN, {"status": "error", "message": "request_not_authorized"})
                    return
                if self.headers.get_content_type() != "application/json":
                    self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"status": "error", "message": "json_required"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = -1
                if length < 1 or length > MAX_REQUEST_BYTES:
                    self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"status": "error", "message": "invalid_request_size"})
                    return
                try:
                    value = json.loads(self.rfile.read(length))
                    request = outer.query_request(value)
                    policy_error = stale_policy_error(outer.index_status, outer.stale_policy)
                    if policy_error:
                        raise ValueError(policy_error)
                    with outer._query_lock:
                        response = outer.service.query(request)
                    self._json(HTTPStatus.OK, _payload(response, outer.index_status, outer.stale_policy))
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"status": "error", "message": str(exc)})
                except Exception:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"status": "error", "message": "query_failed"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.httpd.daemon_threads = True

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    @property
    def authority(self) -> str:
        return f"127.0.0.1:{self.port}"

    @property
    def origin(self) -> str:
        return f"http://{self.authority}"

    @property
    def url(self) -> str:
        return f"{self.origin}/"

    def status_payload(self) -> dict[str, Any]:
        return attach_operational_status({
            "status": "ready",
            "repository": self.repository,
            "language": self.language,
            "capabilities": ["definition", "references", "callers", "callees", "related_tests", "impact"],
        }, self.index_status, self.stale_policy)

    def index_html(self) -> str:
        template = files("codebase_atlas.ui_assets").joinpath("index.html").read_text(encoding="utf-8")
        return template.replace("{{TOKEN}}", escape(self.token, quote=True))

    def query_request(self, value: Any) -> QueryRequest:
        if not isinstance(value, dict) or set(value) - {"query_type", "symbol", "parameters"}:
            raise ValueError("invalid request fields")
        query_type = value.get("query_type")
        symbol = value.get("symbol")
        parameters = value.get("parameters", {})
        if not isinstance(query_type, str) or not isinstance(symbol, str) or not isinstance(parameters, dict):
            raise ValueError("invalid request types")
        if len(symbol) > MAX_SYMBOL_LENGTH:
            raise ValueError("symbol is too long")
        allowed = {"target_path", "target_owner", "relation", "direction", "depth", "max_nodes", "max_edges", "timeout_ms", "continuation"}
        if set(parameters) - allowed:
            raise ValueError("invalid parameter fields")
        for name in ("target_path", "target_owner", "relation", "direction", "continuation"):
            if name in parameters and (not isinstance(parameters[name], str) or len(parameters[name]) > MAX_TEXT_PARAMETER_LENGTH):
                raise ValueError(f"invalid {name}")
        return QueryRequest(query_type, symbol, parameters)

    def serve_forever(self) -> None:
        self.httpd.serve_forever(poll_interval=0.1)

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
