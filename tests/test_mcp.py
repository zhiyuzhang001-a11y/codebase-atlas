from __future__ import annotations

from contextlib import nullcontext
from io import StringIO
import json
from pathlib import Path
import unittest

from codebase_atlas.contracts import Node, SourceRange
from codebase_atlas.mcp import McpServer, PROTOCOL_VERSION, run_stdio
from codebase_atlas.service import QueryResponse


class FakeService:
    def __init__(self):
        self.last_request = None
        self.repository = Path("/repo")
        self.last_locate = None

    def query(self, request):
        self.last_request = request
        node = Node("n", "test", request.symbol, SourceRange("tests/x.ts", 1, 1), "fake", 1.0, "d" * 64)
        return QueryResponse(request.query_type, (node,), ())

    def locate_files(self, intent, **budget):
        self.last_locate = (intent, budget)
        return {
            "status": "ok",
            "files": [{"path": "src/target.py", "rank": -10.5, "evidence_count": 2}],
            "matched_terms": ["target"],
            "budget": {
                "provider_queries": 1,
                "max_internal_rows": budget["max_internal_rows"],
                "max_files": budget["max_files"],
            },
        }


class FakeRefreshCoordinator:
    def __init__(self, plan=None, refreshed=None):
        self.calls = []
        self.plan_result = plan or {"status": "planned", "dirty_paths": ["sample.py"]}
        self.refresh_result = refreshed or {"status": "refreshed", "generation_after": "g2"}
        self.status = {"status": "fresh", "ok": True, "generation_id": "g1"}

    def plan(self):
        self.calls.append(("plan", {}))
        return dict(self.plan_result)

    def refresh(self, **arguments):
        self.calls.append(("refresh", arguments))
        if self.refresh_result.get("status") in {"refreshed", "current"}:
            self.status = {
                "status": "fresh", "ok": True,
                "generation_id": self.refresh_result.get("generation_after", "g2"),
            }
        return dict(self.refresh_result)

    def query_snapshot(self, *, timeout_ms):
        self.calls.append(("query_snapshot", {"timeout_ms": timeout_ms}))
        return nullcontext(dict(self.status))


class McpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = FakeService()
        self.server = McpServer(self.service)

    def test_initializes_and_lists_read_only_tools(self) -> None:
        initialized = self.server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": PROTOCOL_VERSION}}
        )
        self.assertEqual(initialized["result"]["protocolVersion"], PROTOCOL_VERSION)
        listed = self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(
            [tool["name"] for tool in listed["result"]["tools"]],
            [
                "project_status", "plan_refresh", "refresh_index", "locate_files",
                "definition", "references", "callers", "callees",
                "related_tests", "impact", "analyze_change",
            ],
        )
        self.assertTrue(all(
            tool["annotations"]["readOnlyHint"]
            for tool in listed["result"]["tools"]
            if tool["name"] != "refresh_index"
        ))
        schemas = {
            tool["name"]: tool["inputSchema"]["properties"]
            for tool in listed["result"]["tools"]
        }
        self.assertEqual(schemas["callers"]["relation"]["enum"], ["registers"])
        self.assertEqual(schemas["callees"]["relation"]["enum"], ["registers"])
        self.assertNotIn("relation", schemas["definition"])
        self.assertEqual(schemas["references"]["continuation"]["maxLength"], 512)
        self.assertNotIn("continuation", schemas["definition"])
        self.assertIn("fix_bug", schemas["analyze_change"]["intent"]["enum"])
        self.assertEqual(schemas["locate_files"]["max_files"]["maximum"], 2)
        self.assertEqual(schemas["locate_files"]["max_internal_rows"]["maximum"], 60)
        refresh = next(tool for tool in listed["result"]["tools"] if tool["name"] == "refresh_index")
        self.assertFalse(refresh["annotations"]["readOnlyHint"])
        self.assertEqual(refresh["inputSchema"]["properties"]["timeout_ms"]["maximum"], 300000)

    def test_plan_and_refresh_route_to_injected_coordinator(self) -> None:
        coordinator = FakeRefreshCoordinator()
        server = McpServer(self.service, refresh_coordinator=coordinator)
        planned = server.handle({
            "jsonrpc": "2.0", "id": 20, "method": "tools/call",
            "params": {"name": "plan_refresh", "arguments": {}},
        })
        refreshed = server.handle({
            "jsonrpc": "2.0", "id": 21, "method": "tools/call",
            "params": {
                "name": "refresh_index",
                "arguments": {"mode": "moderate", "timeout_ms": 1234},
            },
        })
        self.assertEqual(planned["result"]["structuredContent"]["dirty_paths"], ["sample.py"])
        self.assertEqual(refreshed["result"]["structuredContent"]["generation_after"], "g2")
        self.assertEqual(
            coordinator.calls,
            [("plan", {}), ("refresh", {"mode": "moderate", "timeout_ms": 1234})],
        )

    def test_locate_files_returns_bounded_heuristic_contract(self) -> None:
        server = McpServer(
            self.service,
            {"status": "fresh", "ok": True, "reason": "", "identity": {"repository": "/repo"}},
            "warn",
        )
        response = server.handle({
            "jsonrpc": "2.0", "id": 30, "method": "tools/call",
            "params": {
                "name": "locate_files",
                "arguments": {"intent": "target behavior", "max_files": 2, "max_internal_rows": 40},
            },
        })
        result = response["result"]["structuredContent"]
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["repository"], "/repo")
        self.assertTrue(result["heuristic"])
        self.assertTrue(result["non_exhaustive"])
        self.assertEqual([item["path"] for item in result["files"]], ["src/target.py"])
        self.assertNotIn("symbol", result)
        self.assertNotIn("callable", result)
        self.assertEqual(self.service.last_locate[1]["max_internal_rows"], 40)

    def test_locate_files_refuses_stale_index_without_provider_query(self) -> None:
        server = McpServer(
            self.service,
            {
                "status": "stale", "ok": False, "reason": "repository_changed",
                "identity": {"repository": "/repo"},
            },
            "warn",
        )
        response = server.handle({
            "jsonrpc": "2.0", "id": 31, "method": "tools/call",
            "params": {"name": "locate_files", "arguments": {"intent": "target"}},
        })
        result = response["result"]["structuredContent"]
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["files"], [])
        self.assertEqual(result["budget"]["provider_queries"], 0)
        self.assertIsNone(self.service.last_locate)

    def test_project_status_needs_no_symbol(self) -> None:
        status = {"status": "fresh", "ok": True, "identity": {"repository": "/repo"}}
        server = McpServer(
            self.service, status, "warn",
            instructions="Call project_status for /repo",
        )
        initialized = server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        self.assertIn("/repo", initialized["result"]["instructions"])
        self.assertIn("project_status", initialized["result"]["instructions"])
        response = server.handle({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "project_status", "arguments": {}},
        })
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(
            response["result"]["structuredContent"]["identity"]["repository"],
            "/repo",
        )

    def test_query_response_binds_snapshot_generation_id(self) -> None:
        server = McpServer(
            self.service,
            {"status": "fresh", "ok": True, "generation_id": "generation-7"},
        )
        response = server.handle({
            "jsonrpc": "2.0", "id": 22, "method": "tools/call",
            "params": {"name": "definition", "arguments": {"symbol": "target"}},
        })
        self.assertEqual(
            response["result"]["structuredContent"]["generation_id"],
            "generation-7",
        )

    def test_on_query_refreshes_dirty_generation_before_query(self) -> None:
        coordinator = FakeRefreshCoordinator()
        server = McpServer(
            self.service,
            {"status": "stale", "ok": False},
            "warn",
            refresh_coordinator=coordinator,
            auto_update="on-query",
            auto_update_timeout_ms=4321,
        )
        response = server.handle({
            "jsonrpc": "2.0", "id": 23, "method": "tools/call",
            "params": {"name": "definition", "arguments": {"symbol": "target"}},
        })
        result = response["result"]["structuredContent"]
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(result["generation_id"], "g2")
        self.assertEqual(result["index"]["auto_update"]["status"], "refreshed")
        self.assertEqual(
            coordinator.calls,
            [
                ("refresh", {"mode": "fast", "timeout_ms": 4321}),
                ("query_snapshot", {"timeout_ms": 30000}),
            ],
        )

    def test_on_query_coalesces_current_generation_without_provider_call(self) -> None:
        coordinator = FakeRefreshCoordinator(
            refreshed={
                "status": "current", "generation_after": "g1",
                "provider_called": False, "dirty_paths": [],
            }
        )
        server = McpServer(
            self.service,
            {"status": "fresh", "ok": True},
            "warn",
            refresh_coordinator=coordinator,
            auto_update="on-query",
        )
        response = server.handle({
            "jsonrpc": "2.0", "id": 24, "method": "tools/call",
            "params": {"name": "definition", "arguments": {"symbol": "target"}},
        })
        result = response["result"]["structuredContent"]
        self.assertEqual(result["index"]["auto_update"]["status"], "current")
        self.assertEqual(
            coordinator.calls,
            [
                ("refresh", {"mode": "fast", "timeout_ms": 60000}),
                ("query_snapshot", {"timeout_ms": 30000}),
            ],
        )

    def test_on_query_refresh_failure_is_visible_while_old_generation_is_usable(self) -> None:
        coordinator = FakeRefreshCoordinator(
            refreshed={
                "status": "failed",
                "generation_after": "g1",
                "provider_called": True,
                "previous_generation_preserved": True,
                "error": "provider timeout",
            }
        )
        server = McpServer(
            self.service,
            {"status": "fresh", "ok": True, "generation_id": "g1"},
            "warn",
            refresh_coordinator=coordinator,
            auto_update="on-query",
        )
        response = server.handle({
            "jsonrpc": "2.0", "id": 25, "method": "tools/call",
            "params": {"name": "definition", "arguments": {"symbol": "target"}},
        })
        result = response["result"]["structuredContent"]
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(result["generation_id"], "g1")
        self.assertEqual(result["index"]["auto_update"]["status"], "failed")
        self.assertTrue(
            result["index"]["auto_update"]["previous_generation_preserved"]
        )

    def test_unavailable_project_keeps_status_and_refuses_queries(self) -> None:
        status = {
            "status": "not_configured",
            "ok": False,
            "reason": "atlas_config_missing",
            "resolved_root": "/new-project",
            "provider_started": False,
            "next_action": "codebase-atlas onboard --repo /new-project",
        }
        server = McpServer(None, status, "error", instructions="Call project_status")
        project = server.handle({
            "jsonrpc": "2.0", "id": 20, "method": "tools/call",
            "params": {"name": "project_status", "arguments": {}},
        })
        self.assertEqual(
            project["result"]["structuredContent"]["status"], "not_configured"
        )
        refused = server.handle({
            "jsonrpc": "2.0", "id": 21, "method": "tools/call",
            "params": {"name": "definition", "arguments": {"symbol": "LocalUiServer"}},
        })
        self.assertTrue(refused["result"]["isError"])
        self.assertEqual(refused["result"]["structuredContent"]["code"], "not_configured")
        self.assertEqual(
            refused["result"]["structuredContent"]["project"]["resolved_root"],
            "/new-project",
        )

    def test_analyze_change_uses_shared_product_contract(self) -> None:
        response = self.server.handle({
            "jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {
                "name": "analyze_change",
                "arguments": {
                    "symbol": "target", "intent": "fix_bug",
                    "target_path": "src/x.py", "timeout_ms": 5000,
                },
            },
        })
        brief = response["result"]["structuredContent"]
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(brief["analysis_type"], "change_brief")
        self.assertEqual(brief["intent"], "fix_bug")
        self.assertEqual(brief["target"]["name"], "target")

    def test_calls_tool_with_structured_and_text_content(self) -> None:
        response = self.server.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "related_tests", "arguments": {"symbol": "target"}},
            }
        )
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(response["result"]["structuredContent"]["nodes"][0]["name"], "target")

    def test_forwards_path_and_owner_for_ambiguous_members(self) -> None:
        response = self.server.handle(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "impact",
                    "arguments": {
                        "symbol": "render",
                        "target_path": "packages/ui/src/render.ts",
                        "target_owner": "Renderer",
                        "max_nodes": 25,
                    },
                },
            }
        )
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(
            self.service.last_request.parameters["target_path"],
            "packages/ui/src/render.ts",
        )
        self.assertEqual(
            self.service.last_request.parameters["target_owner"],
            "Renderer",
        )
        self.assertEqual(self.service.last_request.parameters["max_nodes"], 25)

    def test_forwards_exact_registration_scope(self) -> None:
        response = self.server.handle({
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "callers",
                "arguments": {"symbol": "view", "relation": "registers"},
            },
        })
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(
            self.service.last_request.parameters["relation"], "registers"
        )

    def test_forwards_reference_continuation(self) -> None:
        response = self.server.handle({
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {
                "name": "references",
                "arguments": {"symbol": "target", "continuation": "opaque"},
            },
        })
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(
            self.service.last_request.parameters["continuation"], "opaque"
        )

    def test_stdio_emits_one_json_message_per_line(self) -> None:
        source = StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n")
        destination = StringIO()
        run_stdio(self.server, source, destination)
        self.assertEqual(json.loads(destination.getvalue())["result"], {})

    def test_rejects_out_of_range_depth_as_tool_error(self) -> None:
        response = self.server.handle(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "impact", "arguments": {"symbol": "x", "depth": 99}},
            }
        )
        self.assertTrue(response["result"]["isError"])

    def test_warns_or_refuses_stale_index_by_policy(self) -> None:
        stale = {"status": "stale", "ok": False, "reason": "repository_changed"}
        warning_server = McpServer(self.service, stale, "warn")
        warning = warning_server.handle({
            "jsonrpc": "2.0", "id": 6, "method": "tools/call",
            "params": {"name": "definition", "arguments": {"symbol": "target"}},
        })
        self.assertFalse(warning["result"]["isError"])
        self.assertEqual(
            warning["result"]["structuredContent"]["warnings"][0]["code"],
            "index_not_current",
        )

        strict_server = McpServer(self.service, stale, "error")
        refused = strict_server.handle({
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "definition", "arguments": {"symbol": "target"}},
        })
        self.assertTrue(refused["result"]["isError"])
        self.assertEqual(refused["result"]["structuredContent"]["index"], stale)



if __name__ == "__main__":
    unittest.main()
