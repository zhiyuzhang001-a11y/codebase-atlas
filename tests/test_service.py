from __future__ import annotations

import unittest
from pathlib import Path
import tempfile
from threading import Event, current_thread
from unittest.mock import patch

from codebase_atlas.contracts import Edge, Node, SourceRange
from codebase_atlas.graph import ImpactHit, ImpactTraversal
from codebase_atlas.providers.python_references import PythonExactReferenceProvider
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


class ServiceTests(unittest.TestCase):
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
            )
            request = {"target_path": "tests/test_routes.py", "target_owner": "test_register.callback"}
            with service:
                related = service.query(QueryRequest("related_tests", "callback", request))
                impact = service.query(QueryRequest("impact", "callback", request))
        self.assertEqual([node.name for node in related.nodes], ["test_register"])
        self.assertEqual([edge.relation for edge in related.edges], ["registers"])
        self.assertEqual([node.name for node in impact.nodes], ["test_register"])

    def test_registration_cache_invalidates_on_source_change_and_clears(self) -> None:
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
            )
            request = QueryRequest("callers", "view", {"target_path": "routes.py"})
            with service:
                first = service.query(request)
                source.write_text(source.read_text() + "app.add_url_rule('/two', view_func=view)\n")
                second = service.query(request)
                self.assertEqual(len(service._python_registration_cache), 2)
                self.assertEqual(len(service._python_registration_signature_cache), 2)
            self.assertEqual(len(service._python_registration_cache), 0)
            self.assertEqual(len(service._python_registration_signature_cache), 0)
            self.assertEqual(len(service._python_registration_provider_cache), 0)
        self.assertEqual(len(first.edges), 1)
        self.assertEqual(len(second.edges), 2)

    def test_registration_timeout_preserves_structural_result(self) -> None:
        structural = FakeImpactProvider()
        structural.project = "p"
        service = AtlasService(
            repository=Path(__file__).resolve().parent,
            structural_provider=structural,
            impact_provider=structural,
        )
        with patch(
            "codebase_atlas.service.PythonRegistrationProvider.source_fingerprint",
            side_effect=TimeoutError("budget"),
        ):
            with service:
                response = service.query(QueryRequest(
                    "callers", "target", {"target_path": "src/x.py"}
                ))
        self.assertEqual([node.name for node in response.nodes], ["caller"])
        self.assertTrue(response.truncated)
        self.assertIn("time_budget_exceeded", response.truncation["reasons"])

    def test_registration_scan_error_is_not_cached(self) -> None:
        structural = FakeImpactProvider()
        structural.project = "p"
        service = AtlasService(
            repository=Path(__file__).resolve().parent,
            structural_provider=structural,
            impact_provider=structural,
        )
        with patch(
            "codebase_atlas.service.PythonRegistrationProvider.scan",
            side_effect=RuntimeError("scan failed"),
        ):
            with service:
                with self.assertRaisesRegex(RuntimeError, "scan failed"):
                    service.query(QueryRequest(
                        "callers", "target", {"target_path": "src/x.py"}
                    ))
                self.assertEqual(len(service._python_registration_cache), 0)
                self.assertEqual(
                    len(service._python_registration_signature_cache), 0
                )

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
        for index in range(129):
            service._cache_put(
                service._python_registration_cache,
                (Path("/repo"), "p", f"fingerprint-{index}"),
                (),
            )
        self.assertEqual(len(service._python_registration_cache), 128)
        service.started = True
        service.close()
        self.assertEqual(len(service._python_complete_reference_cache), 0)
        self.assertEqual(len(service._python_registration_cache), 0)

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

    def test_rejects_non_string_owner_selector(self) -> None:
        with self.assertRaisesRegex(ValueError, "target_owner"):
            QueryRequest("definition", "target", {"target_owner": 42})


if __name__ == "__main__":
    unittest.main()
