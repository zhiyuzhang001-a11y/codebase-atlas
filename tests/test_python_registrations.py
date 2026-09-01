from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codebase_atlas.contracts import Node, SourceRange
from codebase_atlas.providers.python_registrations import PythonRegistrationProvider


HASH = "a" * 64


def seed(name: str, path: str, line: int, owner: str = "") -> Node:
    return Node(
        f"p.{path.replace('/', '.').removesuffix('.py')}.{owner or name}",
        "function", name, SourceRange(path, line, line), "structural", 1.0, HASH,
    )


class PythonRegistrationTests(unittest.TestCase):
    def test_resolves_exact_explicit_and_decorator_registrations(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "routes.py").write_text(
                "from flask import Flask\n"
                "from fastapi import FastAPI\n\n"
                "flask_app = Flask(__name__)\n"
                "fastapi_app = FastAPI()\n\n"
                "def first():\n    pass\n\n"
                "flask_app.add_url_rule('/first', view_func=first)\n\n"
                "@flask_app.route('/second')\n"
                "def second():\n    pass\n\n"
                "def configure():\n"
                "    async def endpoint():\n        pass\n"
                "    fastapi_app.add_api_route('/endpoint', endpoint)\n"
            )
            index = PythonRegistrationProvider(repository, "p").scan()
            first = index.callers(seed("first", "routes.py", 7))
            second = index.callers(seed("second", "routes.py", 13))
            endpoint = index.callers(seed("endpoint", "routes.py", 17, "configure.endpoint"))
            second_without_structural = index.callers_for(
                "second", target_path="routes.py", target_owner="second"
            )
        self.assertEqual(first[0].node.name, "registration@10")
        self.assertEqual(second[0].node.name, "registration@12")
        self.assertEqual(endpoint[0].node.name, "configure")
        self.assertEqual(second_without_structural[0].node.name, "registration@12")
        self.assertEqual(
            [first[0].path[0].relation, second[0].path[0].relation, endpoint[0].path[0].relation],
            ["registers", "registers", "registers"],
        )

    def test_resolves_imported_function_registration_and_inverse_callees(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            package = repository / "pkg"
            package.mkdir()
            (package / "__init__.py").write_text("")
            (package / "views.py").write_text("def view(request):\n    pass\n")
            (package / "urls.py").write_text(
                "from django.urls import path\nfrom .views import view\n"
                "urlpatterns = [path('x/', view)]\n"
            )
            index = PythonRegistrationProvider(repository, "p").scan()
            callers = index.callers(seed("view", "pkg/views.py", 1))
            callees = index.callees("registration@3", target_path="pkg/urls.py")
        self.assertEqual([hit.node.name for hit in callers], ["registration@3"])
        self.assertEqual([hit.node.name for hit in callees], ["view"])

    def test_rejects_unresolved_names_strings_calls_and_rebinding(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "negative.py").write_text(
                "from flask import Flask\napp = Flask(__name__)\n\n"
                "def target():\n    pass\n\n"
                "def factory():\n    return target\n\n"
                "unknown.add_url_rule('/', view_func=target)\n"
                "app.add_url_rule('/string', view_func='target')\n"
                "app.add_url_rule('/call', view_func=factory())\n"
                "app = object()\n"
                "app.add_url_rule('/rebound', view_func=target)\n"
                "@unknown.route('/decorator')\n"
                "def decorated():\n    pass\n"
            )
            index = PythonRegistrationProvider(repository, "p").scan()
        self.assertEqual(index.registrations, ())

    def test_same_name_targets_are_distinguished_by_range_and_owner(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "same.py").write_text(
                "from flask import Flask\n\n"
                "def one(app: Flask):\n"
                "    def view():\n        pass\n"
                "    app.add_url_rule('/one', view_func=view)\n\n"
                "def two(app: Flask):\n"
                "    def view():\n        pass\n"
                "    app.add_url_rule('/two', view_func=view)\n"
            )
            index = PythonRegistrationProvider(repository, "p").scan()
            one = index.callers(seed("view", "same.py", 4, "one.view"))
            two = index.callers(seed("view", "same.py", 9, "two.view"))
        self.assertEqual([hit.node.name for hit in one], ["one"])
        self.assertEqual([hit.node.name for hit in two], ["two"])

    def test_timeout_is_explicit_and_source_fingerprint_changes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            source = repository / "x.py"
            source.write_text("def x():\n    pass\n")
            provider = PythonRegistrationProvider(repository, "p")
            before = provider.source_fingerprint()
            signature_before = provider.source_signature()
            source.write_text("def x():\n    return 1\n")
            after = provider.source_fingerprint()
            signature_after = provider.source_signature()
            self.assertNotEqual(before, after)
            self.assertNotEqual(signature_before, signature_after)
            (repository / "added.py").write_text("def added():\n    pass\n")
            self.assertNotEqual(signature_after, provider.source_signature())
            with self.assertRaises(TimeoutError):
                provider.scan(timeout_ms=0)

    def test_refreshes_inventory_after_file_addition(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "first.py").write_text("def first():\n    pass\n")
            provider = PythonRegistrationProvider(repository, "p")
            before = provider.source_signature()
            (repository / "second.py").write_text("def second():\n    pass\n")
            after = provider.source_signature()
            self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
