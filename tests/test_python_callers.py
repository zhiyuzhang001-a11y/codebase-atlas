from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codebase_atlas.contracts import Node, SourceRange
from codebase_atlas.providers.python_callers import PythonExactCallerProvider


HASH = "e" * 64


class PythonExactCallerTests(unittest.TestCase):
    def test_maps_exact_reference_to_smallest_enclosing_caller(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            source = repository / "src/example.py"
            source.parent.mkdir(parents=True)
            source.write_text(
                "def target():\n    pass\n\nclass Worker:\n"
                "    def run(self):\n        target()\n\ntarget_name = 'target'\n"
            )
            seed = Node(
                "p.src.example.target", "function", "target",
                SourceRange("src/example.py", 1, 2), "structural", 1.0, HASH,
            )
            reference = Node(
                "ref", "reference", "target", SourceRange("src/example.py", 6, 6),
                "semantic", 1.0, HASH,
            )
            result = PythonExactCallerProvider(repository, "p").callers(
                seed, (reference,)
            )
            self.assertEqual([hit.node.id for hit in result], ["p.src.example.Worker.run"])
            self.assertEqual(result[0].path[0].resolution, "exact")

    def test_ignores_module_level_occurrence_and_non_python_reference(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "example.py").write_text("target()\n")
            seed = Node(
                "p.example.target", "function", "target",
                SourceRange("example.py", 1, 1), "structural", 1.0, HASH,
            )
            references = (
                Node("module", "reference", "target", SourceRange("example.py", 1, 1), "semantic", 1.0, HASH),
                Node("other", "reference", "target", SourceRange("example.ts", 1, 1), "semantic", 1.0, HASH),
            )
            self.assertEqual(
                tuple(PythonExactCallerProvider(repository, "p").callers(seed, references)),
                (),
            )

    def test_does_not_reemit_recursive_seed_as_its_own_caller(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            (repository / "example.py").write_text(
                "def target():\n    target()\n\ndef wrapper():\n    target()\n"
            )
            seed = Node(
                "p.example.target", "function", "target",
                SourceRange("example.py", 1, 2), "structural", 1.0, HASH,
            )
            references = (
                Node("recursive", "reference", "target", SourceRange("example.py", 2, 2), "semantic", 1.0, HASH),
                Node("wrapper", "reference", "target", SourceRange("example.py", 5, 5), "semantic", 1.0, HASH),
            )
            result = PythonExactCallerProvider(repository, "p").callers(
                seed, references
            )
            self.assertEqual([hit.node.id for hit in result], ["p.example.wrapper"])


if __name__ == "__main__":
    unittest.main()
