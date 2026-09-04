"""Minimal MCP stdio interface over AtlasService and explicit refresh."""

from __future__ import annotations

from dataclasses import asdict
import json
import sys
from contextlib import nullcontext
from typing import Any, TextIO

from . import __version__
from .change_analysis import CHANGE_INTENTS, analyze_change
from .operations import (
    attach_operational_status,
    stale_policy_error,
)
from .refresh_coordinator import refresh_with_retry
from .service import AtlasService, QueryRequest, QueryResponse
from .version_check import VersionNotifier


PROTOCOL_VERSION = "2025-11-25"
LOCATE_FILES_NEXT_ACTION = (
    "Read the returned files, search within them, then use exact symbol lookup "
    "and impact analysis before editing."
)


def _structured(response: QueryResponse) -> dict[str, Any]:
    return {
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
    }


def _tool_result(
    response: QueryResponse,
    index_status: dict[str, Any] | None = None,
    stale_policy: str = "ignore",
) -> dict[str, Any]:
    structured = attach_operational_status(
        _structured(response), index_status, stale_policy
    )
    if index_status is not None and index_status.get("generation_id"):
        structured["generation_id"] = index_status["generation_id"]
    return {
        "content": [{"type": "text", "text": json.dumps(structured, ensure_ascii=False)}],
        "structuredContent": structured,
        "isError": False,
    }


def _brief_tool_result(brief: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(brief, ensure_ascii=False)}],
        "structuredContent": brief,
        "isError": brief.get("status") == "error",
    }


def _locate_payload(
    *,
    status: str,
    repository: str,
    freshness: dict[str, Any],
    files: list[dict[str, Any]] | None = None,
    matched_terms: list[str] | None = None,
    budget: dict[str, int] | None = None,
    message: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": status,
        "repository": repository,
        "freshness": freshness,
        "heuristic": True,
        "non_exhaustive": True,
        "files": files or [],
        "matched_terms": matched_terms or [],
        "budget": budget or {
            "provider_queries": 0,
            "max_internal_rows": 60,
            "max_files": 2,
        },
        "required_next_action": LOCATE_FILES_NEXT_ACTION,
    }
    if message:
        payload["message"] = message
    return payload


def _locate_tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "structuredContent": payload,
        "isError": payload["status"] in {"stale", "error"},
    }


TOOLS = [
    {
        "name": "project_status",
        "title": "Check the active Atlas project",
        "description": (
            "Return the resolved repository, project identity, index freshness, "
            "session-start update result, and notify-only software version status."
        ),
        "inputSchema": {
            "type": "object", "properties": {}, "additionalProperties": False
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "plan_refresh",
        "title": "Plan an exact index refresh",
        "description": "Read-only exact dirty-set plan for this active repository.",
        "inputSchema": {
            "type": "object", "properties": {}, "additionalProperties": False
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "refresh_index",
        "title": "Refresh the active Atlas generation",
        "description": (
            "Refresh this exact repository through the Provider connection already "
            "owned by the active MCP, then publish one validated generation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string", "enum": ["fast", "moderate", "full"], "default": "fast"
                },
                "timeout_ms": {
                    "type": "integer", "minimum": 1, "maximum": 300000, "default": 300000
                },
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "locate_files",
        "title": "Locate likely implementation files",
        "description": (
            "Return at most two repository-relative files likely to contain a free-form "
            "implementation intent. This is heuristic and non-exhaustive; read and search "
            "the returned files, then use exact symbol lookup and impact analysis before editing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "minLength": 1, "maxLength": 1000},
                "max_files": {"type": "integer", "minimum": 1, "maximum": 2, "default": 2},
                "max_internal_rows": {
                    "type": "integer", "minimum": 1, "maximum": 60, "default": 60,
                },
                "timeout_ms": {
                    "type": "integer", "minimum": 1, "maximum": 300000, "default": 30000,
                },
            },
            "required": ["intent"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    *[
        {
            "name": name,
            "title": title,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "target_path": {
                        "type": "string",
                        "description": "Repository-relative path of the intended declaration.",
                    },
                    "target_owner": {
                        "type": "string",
                        "description": "Enclosing class or object name for a same-file member.",
                    },
                    "max_nodes": {"type": "integer", "minimum": 1, "maximum": 10000},
                    "max_edges": {"type": "integer", "minimum": 1, "maximum": 20000},
                    "timeout_ms": {"type": "integer", "minimum": 1, "maximum": 300000},
                },
                "required": ["symbol"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "destructiveHint": False},
        }
        for name, title, description in (
            ("definition", "Find definitions", "Return exact-name symbol definitions."),
            ("references", "Find references", "Return exact semantic reference occurrences."),
            ("callers", "Find callers", "Return direct callers by stable identity."),
            ("callees", "Find callees", "Return direct callees by stable identity."),
        )
    ],
    {
        "name": "related_tests",
        "title": "Find exact related tests",
        "description": "Return exact TS/JS test callbacks that call a declaration.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "target_path": {"type": "string"},
                "target_owner": {"type": "string"},
                "max_nodes": {"type": "integer", "minimum": 1, "maximum": 10000},
                "max_edges": {"type": "integer", "minimum": 1, "maximum": 20000},
                "timeout_ms": {"type": "integer", "minimum": 1, "maximum": 300000},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "impact",
        "title": "Trace exact impact",
        "description": "Traverse exact call edges by stable identity to an explicit depth.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "direction": {"type": "string", "enum": ["upstream", "downstream"]},
                "depth": {"type": "integer", "minimum": 1, "maximum": 10},
                "target_path": {
                    "type": "string",
                    "description": "Repository-relative path of the intended declaration.",
                },
                "target_owner": {
                    "type": "string",
                    "description": "Enclosing class or object name for a same-file member.",
                },
                "max_nodes": {"type": "integer", "minimum": 1, "maximum": 10000},
                "max_edges": {"type": "integer", "minimum": 1, "maximum": 20000},
                "timeout_ms": {"type": "integer", "minimum": 1, "maximum": 300000},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "analyze_change",
        "title": "Build an exact change brief",
        "description": (
            "Resolve one exact target and return bounded implementation, relationship, "
            "impact, test, freshness, provenance, and completeness evidence."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "intent": {"type": "string", "enum": list(CHANGE_INTENTS)},
                "target_path": {"type": "string"},
                "target_owner": {"type": "string"},
                "direction": {"type": "string", "enum": ["upstream", "downstream"]},
                "depth": {"type": "integer", "minimum": 1, "maximum": 10},
                "max_nodes": {"type": "integer", "minimum": 1, "maximum": 10000},
                "max_edges": {"type": "integer", "minimum": 1, "maximum": 20000},
                "timeout_ms": {"type": "integer", "minimum": 1, "maximum": 300000},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
]

for _tool in TOOLS:
    if _tool["name"] in {"callers", "callees"}:
        _tool["inputSchema"]["properties"]["relation"] = {
            "type": "string",
            "enum": ["registers"],
            "description": "Restrict the answer to exact Python registration edges.",
        }
    if _tool["name"] == "references":
        _tool["inputSchema"]["properties"]["continuation"] = {
            "type": "string",
            "minLength": 1,
            "maxLength": 512,
            "description": "Opaque next-page token from this MCP session.",
        }


class McpServer:
    def __init__(
        self,
        service: AtlasService | None,
        index_status: dict[str, Any] | None = None,
        stale_policy: str = "ignore",
        instructions: str = "",
        version_notifier: VersionNotifier | None = None,
        refresh_coordinator: Any | None = None,
    ) -> None:
        self.service = service
        self.index_status = index_status
        self.stale_policy = stale_policy
        self.instructions = instructions
        self.version_notifier = version_notifier
        self.refresh_coordinator = refresh_coordinator

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        request_id = message.get("id")
        method = message.get("method")
        if method == "notifications/initialized":
            return None
        if method == "initialize":
            if self.version_notifier is not None:
                self.version_notifier.start()
            requested = message.get("params", {}).get("protocolVersion")
            protocol = requested if requested == PROTOCOL_VERSION else PROTOCOL_VERSION
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": protocol,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": "codebase-atlas",
                        "title": "Codebase Atlas",
                        "version": __version__,
                        "description": "Read-only local code intelligence with exact provenance",
                    },
                    "instructions": self.instructions,
                },
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
        if method != "tools/call":
            return self._error(request_id, -32601, f"Method not found: {method}")
        params = message.get("params", {})
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            return self._error(request_id, -32602, "Tool arguments must be an object")
        try:
            if self.version_notifier is not None and self.index_status is not None:
                self.index_status["software_update"] = self.version_notifier.current()
            if name == "project_status":
                status_context = (
                    self.refresh_coordinator.query_snapshot(timeout_ms=2000)
                    if self.refresh_coordinator is not None
                    else nullcontext(dict(
                        self.index_status or {"status": "unknown", "ok": True}
                    ))
                )
                with status_context as status:
                    status = dict(status)
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(status, ensure_ascii=False)}],
                        "structuredContent": status,
                        "isError": False,
                    },
                }
            if name in {"plan_refresh", "refresh_index"} and self.refresh_coordinator is not None:
                allowed = set() if name == "plan_refresh" else {"mode", "timeout_ms"}
                unexpected = sorted(set(arguments) - allowed)
                if unexpected:
                    raise ValueError(f"unexpected refresh arguments: {', '.join(unexpected)}")
                payload = (
                    self.refresh_coordinator.plan()
                    if name == "plan_refresh"
                    else refresh_with_retry(
                        self.refresh_coordinator,
                        mode=arguments.get("mode", "fast"),
                        timeout_ms=arguments.get("timeout_ms", 300_000),
                    )
                )
                failed = payload.get("status") in {
                    "failed", "refresh_in_progress", "refresh_owned_elsewhere"
                }
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{
                            "type": "text",
                            "text": json.dumps(payload, ensure_ascii=False),
                        }],
                        "structuredContent": payload,
                        "isError": failed,
                    },
                }
            if name in {"plan_refresh", "refresh_index"}:
                raise RuntimeError(
                    "refresh is unavailable without an exact project configuration"
                )
            if self.service is None:
                if name == "locate_files":
                    unavailable = dict(self.index_status or {})
                    repository = str(
                        unavailable.get("resolved_root")
                        or unavailable.get("identity", {}).get("repository", "")
                    )
                    payload = _locate_payload(
                        status="error",
                        repository=repository,
                        freshness={
                            "status": str(unavailable.get("status", "not_configured")),
                            "ok": False,
                            "reason": str(unavailable.get("reason", "project_configuration_not_loaded")),
                        },
                        message="Codebase Atlas is unavailable for this project.",
                    )
                    return {"jsonrpc": "2.0", "id": request_id, "result": _locate_tool_result(payload)}
                unavailable = dict(self.index_status or {
                    "status": "not_configured",
                    "ok": False,
                    "reason": "project_configuration_not_loaded",
                })
                structured = {
                    "schema_version": 1,
                    "status": "error",
                    "code": str(unavailable.get("status", "project_unavailable")),
                    "message": (
                        "Codebase Atlas is unavailable for this project; call "
                        "project_status and follow next_action."
                    ),
                    "project": unavailable,
                }
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{
                            "type": "text",
                            "text": json.dumps(structured, ensure_ascii=False),
                        }],
                        "structuredContent": structured,
                        "isError": True,
                    },
                }
            if name == "locate_files":
                snapshot_context = (
                    self.refresh_coordinator.query_snapshot(
                        timeout_ms=arguments.get("timeout_ms", 30_000)
                    )
                    if self.refresh_coordinator is not None
                    else nullcontext(
                        self.index_status
                        or {"status": "unknown", "ok": True, "reason": ""}
                    )
                )
                with snapshot_context as status:
                    identity = status.get("identity", {})
                    repository_value = identity.get("repository") if isinstance(identity, dict) else None
                    repository = str(repository_value or getattr(self.service, "repository", "") or "")
                    freshness = {
                        "status": str(status.get("status", "unknown")),
                        "ok": bool(status.get("ok", True)),
                        "reason": str(status.get("reason", "")),
                    }
                    if not repository:
                        payload = _locate_payload(
                            status="error", repository="", freshness=freshness,
                            message="Exact repository identity is unavailable.",
                        )
                    elif not freshness["ok"]:
                        payload = _locate_payload(
                            status="stale", repository=repository, freshness=freshness,
                            message="The index is not current; refresh it before locating files.",
                        )
                    else:
                        intent = arguments.get("intent")
                        result = self.service.locate_files(
                            intent,
                            max_files=arguments.get("max_files", 2),
                            max_internal_rows=arguments.get("max_internal_rows", 60),
                            timeout_ms=arguments.get("timeout_ms", 30_000),
                        )
                        payload = _locate_payload(
                            status=str(result["status"]),
                            repository=repository,
                            freshness=freshness,
                            files=result["files"],
                            matched_terms=result["matched_terms"],
                            budget=result["budget"],
                        )
                return {"jsonrpc": "2.0", "id": request_id, "result": _locate_tool_result(payload)}

            if self.refresh_coordinator is None:
                policy_error = stale_policy_error(
                    self.index_status or {"ok": True}, self.stale_policy
                )
                if policy_error:
                    raise RuntimeError(policy_error)
            symbol = arguments.get("symbol")
            if not isinstance(symbol, str) or not symbol:
                raise ValueError("symbol must be a non-empty string")
            target_path = arguments.get("target_path", "")
            if not isinstance(target_path, str):
                raise ValueError("target_path must be a string")
            target_owner = arguments.get("target_owner", "")
            if not isinstance(target_owner, str):
                raise ValueError("target_owner must be a string")
            relation = arguments.get("relation", "")
            if not isinstance(relation, str):
                raise ValueError("relation must be a string")
            budget = {
                name: arguments[name]
                for name in ("max_nodes", "max_edges", "timeout_ms")
                if name in arguments
            }
            if name == "analyze_change":
                brief = analyze_change(
                    self.service,
                    symbol,
                    intent=arguments.get("intent", "change_behavior"),
                    target_path=target_path,
                    target_owner=target_owner,
                    direction=arguments.get("direction", "upstream"),
                    depth=arguments.get("depth", 2),
                    max_nodes=arguments.get("max_nodes", 100),
                    max_edges=arguments.get("max_edges", 200),
                    timeout_ms=arguments.get("timeout_ms", 60_000),
                    index_status=self.index_status,
                    stale_policy=self.stale_policy,
                )
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": _brief_tool_result(brief),
                }
            if name in {"definition", "references", "callers", "callees"}:
                continuation = (
                    {"continuation": arguments["continuation"]}
                    if name == "references" and "continuation" in arguments
                    else {}
                )
                request = QueryRequest(
                    name, symbol, {
                        "target_path": target_path,
                        "target_owner": target_owner,
                        "relation": relation,
                        **budget,
                        **continuation,
                    }
                )
            elif name == "related_tests":
                request = QueryRequest(
                    "related_tests",
                    symbol,
                    {
                        "target_path": target_path,
                        "target_owner": target_owner,
                        **budget,
                    },
                )
            elif name == "impact":
                request = QueryRequest(
                    "impact",
                    symbol,
                    {
                        "direction": arguments.get("direction", "upstream"),
                        "depth": arguments.get("depth", 1),
                        "target_path": target_path,
                        "target_owner": target_owner,
                        **budget,
                    },
                )
            else:
                return self._error(request_id, -32602, f"Unknown tool: {name}")
            snapshot_context = (
                self.refresh_coordinator.query_snapshot(
                    timeout_ms=arguments.get("timeout_ms", 30_000)
                )
                if self.refresh_coordinator is not None
                else nullcontext(
                    dict(self.index_status) if self.index_status is not None else None
                )
            )
            with snapshot_context as query_status:
                policy_error = stale_policy_error(
                    query_status or {"ok": True}, self.stale_policy
                )
                if policy_error:
                    raise RuntimeError(policy_error)
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": _tool_result(
                        self.service.query(request),
                        query_status,
                        self.stale_policy,
                    ),
                }
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            if name == "locate_files":
                status = self.index_status or {"status": "unknown", "ok": True, "reason": ""}
                identity = status.get("identity", {})
                repository_value = identity.get("repository") if isinstance(identity, dict) else None
                repository = str(repository_value or getattr(self.service, "repository", "") or "")
                payload = _locate_payload(
                    status="error",
                    repository=repository,
                    freshness={
                        "status": str(status.get("status", "unknown")),
                        "ok": bool(status.get("ok", True)),
                        "reason": str(status.get("reason", "")),
                    },
                    message=str(exc),
                )
                return {"jsonrpc": "2.0", "id": request_id, "result": _locate_tool_result(payload)}
            failure = attach_operational_status(
                {"error": str(exc)}, self.index_status, self.stale_policy
            )
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(failure)}],
                    "structuredContent": failure,
                    "isError": True,
                },
            }


def run_stdio(server: McpServer, input_stream: TextIO = sys.stdin, output_stream: TextIO = sys.stdout) -> None:
    for line in input_stream:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError("JSON-RPC message must be an object")
            response = server.handle(message)
        except (json.JSONDecodeError, ValueError) as exc:
            response = McpServer._error(None, -32700, str(exc))
        if response is not None:
            output_stream.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            output_stream.flush()
