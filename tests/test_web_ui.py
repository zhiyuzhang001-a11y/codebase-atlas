from __future__ import annotations

from http.client import HTTPConnection
import json
import threading
import unittest

from codebase_atlas.contracts import Edge, Node, SourceRange
from codebase_atlas.service import QueryResponse
from codebase_atlas.web_ui import LocalUiServer


class FakeService:
    def __init__(self) -> None:
        self.requests = []

    def query(self, request):
        self.requests.append(request)
        node = Node(
            "node:1", "method", request.symbol,
            SourceRange("src/sample.py", 10, 12),
            "fake", 1.0, "a" * 64, {"owner": "Sample"},
        )
        edge = Edge("node:1", "node:1", "calls", "fake", 1.0, "b" * 64)
        return QueryResponse(request.query_type, (node,), (edge,))


class WebUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = FakeService()
        self.server = LocalUiServer(
            self.service, repository="/repo", language="python",
            index_status={"source": {"status": "fresh"}}, port=0,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.close()
        self.thread.join(timeout=2)

    def request(self, method: str, path: str, *, headers=None, body=None):
        connection = HTTPConnection("127.0.0.1", self.server.port, timeout=2)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response, payload

    def test_index_and_assets_are_local_and_hardened(self) -> None:
        response, body = self.request("GET", "/")
        self.assertEqual(response.status, 200)
        self.assertIn(b"Codebase Atlas", body)
        self.assertIn("default-src 'self'", response.getheader("Content-Security-Policy"))
        self.assertEqual(response.getheader("X-Frame-Options"), "DENY")
        response, body = self.request("GET", "/app.js")
        self.assertEqual(response.status, 200)
        self.assertNotIn(b"https://", body)

    def test_status_and_query_require_session_authorization(self) -> None:
        response, _ = self.request("GET", "/api/status")
        self.assertEqual(response.status, 403)
        response, body = self.request(
            "POST", "/api/query",
            headers={
                "Content-Type": "application/json",
                "X-Atlas-Token": self.server.token,
                "Origin": self.server.origin,
            },
            body=json.dumps({
                "query_type": "callers", "symbol": "Sample.run",
                "parameters": {"target_path": "src/sample.py", "target_owner": "Sample"},
            }),
        )
        self.assertEqual(response.status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["nodes"][0]["name"], "Sample.run")
        self.assertEqual(self.service.requests[0].parameters["target_owner"], "Sample")

    def test_rejects_foreign_host_origin_and_unbounded_inputs(self) -> None:
        response, _ = self.request(
            "GET", "/", headers={"Host": "evil.invalid"}
        )
        self.assertEqual(response.status, 403)
        common = {
            "Content-Type": "application/json",
            "X-Atlas-Token": self.server.token,
            "Origin": "http://evil.invalid",
        }
        response, _ = self.request("POST", "/api/query", headers=common, body=b"{}")
        self.assertEqual(response.status, 403)
        common["Origin"] = self.server.origin
        response, _ = self.request(
            "POST", "/api/query", headers=common,
            body=json.dumps({"query_type": "impact", "symbol": "x", "parameters": {"depth": 99}}),
        )
        self.assertEqual(response.status, 400)
        response, _ = self.request(
            "POST", "/api/query", headers=common, body=b"x" * (64 * 1024 + 1)
        )
        self.assertEqual(response.status, 413)

    def test_status_lists_all_visible_query_capabilities(self) -> None:
        response, body = self.request(
            "GET", "/api/status", headers={"X-Atlas-Token": self.server.token}
        )
        self.assertEqual(response.status, 200)
        payload = json.loads(body)
        self.assertEqual(len(payload["capabilities"]), 6)
        self.assertEqual(payload["repository"], "/repo")


if __name__ == "__main__":
    unittest.main()
