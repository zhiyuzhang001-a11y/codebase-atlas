from __future__ import annotations

import unittest
from pathlib import Path
import tempfile
from threading import Event, current_thread
from unittest.mock import patch

from codebase_atlas.contracts import Edge, Node, SourceRange
from codebase_atlas.graph import ImpactHit, ImpactTraversal
from codebase_atlas.index_state import RepositorySnapshot
from codebase_atlas.provider_transport import ProviderInitializeTimeout
from codebase_atlas.providers.python_references import PythonExactReferenceProvider
from codebase_atlas.providers.python_registrations import PythonRegistrationProvider
from codebase_atlas.service import AtlasService, QueryRequest


HASH = "c" * 64


class FakeLifecycle:
    def __init__(self) -> None:
        self.starts = 0
        self.closes = 0

    def start(self, *, timeout_seconds=None) -> None:
        self.starts += 1

    def close(self) -> None:
        self.closes += 1


class FakeImpactProvider:
    def definitions(self, symbol, *, target_path="", target_owner=""):
        return (Node("target", "function", symbol, SourceRange("src/x.py", 1, 1), "fake", 1.0, HASH),)

    def callers(self, symbol, *, target_path="", target_owner="", **budget):
        return self.impact(
            symbol, direction="upstream", max_depth=1,
            target_path=target_path, target_owner=target_owner, **budget,
        )

    def callees(self, symbol, *, target_path="", target_owner="", **budget):
        return self.impact(
            symbol, direction="downstream", max_depth=1,
            target_path=target_path, target_owner=target_owner, **budget,
        )

    def related_tests(self, symbol, *, target_path="", target_owner="", **budget):
        return self.impact(
            symbol,
            direction="upstream",
            max_depth=1,
            target_path=target_path,
            target_owner=target_owner,
            **budget,
        )

    def impact(
        self, _symbol, *, direction, max_depth, target_path="", target_owner="", **_budget
    ):
        target = Node("target", "function", "target", SourceRange("src/x.py", 1, 1), "fake", 1.0, HASH)
        caller = Node("caller", "function", "caller", SourceRange("src/x.py", 2, 2), "fake", 1.0, HASH)
        edge = Edge("caller", "target", "calls", "fake", 1.0, HASH)
        return (ImpactHit(caller, min(max_depth, 1), (edge,)),)


class FakeSemanticProvider:
    def __init__(self):
        self.starts = 0
        self.closes = 0

    def start(self, *, timeout_seconds=None):
        self.starts += 1

    def close(self):
        self.closes += 1

    def query(
        self,
        query_type,
        symbol,
        *,
        target_path="",
        target_owner="",
        timeout_ms=None,
    ):
        return (
            Node("reference-1", query_type, symbol, SourceRange("src/x.py", 3, 3), "semantic", 1.0, HASH),
            Node("reference-2", query_type, symbol, SourceRange("src/y.py", 5, 5), "semantic", 1.0, HASH),
        )


class FakeTestProvider:
    calls = 0

    def related_tests(
        self,
        _repository,
        symbol,
        *,
        target_path="",
        target_owner="",
        timeout_ms=None,
    ):
        self.calls += 1
        target = Node("target", "function", symbol, SourceRange(target_path or "src/x.ts", 1, 1), "fake", 1.0, HASH)
        test = Node("test", "test", "works", SourceRange("tests/x.test.ts", 4, 5), "tests", 1.0, HASH)
        return ((test, Edge("test", target.id, "calls", "tests", 1.0, HASH)),)


class CountingTsReferences(FakeTestProvider):
    tsconfig = Path("tsconfig.json")

    def __init__(self, count: int = 5, *, timeout: bool = False) -> None:
        self.count = count
        self.timeout = timeout
        self.reference_calls = 0

    def references(self, _repository, symbol, **_options):
        self.reference_calls += 1
        if self.timeout:
            raise TimeoutError("budget")
        return tuple(
            Node(
                f"ts-reference-{index}",
                "references",
                symbol,
                SourceRange(f"src/x{index}.ts", index + 1, index + 1),
                "atlas-ts-references",
                1.0,
                HASH,
            )
            for index in range(self.count)
        )


def source_snapshot(fingerprint: str | None = "source-a") -> RepositorySnapshot:
    return RepositorySnapshot(
        "git" if fingerprint is not None else "unknown",
        fingerprint,
        "head",
        0,
        "snapshot_complete" if fingerprint is not None else "git_status_failed",
    )


class ServiceTests(unittest.TestCase):
    def test_provider_lock_conflict_is_explicit(self) -> None:
        class BusyLifecycle(FakeLifecycle):
            def start(self, *, timeout_seconds=None):
                self.starts += 1
                self.last_timeout = timeout_seconds
                raise TimeoutError("busy")

        lifecycle = BusyLifecycle()
        service = AtlasService(
            impact_provider=FakeImpactProvider(), lifecycle=lifecycle
        )
        with service:
            response = service.query(QueryRequest(
                "definition", "target", {"timeout_ms": 60_000}
            ))
            repeated = service.query(QueryRequest(
                "callers", "target", {"timeout_ms": 60_000}
            ))
        self.assertTrue(response.truncated)
        self.assertEqual(response.truncation["reasons"], ("provider_busy",))
        self.assertEqual(repeated.truncation["reasons"], ("provider_busy",))
        self.assertEqual(lifecycle.starts, 1)
        self.assertEqual(lifecycle.last_timeout, 2.0)

    def test_locate_files_separates_lock_initialize_and_remaining_tool_budget(self) -> None:
        class SeparatedLifecycle(FakeLifecycle):
            def start_for_request(self, *, lock_timeout_seconds, initialize_timeout_seconds):
                self.starts += 1
                self.lock_timeout = lock_timeout_seconds
                self.initialize_timeout = initialize_timeout_seconds

        class LocateProvider(FakeImpactProvider):
            def locate_files(self, _intent, **budget):
                self.budget = budget
                return {"status": "no_matches", "files": [], "matched_terms": [],
                        "budget": {"provider_queries": 1, "max_internal_rows": 60,
                                   "max_files": 2}}

        lifecycle = SeparatedLifecycle()
        provider = LocateProvider()
        service = AtlasService(structural_provider=provider, lifecycle=lifecycle)
        with service:
            result = service.locate_files("synthetic", timeout_ms=30_000)
        self.assertEqual(result["status"], "no_matches")
        self.assertEqual(lifecycle.lock_timeout, 2.0)
        self.assertEqual(lifecycle.initialize_timeout, 30.0)
        self.assertGreater(provider.budget["timeout_ms"], 0)
        self.assertLessEqual(provider.budget["timeout_ms"], 30_000)

    def test_locate_files_reports_initialize_timeout_not_lock_contention(self) -> None:
        class SlowLifecycle(FakeLifecycle):
            def start_for_request(self, **_timeouts):
                raise ProviderInitializeTimeout("initialize")

        service = AtlasService(
            structural_provider=FakeImpactProvider(), lifecycle=SlowLifecycle()
        )
        with service:
            with self.assertRaisesRegex(RuntimeError, "startup timed out"):
                service.locate_files("synthetic")

    @staticmethod
    def _registration_index(repository: Path):
        return PythonRegistrationProvider(repository, "p").scan()

    @staticmethod
    def _registration_structural(definitions):
        class RegistrationStructural(FakeImpactProvider):
            project = "p"

            def definitions(self, symbol, *, target_path="", target_owner=""):
                line, owner = definitions[symbol]
                return (Node(
                    f"p.{target_path.replace('/', '.').removesuffix('.py')}.{owner}",
                    "function", symbol, SourceRange(target_path, line, line),
                    "structural", 1.0, HASH,
                ),)

            def callers(self, *args, **kwargs):
                return ImpactTraversal(())

            def callees(self, *args, **kwargs):
                return ImpactTraversal(())

            def related_tests(self, *args, **kwargs):
                return ImpactTraversal(())

            def impact(self, *args, **kwargs):
                return ImpactTraversal(())

        return RegistrationStructural()

    def test_adds_exact_registration_callers_and_inverse_callees(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "routes.py").write_text(
                "from flask import Flask\napp = Flask(__name__)\n\n"
                "def view():\n    pass\n\n"
                "app.add_url_rule('/view', view_func=view)\n"
            )
            structural = self._registration_structural({"view": (4, "view")})
            service = AtlasService(
                repository=repository,
                structural_provider=structural,
                impact_provider=structural,
                registration_index=self._registration_index(repository),
            )
            with service:
                callers = service.query(QueryRequest(
                    "callers", "view", {"target_path": "routes.py", "target_owner": "view"}
                ))
                callees = service.query(QueryRequest(
                    "callees", "registration@7", {"target_path": "routes.py"}
                ))
        self.assertEqual([node.name for node in callers.nodes], ["registration@7"])
        self.assertEqual([edge.relation for edge in callers.edges], ["registers"])
        self.assertEqual([node.name for node in callees.nodes], ["view"])
        self.assertEqual([edge.relation for edge in callees.edges], ["registers"])

    def test_registration_callers_work_when_structural_definition_is_absent(self) -> None:
        class NoDefinition(FakeImpactProvider):
            project = "p"

            def definitions(self, *args, **kwargs):
                return ()

            def callers(self, *args, **kwargs):
                return ImpactTraversal(())

        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "routes.py").write_text(
                "from flask import Flask\napp = Flask(__name__)\n\n"
                "@app.route('/view')\ndef view():\n    pass\n"
            )
            structural = NoDefinition()
            service = AtlasService(
                repository=repository,
                structural_provider=structural,
                impact_provider=structural,
                registration_index=self._registration_index(repository),
            )
            with service:
                response = service.query(QueryRequest(
                    "callers", "view", {"target_path": "routes.py", "target_owner": "view"}
                ))
        self.assertEqual([node.name for node in response.nodes], ["registration@4"])
        self.assertEqual([edge.relation for edge in response.edges], ["registers"])

    def test_registration_edges_feed_related_tests_and_upstream_impact(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            tests = repository / "tests"
            tests.mkdir()
            (tests / "test_routes.py").write_text(
                "from flask import Flask\n\n"
                "def test_register(app: Flask):\n"
                "    def callback():\n        pass\n"
                "    app.add_url_rule('/callback', view_func=callback)\n"
            )
            structural = self._registration_structural({
                "callback": (4, "test_register.callback")
            })
            service = AtlasService(
                repository=repository,
                structural_provider=structural,
                impact_provider=structural,
                registration_index=self._registration_index(repository),
            )
            request = {"target_path": "tests/test_routes.py", "target_owner": "test_register.callback"}
            with service:
                related = service.query(QueryRequest("related_tests", "callback", request))
                impact = service.query(QueryRequest("impact", "callback", request))
        self.assertEqual([node.name for node in related.nodes], ["test_register"])
        self.assertEqual([edge.relation for edge in related.edges], ["registers"])
        self.assertEqual([node.name for node in impact.nodes], ["test_register"])

    def test_registration_query_uses_only_injected_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            source = repository / "routes.py"
            source.write_text(
                "from flask import Flask\napp = Flask(__name__)\n\n"
                "def view():\n    pass\n\n"
                "app.add_url_rule('/one', view_func=view)\n"
            )
            structural = self._registration_structural({"view": (4, "view")})
            service = AtlasService(
                repository=repository,
                structural_provider=structural,
                impact_provider=structural,
                registration_index=self._registration_index(repository),
            )
            request = QueryRequest("callers", "view", {"target_path": "routes.py"})
            with service:
                first = service.query(request)
                source.write_text(source.read_text() + "app.add_url_rule('/two', view_func=view)\n")
                second = service.query(request)
        self.assertEqual(len(first.edges), 1)
        self.assertEqual(len(second.edges), 1)

    def test_missing_registration_snapshot_preserves_structural_result(self) -> None:
        structural = FakeImpactProvider()
        structural.project = "p"
        service = AtlasService(
            repository=Path(__file__).resolve().parent,
            structural_provider=structural,
            impact_provider=structural,
        )
        with service:
            response = service.query(QueryRequest(
                "callers", "target", {"target_path": "src/x.py"}
            ))
        self.assertEqual([node.name for node in response.nodes], ["caller"])
        self.assertFalse(response.truncated)

    def test_registration_query_never_invokes_source_scanner(self) -> None:
        structural = FakeImpactProvider()
        structural.project = "p"
        service = AtlasService(
            repository=Path(__file__).resolve().parent,
            structural_provider=structural,
            impact_provider=structural,
        )
        with patch.object(
            PythonRegistrationProvider,
            "scan",
            side_effect=AssertionError("query-time scan"),
        ) as scan:
            with service:
                response = service.query(QueryRequest(
                    "callers", "target", {"target_path": "src/x.py"}
                ))
        scan.assert_not_called()
        self.assertEqual([node.name for node in response.nodes], ["caller"])

    def test_scoped_registration_query_skips_structural_provider_and_lifecycle(self) -> None:
        class ForbiddenStructural(FakeImpactProvider):
            project = "p"

            def callers(self, *args, **kwargs):
                raise AssertionError("structural callers started")

            def callees(self, *args, **kwargs):
                raise AssertionError("structural callees started")

        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "routes.py").write_text(
                "from flask import Flask\napp = Flask(__name__)\n\n"
                "def view():\n    pass\n\n"
                "app.add_url_rule('/view', view_func=view)\n"
            )
            lifecycle = FakeLifecycle()
            service = AtlasService(
                repository=repository,
                structural_provider=ForbiddenStructural(),
                lifecycle=lifecycle,
                registration_index=self._registration_index(repository),
            )
            with service:
                callers = service.query(QueryRequest(
                    "callers", "view", {
                        "target_path": "routes.py", "relation": "registers"
                    }
                ))
                callees = service.query(QueryRequest(
                    "callees", "registration@7", {
                        "target_path": "routes.py", "relation": "registers"
                    }
                ))
                empty = service.query(QueryRequest(
                    "callers", "absent", {"relation": "registers"}
                ))
            self.assertEqual(lifecycle.starts, 0)
            self.assertEqual(lifecycle.closes, 0)
            self.assertEqual([node.name for node in callers.nodes], ["registration@7"])
            self.assertEqual([node.name for node in callees.nodes], ["view"])
            self.assertFalse(empty.truncated)
            self.assertEqual(empty.nodes, ())

    def test_unavailable_scoped_registration_is_explicit_without_provider(self) -> None:
        class ForbiddenStructural(FakeImpactProvider):
            project = "p"

            def callers(self, *args, **kwargs):
                raise AssertionError("structural callers started")

        lifecycle = FakeLifecycle()
        service = AtlasService(
            structural_provider=ForbiddenStructural(), lifecycle=lifecycle
        )
        with service:
            response = service.query(QueryRequest(
                "callers", "view", {"relation": "registers"}
            ))
        self.assertEqual(lifecycle.starts, 0)
        self.assertTrue(response.truncated)
        self.assertIn(
            "registration_index_unavailable", response.truncation["reasons"]
        )

    def test_scoped_registration_answer_keeps_result_budgets(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "routes.py").write_text(
                "from flask import Flask\napp = Flask(__name__)\n\n"
                "def view():\n    pass\n\n"
                "app.add_url_rule('/one', view_func=view)\n"
                "app.add_url_rule('/two', view_func=view)\n"
            )
            service = AtlasService(
                registration_index=self._registration_index(repository)
            )
            with service:
                response = service.query(QueryRequest(
                    "callers", "view", {
                        "target_path": "routes.py",
                        "relation": "registers",
                        "max_nodes": 1,
                        "max_edges": 1,
                    }
                ))
        self.assertEqual(len(response.nodes), 1)
        self.assertTrue(response.truncated)
        self.assertIn("node_budget_exceeded", response.truncation["reasons"])

    def test_python_exact_scan_timeout_preserves_semantic_callers(self) -> None:
        class EmptyStructural(FakeImpactProvider):
            project = "p"

            def definitions(self, symbol, *, target_path="", target_owner=""):
                return (Node(
                    "p.target.target", "function", symbol,
                    SourceRange(target_path, 1, 2), "structural", 1.0, HASH,
                ),)

            def callers(self, *args, **kwargs):
                return ImpactTraversal(())

        class ExactSemantic(FakeSemanticProvider):
            def query(self, *args, **kwargs):
                return (Node(
                    "ref", "reference", "target",
                    SourceRange("target.py", 5, 5), "semantic", 1.0, HASH,
                ),)

        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "target.py").write_text(
                "def target():\n    pass\n\ndef caller():\n    target()\n"
            )
            structural = EmptyStructural()
            service = AtlasService(
                repository=repository,
                structural_provider=structural,
                semantic_provider=ExactSemantic(),
                impact_provider=structural,
            )
            with patch.object(
                service,
                "_python_exact_references",
                side_effect=TimeoutError,
            ):
                with service:
                    response = service.query(QueryRequest(
                        "callers", "target", {"target_path": "target.py"}
                    ))
        self.assertEqual([node.name for node in response.nodes], ["caller"])
        self.assertTrue(response.truncated)
        self.assertIn("time_budget_exceeded", response.truncation["reasons"])

    def test_reuses_successful_python_caller_supplement_within_session(self) -> None:
        class EmptyStructural(FakeImpactProvider):
            project = "p"

            def definitions(self, symbol, *, target_path="", target_owner=""):
                return (Node(
                    "p.target.target", "function", symbol,
                    SourceRange(target_path, 1, 2), "structural", 1.0, HASH,
                ),)

            def callers(self, *args, **kwargs):
                return ImpactTraversal(())

        class CountingSemantic(FakeSemanticProvider):
            queries = 0

            def query(self, *args, **kwargs):
                self.queries += 1
                return (Node(
                    "ref", "reference", "target",
                    SourceRange("target.py", 5, 5), "semantic", 1.0, HASH,
                ),)

        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "target.py").write_text(
                "def target():\n    pass\n\ndef caller():\n    target()\n"
            )
            structural = EmptyStructural()
            semantic = CountingSemantic()
            service = AtlasService(
                repository=repository,
                structural_provider=structural,
                semantic_provider=semantic,
                impact_provider=structural,
            )
            with service:
                request = QueryRequest(
                    "callers", "target", {"target_path": "target.py"}
                )
                first = service.query(request)
                second = service.query(request)
        self.assertEqual(semantic.queries, 1)
        self.assertEqual(first.nodes, second.nodes)

    def test_python_caller_overlaps_structural_and_semantic_startup(self) -> None:
        structural_entered = Event()
        semantic_entered = Event()

        class CoordinatedLifecycle(FakeLifecycle):
            overlapped = False

            def start(self, *, timeout_seconds=None):
                super().start(timeout_seconds=timeout_seconds)
                structural_entered.set()
                self.overlapped = semantic_entered.wait(1.0)
                if not self.overlapped:
                    raise RuntimeError("semantic startup did not overlap")

        class EmptyStructural(FakeImpactProvider):
            project = "p"

            def callers(self, *args, **kwargs):
                return ImpactTraversal(())

        class CoordinatedSemantic(FakeSemanticProvider):
            worker_name = ""

            def start(self, *, timeout_seconds=None):
                super().start(timeout_seconds=timeout_seconds)
                self.worker_name = current_thread().name
                semantic_entered.set()
                if not structural_entered.wait(1.0):
                    raise RuntimeError("structural startup did not overlap")

            def query(self, *args, **kwargs):
                return ()

        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "target.py").write_text("def target():\n    pass\n")
            lifecycle = CoordinatedLifecycle()
            semantic = CoordinatedSemantic()
            structural = EmptyStructural()
            service = AtlasService(
                repository=repository,
                structural_provider=structural,
                semantic_provider=semantic,
                impact_provider=structural,
                lifecycle=lifecycle,
            )
            with service:
                service.query(QueryRequest(
                    "callers", "target", {"target_path": "target.py"}
                ))
        self.assertTrue(lifecycle.overlapped)
        self.assertTrue(semantic.worker_name.startswith("atlas-python-evidence"))

    def test_parallel_semantic_timeout_preserves_structural_python_caller(self) -> None:
        class StructuralCaller(FakeImpactProvider):
            project = "p"

        class TimeoutSemantic(FakeSemanticProvider):
            def query(self, *args, **kwargs):
                raise TimeoutError("budget")

        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "target.py").write_text("def target():\n    pass\n")
            structural = StructuralCaller()
            service = AtlasService(
                repository=repository,
                structural_provider=structural,
                semantic_provider=TimeoutSemantic(),
                impact_provider=structural,
            )
            with service:
                response = service.query(QueryRequest(
                    "callers", "target", {"target_path": "target.py"}
                ))
        self.assertEqual([node.name for node in response.nodes], ["caller"])
        self.assertTrue(response.truncated)
        self.assertIn("time_budget_exceeded", response.truncation["reasons"])

    def test_structural_timeout_skips_post_deadline_python_merge(self) -> None:
        class TimedOutStructural(FakeImpactProvider):
            project = "p"
            definition_calls = 0

            def callers(self, *args, **kwargs):
                return ImpactTraversal(
                    (), True, ("time_budget_exceeded",), 1, 0
                )

            def definitions(self, *args, **kwargs):
                self.definition_calls += 1
                return super().definitions(*args, **kwargs)

        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "target.py").write_text("def target():\n    pass\n")
            structural = TimedOutStructural()
            service = AtlasService(
                repository=repository,
                structural_provider=structural,
                semantic_provider=FakeSemanticProvider(),
                impact_provider=structural,
            )
            with service:
                response = service.query(QueryRequest(
                    "callers", "target", {"target_path": "target.py"}
                ))
        self.assertTrue(response.truncated)
        self.assertEqual(structural.definition_calls, 0)

    def test_parallel_worker_is_closed_after_structural_query_error(self) -> None:
        class FailingStructural(FakeImpactProvider):
            project = "p"

            def callers(self, *args, **kwargs):
                raise RuntimeError("structural failure")

        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "target.py").write_text("def target():\n    pass\n")
            structural = FailingStructural()
            semantic = FakeSemanticProvider()
            service = AtlasService(
                repository=repository,
                structural_provider=structural,
                semantic_provider=semantic,
                impact_provider=structural,
            )
            with self.assertRaisesRegex(RuntimeError, "structural failure"):
                with service:
                    service.query(QueryRequest(
                        "callers", "target", {"target_path": "target.py"}
                    ))
        self.assertEqual((semantic.starts, semantic.closes), (1, 1))

    def test_python_callees_does_not_start_semantic_worker(self) -> None:
        class PythonStructural(FakeImpactProvider):
            project = "p"

        semantic = FakeSemanticProvider()
        structural = PythonStructural()
        service = AtlasService(
            repository=Path("/repo"),
            structural_provider=structural,
            semantic_provider=semantic,
            impact_provider=structural,
        )
        with service:
            service.query(QueryRequest(
                "callees", "target", {"target_path": "target.py"}
            ))
        self.assertEqual((semantic.starts, semantic.closes), (0, 0))

    def test_reuses_successful_python_reference_scan_within_service_session(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            source = repository / "target.py"
            source.write_text("def target():\n    pass\n")
            service = AtlasService(
                repository=repository,
                semantic_provider=FakeSemanticProvider(),
            )
            with patch.object(
                PythonExactReferenceProvider,
                "references",
                autospec=True,
                return_value=(),
            ) as references:
                with service:
                    request = QueryRequest(
                        "references", "target", {"target_path": "target.py"}
                    )
                    service.query(request)
                    service.query(request)
        self.assertEqual(references.call_count, 1)

    def test_python_reexport_references_work_without_semantic_provider(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            package = repository / "pkg"
            package.mkdir()
            (package / "__init__.py").write_text(
                "from .helpers import target as target\n"
            )
            (package / "helpers.py").write_text("def target():\n    pass\n")
            (repository / "consumer.py").write_text(
                "import pkg\n\ndef caller():\n    pkg.target()\n"
            )
            service = AtlasService(repository=repository)
            with service:
                response = service.query(QueryRequest(
                    "references", "target", {"target_path": "pkg/helpers.py"}
                ))
        self.assertEqual(
            [(node.location.path, node.location.start_line) for node in response.nodes],
            [("consumer.py", 4)],
        )
        self.assertEqual(response.nodes[0].provider, "atlas-python-references")

    def test_python_related_tests_adds_only_exact_test_callers(self) -> None:
        class EmptyStructural(FakeImpactProvider):
            project = "p"

            def definitions(self, symbol, *, target_path="", target_owner=""):
                return (Node(
                    "p.pkg.helpers.target", "function", symbol,
                    SourceRange(target_path, 1, 2), "structural", 1.0, HASH,
                ),)

            def related_tests(self, *args, **kwargs):
                return ImpactTraversal(())

        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            package = repository / "pkg"
            tests = repository / "tests"
            package.mkdir()
            tests.mkdir()
            (package / "__init__.py").write_text(
                "from .helpers import target as target\n"
            )
            (package / "helpers.py").write_text("def target():\n    pass\n")
            (repository / "consumer.py").write_text(
                "import pkg\n\ndef production():\n    pkg.target()\n"
            )
            (tests / "test_helpers.py").write_text(
                "import pkg\n\ndef test_target():\n    pkg.target()\n"
            )
            structural = EmptyStructural()
            service = AtlasService(
                repository=repository,
                structural_provider=structural,
                impact_provider=structural,
            )
            with service:
                response = service.query(QueryRequest(
                    "related_tests", "target", {"target_path": "pkg/helpers.py"}
                ))
        self.assertEqual([node.name for node in response.nodes], ["test_target"])

    def test_python_upstream_impact_adds_exact_reexport_caller(self) -> None:
        class EmptyStructural(FakeImpactProvider):
            project = "p"

            def definitions(self, symbol, *, target_path="", target_owner=""):
                return (Node(
                    "p.pkg.helpers.target", "function", symbol,
                    SourceRange(target_path, 1, 2), "structural", 1.0, HASH,
                ),)

            def impact(self, *args, **kwargs):
                return ImpactTraversal(())

        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            package = repository / "pkg"
            package.mkdir()
            (package / "__init__.py").write_text(
                "from .helpers import target as target\n"
            )
            (package / "helpers.py").write_text("def target():\n    pass\n")
            (repository / "consumer.py").write_text(
                "import pkg\n\ndef caller():\n    pkg.target()\n"
            )
            structural = EmptyStructural()
            service = AtlasService(
                repository=repository,
                structural_provider=structural,
                impact_provider=structural,
            )
            with service:
                response = service.query(QueryRequest(
                    "impact", "target", {
                        "target_path": "pkg/helpers.py",
                        "direction": "upstream",
                        "depth": 1,
                    }
                ))
        self.assertEqual([node.name for node in response.nodes], ["caller"])
        self.assertEqual(response.edges[0].resolution, "exact")

    def test_falls_back_from_empty_python_callers_to_exact_semantic_ast_identity(self) -> None:
        class EmptyStructural(FakeImpactProvider):
            project = "p"

            def definitions(self, symbol, *, target_path="", target_owner=""):
                return (Node(
                    "p.src.x.target", "function", symbol,
                    SourceRange(target_path, 1, 2), "structural", 1.0, HASH,
                ),)

            def callers(self, *args, **kwargs):
                return ImpactTraversal(())

        class ExactSemantic(FakeSemanticProvider):
            def query(self, *args, **kwargs):
                return (Node(
                    "reference", "reference", "target",
                    SourceRange("src/x.py", 5, 5), "semantic", 1.0, HASH,
                ),)

        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            source = repository / "src/x.py"
            source.parent.mkdir(parents=True)
            source.write_text("def target():\n    pass\n\ndef caller():\n    target()\n")
            structural = EmptyStructural()
            service = AtlasService(
                repository=repository,
                structural_provider=structural,
                semantic_provider=ExactSemantic(),
                impact_provider=structural,
            )
            with service:
                response = service.query(QueryRequest(
                    "callers", "target", {"target_path": "src/x.py"}
                ))
        self.assertEqual([node.id for node in response.nodes], ["p.src.x.caller"])
        self.assertEqual(response.edges[0].provider, "atlas-python-exact-callers")
        self.assertEqual(response.edges[0].resolution, "exact")

    def test_supplements_partial_python_callers_and_deduplicates_identity(self) -> None:
        class PartialStructural(FakeImpactProvider):
            project = "p"

            def definitions(self, symbol, *, target_path="", target_owner=""):
                return (Node(
                    "p.src.x.target", "function", symbol,
                    SourceRange(target_path, 1, 2), "structural", 1.0, HASH,
                ),)

            def callers(self, *args, **kwargs):
                caller = Node(
                    "p.src.x.first", "function", "first",
                    SourceRange("src/x.py", 4, 5), "structural", 1.0, HASH,
                )
                edge = Edge(
                    caller.id, "p.src.x.target", "calls", "structural", 1.0, HASH
                )
                return ImpactTraversal((ImpactHit(caller, 1, (edge,)),))

        class ExactSemantic(FakeSemanticProvider):
            def query(self, *args, **kwargs):
                return (
                    Node("ref-1", "reference", "target", SourceRange("src/x.py", 5, 5), "semantic", 1.0, HASH),
                    Node("ref-2", "reference", "target", SourceRange("src/x.py", 8, 8), "semantic", 1.0, HASH),
                )

        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            source = repository / "src/x.py"
            source.parent.mkdir(parents=True)
            source.write_text(
                "def target():\n    pass\n\ndef first():\n    target()\n\n"
                "def second():\n    target()\n"
            )
            structural = PartialStructural()
            service = AtlasService(
                repository=repository,
                structural_provider=structural,
                semantic_provider=ExactSemantic(),
                impact_provider=structural,
            )
            with service:
                response = service.query(QueryRequest(
                    "callers", "target", {"target_path": "src/x.py"}
                ))
        self.assertEqual(
            [node.id for node in response.nodes],
            ["p.src.x.first", "p.src.x.second"],
        )
        self.assertEqual(
            [edge.provider for edge in response.edges],
            ["structural", "atlas-python-exact-callers"],
        )

    def test_routes_all_six_query_types(self) -> None:
        structural = FakeImpactProvider()
        service = AtlasService(
            repository=Path(__file__).resolve().parents[1] / "fixtures/ts-tests",
            structural_provider=structural,
            semantic_provider=FakeSemanticProvider(),
            test_provider=FakeTestProvider(),
            impact_provider=structural,
        )
        with service:
            responses = {
                query_type: service.query(
                    QueryRequest(
                        query_type,
                        "target",
                        {"target_path": "src/x.ts", "depth": 1} if query_type == "impact" else {},
                    )
                )
                for query_type in (
                    "definition",
                    "references",
                    "callers",
                    "callees",
                    "related_tests",
                    "impact",
                )
            }
        self.assertTrue(all(response.nodes for response in responses.values()))
        self.assertEqual(responses["references"].nodes[0].kind, "references")
        self.assertEqual(responses["related_tests"].nodes[0].kind, "test")
        self.assertTrue(responses["impact"].paths["caller"])

    def test_forwards_same_file_owner_selector(self) -> None:
        class CapturingProvider(FakeImpactProvider):
            owner = ""

            def definitions(self, symbol, *, target_path="", target_owner=""):
                self.owner = target_owner
                return super().definitions(
                    symbol, target_path=target_path, target_owner=target_owner
                )

        provider = CapturingProvider()
        service = AtlasService(structural_provider=provider)
        with service:
            response = service.query(QueryRequest(
                "definition",
                "run",
                {"target_path": "src/members.ts", "target_owner": "PrimaryWorker"},
            ))
        self.assertEqual(provider.owner, "PrimaryWorker")
        self.assertEqual(len(response.nodes), 1)

    def test_reuses_one_lifecycle_for_multiple_queries(self) -> None:
        lifecycle = FakeLifecycle()
        service = AtlasService(impact_provider=FakeImpactProvider(), lifecycle=lifecycle)
        with service:
            first = service.query(QueryRequest("impact", "target", {"depth": 2}))
            second = service.query(QueryRequest("impact", "target", {"depth": 2}))
        self.assertEqual(first.nodes, second.nodes)
        self.assertEqual((lifecycle.starts, lifecycle.closes), (1, 1))

    def test_starts_only_provider_required_by_query(self) -> None:
        lifecycle = FakeLifecycle()
        semantic = FakeSemanticProvider()
        structural = FakeImpactProvider()
        service = AtlasService(
            structural_provider=structural,
            impact_provider=structural,
            semantic_provider=semantic,
            lifecycle=lifecycle,
        )
        with service:
            service.query(QueryRequest("definition", "target"))
            self.assertEqual((lifecycle.starts, semantic.starts), (1, 0))
        self.assertEqual((lifecycle.closes, semantic.closes), (1, 0))

        lifecycle = FakeLifecycle()
        semantic = FakeSemanticProvider()
        service = AtlasService(semantic_provider=semantic, lifecycle=lifecycle)
        with service:
            service.query(QueryRequest("references", "target"))
            self.assertEqual((lifecycle.starts, semantic.starts), (0, 1))
        self.assertEqual((lifecycle.closes, semantic.closes), (0, 1))

    def test_nonempty_ts_compiler_references_skip_semantic_provider(self) -> None:
        class ExactTsReferences(FakeTestProvider):
            def references(self, _repository, symbol, **_options):
                return (Node(
                    "ts-reference", "references", symbol,
                    SourceRange("src/x.ts", 7, 7),
                    "atlas-ts-references", 1.0, HASH,
                ),)

        semantic = FakeSemanticProvider()
        service = AtlasService(
            repository=Path(__file__).resolve().parents[1] / "fixtures/ts-tests",
            semantic_provider=semantic,
            test_provider=ExactTsReferences(),
        )
        with service:
            response = service.query(QueryRequest("references", "target"))
            self.assertEqual(semantic.starts, 0)
        self.assertEqual([node.provider for node in response.nodes], ["atlas-ts-references"])
        self.assertEqual(semantic.closes, 0)

    def test_ts_continuation_pages_are_exact_replayable_and_reused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "tsconfig.json").write_text("{}")
            provider = CountingTsReferences()
            service = AtlasService(
                repository=repository,
                test_provider=provider,
                session_continuations=True,
            )
            parameters = {
                "target_path": "src/target.ts",
                "target_owner": "Owner",
                "max_nodes": 2,
            }
            with patch(
                "codebase_atlas.service.repository_snapshot",
                return_value=source_snapshot(),
            ):
                with service:
                    first = service.query(QueryRequest(
                        "references", "target", parameters
                    ))
                    second_token = first.truncation["continuation"]
                    second = service.query(QueryRequest(
                        "references", "target", {
                            **parameters,
                            "max_nodes": 1,
                            "continuation": second_token,
                        }
                    ))
                    replay = service.query(QueryRequest(
                        "references", "target", {
                            **parameters,
                            "max_nodes": 1,
                            "continuation": second_token,
                        }
                    ))
                    final = service.query(QueryRequest(
                        "references", "target", {
                            **parameters,
                            "max_nodes": 10,
                            "continuation": second.truncation["continuation"],
                        }
                    ))
                    wider_first = service.query(QueryRequest(
                        "references", "target", {**parameters, "max_nodes": 3}
                    ))
            self.assertEqual(provider.reference_calls, 1)
            self.assertEqual(
                [node.id for node in (*first.nodes, *second.nodes, *final.nodes)],
                [f"ts-reference-{index}" for index in range(5)],
            )
            self.assertEqual(second.nodes, replay.nodes)
            self.assertEqual(
                [node.id for node in wider_first.nodes],
                ["ts-reference-0", "ts-reference-1", "ts-reference-2"],
            )
            self.assertEqual(first.truncation["page"], {
                "offset": 0, "next_offset": 2, "total_nodes": 5,
            })
            self.assertFalse(final.truncated)
            self.assertIsNone(final.truncation["continuation"])
            self.assertFalse(final.truncation["resumable"])

    def test_ts_continuation_is_disabled_by_default_and_skips_narrow_answers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "tsconfig.json").write_text("{}")
            broad = CountingTsReferences()
            service = AtlasService(repository=repository, test_provider=broad)
            with service:
                response = service.query(QueryRequest(
                    "references", "target", {"max_nodes": 1}
                ))
            self.assertIsNone(response.truncation["continuation"])
            self.assertFalse(response.truncation["resumable"])

            narrow = CountingTsReferences(count=1)
            service = AtlasService(
                repository=repository,
                test_provider=narrow,
                session_continuations=True,
            )
            with patch(
                "codebase_atlas.service.repository_snapshot",
                return_value=source_snapshot(),
            ):
                with service:
                    first = service.query(QueryRequest(
                        "references", "target", {"max_nodes": 2}
                    ))
                    service.query(QueryRequest(
                        "references", "target", {"max_nodes": 2}
                    ))
            self.assertFalse(first.truncated)
            self.assertIsNone(first.truncation["continuation"])
            self.assertEqual(narrow.reference_calls, 2)

    def test_ts_continuation_rejects_tamper_mismatch_stale_and_prior_session(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "tsconfig.json").write_text("{}")
            provider = CountingTsReferences()
            service = AtlasService(
                repository=repository,
                test_provider=provider,
                session_continuations=True,
            )
            fingerprint = ["source-a"]
            with patch(
                "codebase_atlas.service.repository_snapshot",
                side_effect=lambda _repository: source_snapshot(fingerprint[0]),
            ):
                service.start()
                first = service.query(QueryRequest(
                    "references", "target", {"max_nodes": 1}
                ))
                token = first.truncation["continuation"]
                replacement = "A" if token[-1] != "A" else "B"
                with self.assertRaisesRegex(ValueError, "invalid_continuation"):
                    service.query(QueryRequest(
                        "references", "target", {
                            "max_nodes": 1,
                            "continuation": token[:-1] + replacement,
                        },
                    ))
                with self.assertRaisesRegex(
                    ValueError, "continuation_query_mismatch"
                ):
                    service.query(QueryRequest(
                        "references", "different", {
                            "max_nodes": 1, "continuation": token,
                        },
                    ))
                fingerprint[0] = "source-b"
                with self.assertRaisesRegex(ValueError, "continuation_stale"):
                    service.query(QueryRequest(
                        "references", "target", {
                            "max_nodes": 1, "continuation": token,
                        },
                    ))
                with self.assertRaisesRegex(ValueError, "continuation_unavailable"):
                    service.query(QueryRequest(
                        "references", "target", {
                            "max_nodes": 1, "continuation": token,
                        },
                    ))
                fingerprint[0] = "source-a"
                replacement_first = service.query(QueryRequest(
                    "references", "target", {"max_nodes": 1}
                ))
                prior_session_token = replacement_first.truncation["continuation"]
                service.close()
                service.start()
                with self.assertRaisesRegex(ValueError, "invalid_continuation"):
                    service.query(QueryRequest(
                        "references", "target", {
                            "max_nodes": 1,
                            "continuation": prior_session_token,
                        },
                    ))
                service.close()

    def test_ts_continuation_source_unavailable_and_validation_timeout_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "tsconfig.json").write_text("{}")
            service = AtlasService(
                repository=repository,
                test_provider=CountingTsReferences(),
                session_continuations=True,
            )
            fingerprint = ["source-a"]
            with patch(
                "codebase_atlas.service.repository_snapshot",
                side_effect=lambda _repository: source_snapshot(fingerprint[0]),
            ):
                with service:
                    first = service.query(QueryRequest(
                        "references", "target", {"max_nodes": 1}
                    ))
                    token = first.truncation["continuation"]
                    with patch(
                        "codebase_atlas.service.monotonic",
                        side_effect=(0.0, 0.01, 0.011),
                    ):
                        timed = service.query(QueryRequest(
                            "references", "target", {
                                "max_nodes": 1,
                                "timeout_ms": 1,
                                "continuation": token,
                            },
                        ))
                    self.assertEqual(timed.nodes, ())
                    self.assertEqual(
                        timed.truncation["reasons"], ("time_budget_exceeded",)
                    )
                    self.assertEqual(timed.truncation["continuation"], token)
                    self.assertTrue(timed.truncation["resumable"])
                    fingerprint[0] = None
                    with self.assertRaisesRegex(
                        ValueError, "continuation_unavailable"
                    ):
                        service.query(QueryRequest(
                            "references", "target", {
                                "max_nodes": 1, "continuation": token,
                            },
                        ))

    def test_ts_continuation_memory_limits_lru_and_close_cleanup(self) -> None:
        provider = CountingTsReferences(count=2)
        service = AtlasService(
            test_provider=provider, session_continuations=True
        )
        service.start()
        nodes = provider.references(Path("/repo"), "target")
        probe = service._store_ts_continuation(("probe",), "source", nodes)
        self.assertIsNotNone(probe)
        weight = probe.weight
        service._clear_ts_continuations()
        with patch("codebase_atlas.service.MAX_CONTINUATION_CACHE_ENTRIES", 2):
            first = service._store_ts_continuation(("first",), "source", nodes)
            second = service._store_ts_continuation(("second",), "source", nodes)
            service._ts_continuation_cache.move_to_end(first.entry_id)
            third = service._store_ts_continuation(("third",), "source", nodes)
        self.assertIn(first.entry_id, service._ts_continuation_cache)
        self.assertNotIn(second.entry_id, service._ts_continuation_cache)
        self.assertIn(third.entry_id, service._ts_continuation_cache)
        self.assertEqual(
            service._ts_continuation_bytes,
            sum(entry.weight for entry in service._ts_continuation_cache.values()),
        )
        service._clear_ts_continuations()
        with patch(
            "codebase_atlas.service.MAX_CONTINUATION_CACHE_BYTES",
            weight * 2 - 1,
        ):
            service._store_ts_continuation(("first",), "source", nodes)
            service._store_ts_continuation(("second",), "source", nodes)
        self.assertEqual(len(service._ts_continuation_cache), 1)
        service.close()
        self.assertEqual(service._ts_continuation_bytes, 0)
        self.assertEqual(service._ts_continuation_cache, {})
        self.assertEqual(service._ts_continuation_queries, {})
        self.assertIsNone(service._continuation_secret)

    def test_ts_continuation_rejects_oversized_and_timed_out_answers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "tsconfig.json").write_text("{}")
            provider = CountingTsReferences()
            service = AtlasService(
                repository=repository,
                test_provider=provider,
                session_continuations=True,
            )
            with patch(
                "codebase_atlas.service.repository_snapshot",
                return_value=source_snapshot(),
            ), patch("codebase_atlas.service.MAX_CONTINUATION_ENTRY_BYTES", 1):
                with service:
                    response = service.query(QueryRequest(
                        "references", "target", {"max_nodes": 1}
                    ))
            self.assertIsNone(response.truncation["continuation"])
            self.assertEqual(
                response.truncation["continuation_unavailable_reason"],
                "entry_too_large",
            )

            timeout_provider = CountingTsReferences(timeout=True)
            service = AtlasService(
                repository=repository,
                test_provider=timeout_provider,
                session_continuations=True,
            )
            with patch(
                "codebase_atlas.service.repository_snapshot",
                return_value=source_snapshot(),
            ):
                with service:
                    timed = service.query(QueryRequest(
                        "references", "target", {"max_nodes": 1}
                    ))
            self.assertTrue(timed.truncated)
            self.assertIsNone(timed.truncation["continuation"])
            self.assertEqual(service._ts_continuation_cache, {})

    def test_empty_ts_compiler_references_fall_back_to_semantic_provider(self) -> None:
        class EmptyTsReferences(FakeTestProvider):
            def references(self, _repository, _symbol, **_options):
                return ()

        semantic = FakeSemanticProvider()
        service = AtlasService(
            repository=Path(__file__).resolve().parents[1] / "fixtures/ts-tests",
            semantic_provider=semantic,
            test_provider=EmptyTsReferences(),
        )
        with service:
            response = service.query(QueryRequest("references", "target"))
            self.assertEqual(semantic.starts, 1)
        self.assertEqual([node.provider for node in response.nodes], ["semantic", "semantic"])
        self.assertEqual(semantic.closes, 1)

    def test_ts_compiler_reference_timeout_does_not_start_semantic_provider(self) -> None:
        class TimeoutTsReferences(FakeTestProvider):
            def references(self, _repository, _symbol, **_options):
                raise TimeoutError("budget")

        semantic = FakeSemanticProvider()
        service = AtlasService(
            repository=Path(__file__).resolve().parents[1] / "fixtures/ts-tests",
            semantic_provider=semantic,
            test_provider=TimeoutTsReferences(),
        )
        with service:
            response = service.query(QueryRequest("references", "target"))
        self.assertTrue(response.truncated)
        self.assertEqual(response.truncation["reasons"], ("time_budget_exceeded",))
        self.assertEqual((semantic.starts, semantic.closes), (0, 0))

    def test_returns_explicit_truncation_when_semantic_provider_times_out(self) -> None:
        class TimeoutSemantic(FakeSemanticProvider):
            def query(self, *args, **kwargs):
                raise TimeoutError("budget")

        service = AtlasService(semantic_provider=TimeoutSemantic())
        with service:
            response = service.query(QueryRequest(
                "references", "target", {"timeout_ms": 1000}
            ))
        self.assertTrue(response.truncated)
        self.assertEqual(response.truncation["reasons"], ("time_budget_exceeded",))

    def test_reuses_successful_python_reference_answer_with_new_result_budget(self) -> None:
        class CountingSemantic(FakeSemanticProvider):
            calls = 0

            def query(self, *args, **kwargs):
                self.calls += 1
                return super().query(*args, **kwargs)

        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "target.py").write_text("def target():\n    pass\n")
            semantic = CountingSemantic()
            service = AtlasService(repository=repository, semantic_provider=semantic)
            with service:
                first = service.query(QueryRequest(
                    "references", "target",
                    {"target_path": "target.py", "max_nodes": 1},
                ))
                second = service.query(QueryRequest(
                    "references", "target",
                    {"target_path": "target.py", "max_nodes": 2},
                ))
        self.assertEqual(semantic.calls, 1)
        self.assertEqual((len(first.nodes), len(second.nodes)), (1, 2))
        self.assertTrue(first.truncated)
        self.assertFalse(second.truncated)

    def test_does_not_cache_timed_out_python_reference_answer(self) -> None:
        class FlakySemantic(FakeSemanticProvider):
            calls = 0

            def query(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise TimeoutError("budget")
                return super().query(*args, **kwargs)

        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "target.py").write_text("def target():\n    pass\n")
            semantic = FlakySemantic()
            service = AtlasService(repository=repository, semantic_provider=semantic)
            with service:
                first = service.query(QueryRequest(
                    "references", "target", {"target_path": "target.py"}
                ))
                second = service.query(QueryRequest(
                    "references", "target", {"target_path": "target.py"}
                ))
        self.assertTrue(first.truncated)
        self.assertFalse(second.truncated)
        self.assertEqual(semantic.calls, 2)

    def test_python_session_caches_are_bounded_and_cleared_on_close(self) -> None:
        service = AtlasService()
        for index in range(129):
            service._cache_put(
                service._python_complete_reference_cache,
                (Path("/repo"), f"symbol-{index}", "target.py", ""),
                (),
            )
        self.assertEqual(len(service._python_complete_reference_cache), 128)
        service.started = True
        service.close()
        self.assertEqual(len(service._python_complete_reference_cache), 0)

    def test_requires_started_service(self) -> None:
        service = AtlasService(impact_provider=FakeImpactProvider())
        with self.assertRaisesRegex(RuntimeError, "start"):
            service.query(QueryRequest("impact", "target"))

    def test_applies_result_budget_and_reports_explicit_truncation(self) -> None:
        service = AtlasService(semantic_provider=FakeSemanticProvider())
        with service:
            response = service.query(QueryRequest(
                "references", "target", {"max_nodes": 1, "max_edges": 2, "timeout_ms": 1000}
            ))
        self.assertEqual(len(response.nodes), 1)
        self.assertTrue(response.truncated)
        self.assertEqual(response.truncation["reasons"], ("node_budget_exceeded",))
        self.assertEqual(response.truncation["observed"]["nodes"], 2)
        self.assertEqual(response.truncation["returned"]["nodes"], 1)
        self.assertIsNone(response.truncation["continuation"])
        self.assertFalse(response.truncation["resumable"])

    def test_preserves_provider_side_time_truncation(self) -> None:
        class TruncatedProvider(FakeImpactProvider):
            def impact(self, *args, **kwargs):
                hits = super().impact(*args, **kwargs)
                return ImpactTraversal(
                    hits, True, ("time_budget_exceeded",), 1, 1
                )

        provider = TruncatedProvider()
        service = AtlasService(impact_provider=provider)
        with service:
            response = service.query(QueryRequest("impact", "target"))
        self.assertTrue(response.truncated)
        self.assertIn("time_budget_exceeded", response.truncation["reasons"])

    def test_skips_ts_test_augmentation_after_graph_timeout(self) -> None:
        class TruncatedProvider(FakeImpactProvider):
            def impact(self, *args, **kwargs):
                return ImpactTraversal(
                    (), True, ("time_budget_exceeded",), 1, 0
                )

        tests = FakeTestProvider()
        service = AtlasService(
            repository=Path(__file__).resolve().parents[1] / "fixtures/ts-tests",
            impact_provider=TruncatedProvider(),
            test_provider=tests,
        )
        with service:
            response = service.query(QueryRequest("impact", "run"))
        self.assertEqual(tests.calls, 0)
        self.assertIn("time_budget_exceeded", response.truncation["reasons"])

    def test_returns_explicit_truncation_when_ts_test_provider_times_out(self) -> None:
        class TimeoutTests(FakeTestProvider):
            def related_tests(self, *args, **kwargs):
                raise TimeoutError("budget")

        service = AtlasService(
            repository=Path(__file__).resolve().parents[1] / "fixtures/ts-tests",
            test_provider=TimeoutTests(),
        )
        with service:
            response = service.query(QueryRequest("related_tests", "run"))
        self.assertTrue(response.truncated)
        self.assertEqual(response.truncation["reasons"], ("time_budget_exceeded",))

    def test_rejects_invalid_query_budget(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_nodes"):
            QueryRequest("impact", "target", {"max_nodes": 0})

    def test_rejects_invalid_continuation_contract(self) -> None:
        for query_type, value in (
            ("definition", "token"),
            ("references", ""),
            ("references", 42),
            ("references", "x" * 513),
        ):
            with self.subTest(query_type=query_type, value_type=type(value)):
                with self.assertRaisesRegex(ValueError, "invalid_continuation"):
                    QueryRequest(query_type, "target", {"continuation": value})

    def test_rejects_non_string_owner_selector(self) -> None:
        with self.assertRaisesRegex(ValueError, "target_owner"):
            QueryRequest("definition", "target", {"target_owner": 42})

    def test_rejects_invalid_relation_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "relation"):
            QueryRequest("callers", "target", {"relation": "calls"})
        with self.assertRaisesRegex(ValueError, "only for callers"):
            QueryRequest("impact", "target", {"relation": "registers"})


if __name__ == "__main__":
    unittest.main()
