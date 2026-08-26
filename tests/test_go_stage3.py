from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest

from codebase_atlas.config import AtlasConfig
from codebase_atlas.languages import detected_languages, select_language
from codebase_atlas.providers.go import (
    GOPLS_MEMORY_LIMIT, GoAdapterError, GoSemanticProvider, _GoAdapter,
)
from codebase_atlas.runtime import required_checks_ok, runtime_checks
from codebase_atlas.service import AtlasService, QueryRequest


class Completed:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeDirectProvider:
    def __init__(self, *, timeout: bool = False) -> None:
        self.timeout = timeout
        self.started = 0
        self.closed = 0

    def start(self, timeout_seconds: float = 30.0) -> None:
        self.started += 1
        if self.timeout:
            raise TimeoutError("provider readiness")

    def close(self) -> None:
        self.closed += 1

    def query_product(self, query_type: str, symbol: str, **_: object) -> dict[str, object]:
        return {
            "status": "ok", "capability": "complete", "warnings": [],
            "nodes": [{
                "id": "go:v1:node", "kind": "function", "name": symbol,
                "location": {"path": "main.go", "line": 3, "column": 6},
                "provider": "gopls-0.23.0", "confidence": 1.0,
                "evidence_hash": "a" * 64, "attributes": {"owner_named_origin": ""},
            }],
            "edges": [], "truncation": {"reasons": []},
        }


class GoStage3Tests(unittest.TestCase):
    def test_go_provider_memory_policy_is_contained_and_fixed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = root / "repo"
            repository.mkdir()
            before = os.environ.get("GOMEMLIMIT")
            adapter = _GoAdapter(
                repository=repository, workspace_root=repository,
                data_root=root / "data", go=root / "go", gopls=root / "gopls",
            )
            environment = adapter._environment()
            self.assertEqual(GOPLS_MEMORY_LIMIT, "1400MiB")
            self.assertEqual(environment["GOMEMLIMIT"], GOPLS_MEMORY_LIMIT)
            self.assertEqual(os.environ.get("GOMEMLIMIT"), before)

    def test_registry_detects_go_and_rejects_mixed_implicit_selection(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "go.mod").write_text("module example.test/mixed\n", encoding="utf-8")
            self.assertEqual(detected_languages(root), ("go",))
            self.assertEqual(select_language(root), "go")
            (root / "pyproject.toml").write_text("[project]\nname='mixed'\n", encoding="utf-8")
            self.assertEqual(detected_languages(root), ("python", "go"))
            with self.assertRaisesRegex(ValueError, "language_ambiguous"):
                select_language(root)
            self.assertEqual(select_language(root, "go"), "go")

    def test_go_config_round_trip_keeps_optional_legacy_runtimes_empty(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            (repo / "go.mod").write_text("module example.test/app\n", encoding="utf-8")
            config = AtlasConfig(
                repo, "go", None, None, None, root / "data", "gopls",
                go=root / "go", gopls=root / "gopls", go_workspace=repo,
            )
            path = root / "atlas.toml"
            config.write(path)
            loaded = AtlasConfig.load(path)
            self.assertEqual(loaded.language, "go")
            self.assertIsNone(loaded.node)
            self.assertEqual(loaded.go_workspace, repo.resolve())
            self.assertIn('go_workspace = "', path.read_text(encoding="utf-8"))

    def test_go_runtime_checks_are_read_only_and_do_not_require_legacy_providers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "go.mod").write_text("module example.test/app\n", encoding="utf-8")
            go, gopls = root / "go", root / "gopls"
            go.touch()
            gopls.touch()

            def runner(command: list[str], **_: object) -> Completed:
                if command[-1] == "version" and command[0] == str(go):
                    return Completed(0, "go version go1.27.0 darwin/arm64\n")
                if command[-1] == "version" and command[0] == str(gopls):
                    return Completed(0, "golang.org/x/tools/gopls v0.23.0\n")
                return Completed(1, stderr="not configured")

            checks = runtime_checks(
                root, language="go", go=go, gopls=gopls,
                go_workspace=root, runner=runner,
            )
            by_name = {str(item["name"]): item for item in checks}
            self.assertTrue(required_checks_ok(checks))
            self.assertFalse(by_name["node"]["required"])
            self.assertFalse(by_name["codebase_memory"]["required"])
            self.assertTrue(by_name["gopls"]["ok"])

    def test_service_direct_route_preserves_schema_and_owned_lifecycle(self) -> None:
        provider = FakeDirectProvider()
        service = AtlasService(direct_provider=provider)
        with service:
            response = service.query(QueryRequest(
                "definition", "Run", {"target_path": "main.go", "target_owner": "Alpha"}
            ))
            self.assertEqual(response.nodes[0].location.path, "main.go")
            self.assertEqual(response.nodes[0].provider, "gopls-0.23.0")
            self.assertFalse(response.truncated)
        self.assertEqual((provider.started, provider.closed), (1, 1))

    def test_direct_provider_readiness_timeout_returns_bounded_partial(self) -> None:
        provider = FakeDirectProvider(timeout=True)
        service = AtlasService(direct_provider=provider)
        with service:
            response = service.query(QueryRequest(
                "definition", "Run", {"target_path": "main.go", "timeout_ms": 1}
            ))
        self.assertTrue(response.truncated)
        self.assertIn("time_budget_exceeded", response.truncation["reasons"])
        self.assertEqual(provider.closed, 0)

    def test_product_provider_requires_explicit_target_path(self) -> None:
        provider = object.__new__(GoSemanticProvider)
        with self.assertRaisesRegex(GoAdapterError, "target_path_required"):
            provider.query_product("definition", "Run")

    @unittest.skipUnless(os.environ.get("ATLAS_GO") and os.environ.get("ATLAS_GOPLS"), "M27 Go runtimes are required")
    def test_real_gopls_same_file_receiver_identity(self) -> None:
        repository = Path(os.environ["ATLAS_GO_FIXTURE"]).resolve()
        with tempfile.TemporaryDirectory() as raw:
            provider = GoSemanticProvider(
                repository, Path(raw), Path(os.environ["ATLAS_GO"]),
                Path(os.environ["ATLAS_GOPLS"]), repository,
            )
            service = AtlasService(direct_provider=provider)
            with service:
                responses = {
                    query_type: service.query(QueryRequest(
                        query_type, "Run", {
                            "target_path": "app/identity.go",
                            "target_owner": "Alpha",
                            **({"direction": "upstream", "depth": 2} if query_type == "impact" else {}),
                        },
                    ))
                    for query_type in (
                        "definition", "references", "callers", "callees",
                        "related_tests", "impact",
                    )
                }
            response = responses["definition"]
            self.assertEqual(len(response.nodes), 1)
            self.assertEqual(response.nodes[0].attributes["owner_named_origin"], "Alpha")
            self.assertEqual(response.nodes[0].attributes["declared_receiver_mode"], "value")
            self.assertTrue(responses["references"].nodes)
            self.assertTrue(responses["callers"].nodes)
            self.assertTrue(responses["related_tests"].nodes)
            self.assertTrue(responses["impact"].nodes)
            self.assertTrue(responses["impact"].depths)
            self.assertTrue(responses["impact"].paths)
            self.assertEqual(responses["callees"].query_type, "callees")

    @unittest.skipUnless(
        os.environ.get("ATLAS_GO") and os.environ.get("ATLAS_GOPLS")
        and os.environ.get("ATLAS_GO_FAULT_PROVIDER"),
        "M27 Go fault runtime is required",
    )
    def test_real_provider_start_and_query_timeouts_leave_no_owned_process(self) -> None:
        repository = Path(os.environ["ATLAS_GO_FIXTURE"]).resolve()
        fault = Path(os.environ["ATLAS_GO_FAULT_PROVIDER"]).resolve()
        for mode, expected in (
            ("hang-initialize", "provider_start_timeout"),
            ("hang-query", "provider_query_timeout"),
        ):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as raw:
                adapter = _GoAdapter(
                    repository=repository, workspace_root=repository,
                    data_root=Path(raw), go=Path(os.environ["ATLAS_GO"]),
                    gopls=Path(os.environ["ATLAS_GOPLS"]),
                    start_timeout=0.05,
                    command=[sys.executable, str(fault), mode],
                )
                try:
                    if mode == "hang-initialize":
                        with self.assertRaises(GoAdapterError) as raised:
                            adapter.start()
                    else:
                        adapter.start()
                        with self.assertRaises(GoAdapterError) as raised:
                            adapter.query({
                                "query_type": "definition",
                                "target": {"path": "app/identity.go", "symbol": "Run", "owner": "Alpha"},
                                "parameters": {"timeout_ms": 10},
                            })
                    self.assertEqual(raised.exception.code, expected)
                finally:
                    adapter.close()
                self.assertIsNone(adapter.client)


if __name__ == "__main__":
    unittest.main()
