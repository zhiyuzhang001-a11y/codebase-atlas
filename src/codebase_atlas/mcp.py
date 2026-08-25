"""Minimal read-only MCP stdio interface over AtlasService."""

from __future__ import annotations

from dataclasses import asdict
import json
import sys
from typing import Any, TextIO

from . import __version__
from .operations import (
    attach_operational_status,
    stale_policy_error,
)
from .service import AtlasService, QueryRequest, QueryResponse


PROTOCOL_VERSION = "2025-11-25"


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
    return {
        "content": [{"type": "text", "text": json.dumps(structured, ensure_ascii=False)}],
        "structuredContent": structured,
        "isError": False,
    }


TOOLS = [
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
        service: AtlasService,
        index_status: dict[str, Any] | None = None,
        stale_policy: str = "ignore",
    ) -> None:
        self.service = service
        self.index_status = index_status
        self.stale_policy = stale_policy

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        request_id = message.get("id")
        method = message.get("method")
        if method == "notifications/initialized":
            return None
        if method == "initialize":
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
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": _tool_result(
                    self.service.query(request),
                    self.index_status,
                    self.stale_policy,
                ),
            }
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
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
