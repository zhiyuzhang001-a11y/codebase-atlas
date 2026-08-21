import tempfile
from pathlib import Path
import unittest

from codebase_atlas.providers.python_references import PythonExactReferenceProvider


class PythonExactReferenceTests(unittest.TestCase):
    def test_resolves_package_reexport_and_module_alias_without_same_name_decoy(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "pkg").mkdir()
            (root / "pkg/__init__.py").write_text(
                "from .helpers import target as target\n", encoding="utf-8"
            )
            (root / "pkg/helpers.py").write_text(
                "def target():\n    return 1\n", encoding="utf-8"
            )
            (root / "test_usage.py").write_text(
                "import pkg\n\n"
                "def test_exact():\n    return pkg.target()\n\n"
                "def test_decoy(target):\n    return target()\n",
                encoding="utf-8",
            )
            rows = PythonExactReferenceProvider(root).references(
                "target", target_path="pkg/helpers.py", timeout_ms=1000
            )
            self.assertEqual([(row.location.path, row.location.start_line) for row in rows], [
                ("test_usage.py", 4),
            ])

    def test_resolves_imported_submodule_attribute_and_honors_local_shadow(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "pkg").mkdir()
            (root / "pkg/__init__.py").write_text("", encoding="utf-8")
            (root / "pkg/text.py").write_text(
                "def split():\n    return []\n", encoding="utf-8"
            )
            (root / "tests.py").write_text(
                "from pkg import text\n\n"
                "def test_exact():\n    return text.split()\n\n"
                "def test_shadow():\n    text = object()\n    return text.split()\n",
                encoding="utf-8",
            )
            rows = PythonExactReferenceProvider(root).references(
                "split", target_path="pkg/text.py", timeout_ms=1000
            )
            self.assertEqual([(row.location.path, row.location.start_line) for row in rows], [
                ("tests.py", 4),
            ])
