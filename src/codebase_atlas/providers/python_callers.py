"""Exact Python caller identities derived from semantic references and local AST."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

from ..contracts import Edge, Node, SourceRange
from ..graph import ImpactHit, ImpactTraversal


def _hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PythonExactCallerProvider:
    """Map exact semantic occurrences to their smallest enclosing Python callable."""

    name = "atlas-python-exact-callers"

    def __init__(self, repository: Path, project: str) -> None:
        self.repository = repository.resolve()
        self.project = project

    def callers(
        self, seed: Node, references: tuple[Node, ...]
    ) -> ImpactTraversal:
        hits: list[ImpactHit] = []
        seen: set[str] = set()
        for reference in references:
            if reference.confidence != 1.0 or reference.location.path.endswith(".py") is False:
                continue
            caller = self._enclosing_caller(reference)
            if caller is None or caller.id == seed.id or caller.id in seen:
                continue
            seen.add(caller.id)
            edge = Edge(
                source_id=caller.id,
                target_id=seed.id,
                relation="calls",
                provider=self.name,
                confidence=1.0,
                evidence_hash=_hash({
                    "caller": caller.id,
                    "target": seed.id,
                    "reference": reference.evidence_hash,
                }),
                resolution="exact",
                attributes={
                    "strategy": "semantic_reference_enclosing_ast",
                    "reference_path": reference.location.path,
                    "reference_line": reference.location.start_line,
                },
            )
            hits.append(ImpactHit(caller, 1, (edge,)))
        return ImpactTraversal(tuple(hits))

    def _enclosing_caller(self, reference: Node) -> Node | None:
        relative = Path(reference.location.path)
        source_path = (self.repository / relative).resolve()
        try:
            source_path.relative_to(self.repository)
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(relative))
        except (OSError, SyntaxError, ValueError):
            return None
        line = reference.location.start_line
        candidates: list[tuple[int, ast.AST, tuple[str, ...], bool]] = []

        def visit(node: ast.AST, owners: tuple[str, ...], in_class: bool) -> None:
            next_owners = owners
            next_in_class = in_class
            if isinstance(node, ast.ClassDef):
                next_owners = (*owners, node.name)
                next_in_class = True
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end_line = node.end_lineno or node.lineno
                if node.lineno <= line <= end_line:
                    candidates.append((end_line - node.lineno, node, owners, in_class))
                next_owners = (*owners, node.name)
            for child in ast.iter_child_nodes(node):
                visit(child, next_owners, next_in_class)

        visit(tree, (), False)
        if not candidates:
            return None
        _span, function, owners, in_class = min(candidates, key=lambda item: item[0])
        assert isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
        module = relative.with_suffix("").as_posix().replace("/", ".")
        qualified = ".".join((self.project, module, *owners, function.name))
        location = SourceRange(
            relative.as_posix(), function.lineno, function.end_lineno or function.lineno
        )
        return Node(
            id=qualified,
            kind="method" if in_class else "function",
            name=function.name,
            location=location,
            provider=self.name,
            confidence=1.0,
            evidence_hash=_hash({
                "id": qualified,
                "path": location.path,
                "lines": (location.start_line, location.end_line),
                "reference": reference.evidence_hash,
            }),
            attributes={"project": self.project},
        )
