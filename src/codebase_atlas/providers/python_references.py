"""Exact Python references resolved through explicit import and re-export bindings."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from time import monotonic

from ..contracts import Node, SourceRange
from .python_inventory import python_source_files


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _assigned_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names = {argument.arg for argument in (
        *function.args.posonlyargs, *function.args.args,
        *function.args.kwonlyargs,
    )}
    if function.args.vararg:
        names.add(function.args.vararg.arg)
    if function.args.kwarg:
        names.add(function.args.kwarg.arg)

    class Collector(ast.NodeVisitor):
        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, ast.Store):
                names.add(node.id)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            names.add(node.name)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            names.add(node.name)

    collector = Collector()
    for statement in function.body:
        collector.visit(statement)
    globals_ = {
        name for statement in function.body if isinstance(statement, ast.Global)
        for name in statement.names
    }
    return names - globals_


class PythonExactReferenceProvider:
    name = "atlas-python-references"

    def __init__(self, repository: Path) -> None:
        self.repository = repository.resolve()

    def _files(self) -> tuple[Path, ...]:
        return python_source_files(self.repository)

    @staticmethod
    def _module_name(path: Path) -> str:
        parts: list[str] = [] if path.name == "__init__.py" else [path.stem]
        parent = path.parent
        while (parent / "__init__.py").is_file():
            parts.insert(0, parent.name)
            parent = parent.parent
        return ".".join(parts)

    @staticmethod
    def _import_module(current: str, is_package: bool, node: ast.ImportFrom) -> str:
        if node.level == 0:
            return node.module or ""
        package = current if is_package else current.rpartition(".")[0]
        parts = package.split(".") if package else []
        keep = max(0, len(parts) - (node.level - 1))
        prefix = parts[:keep]
        if node.module:
            prefix.extend(node.module.split("."))
        return ".".join(prefix)

    def references(
        self,
        symbol: str,
        *,
        target_path: str,
        timeout_ms: int | None = None,
    ) -> tuple[Node, ...]:
        deadline = None if timeout_ms is None else monotonic() + timeout_ms / 1000.0

        def check_time() -> None:
            if deadline is not None and monotonic() >= deadline:
                raise TimeoutError("Python exact reference scan exceeded the query time budget")

        files = self._files()
        modules = {self._module_name(path): path for path in files if self._module_name(path)}
        target = (self.repository / target_path).resolve()
        if target not in files:
            return ()
        target_module = self._module_name(target)
        if not target_module:
            return ()
        exports = {target_module}
        parsed: dict[Path, tuple[str, ast.Module]] = {}
        for path in files:
            check_time()
            try:
                text = path.read_text(encoding="utf-8")
                if symbol not in text:
                    continue
                parsed[path] = (text, ast.parse(text, filename=str(path)))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue

        changed = True
        while changed:
            changed = False
            for path, (_text, tree) in parsed.items():
                if path.name != "__init__.py":
                    continue
                current = self._module_name(path)
                for node in tree.body:
                    if not isinstance(node, ast.ImportFrom):
                        continue
                    source = self._import_module(current, True, node)
                    if source not in exports:
                        continue
                    if any(alias.name == symbol for alias in node.names) and current not in exports:
                        exports.add(current)
                        changed = True

        results: list[Node] = []
        seen: set[tuple[str, int, int]] = set()
        for path, (text, tree) in parsed.items():
            check_time()
            current = self._module_name(path)
            is_package = path.name == "__init__.py"
            symbol_bindings: set[str] = set()
            module_bindings: dict[str, str] = {}
            for node in tree.body:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        local = alias.asname or alias.name.split(".")[0]
                        module_bindings[local] = alias.name if alias.asname else alias.name.split(".")[0]
                elif isinstance(node, ast.ImportFrom):
                    source = self._import_module(current, is_package, node)
                    for alias in node.names:
                        local = alias.asname or alias.name
                        imported_module = f"{source}.{alias.name}" if source else alias.name
                        if imported_module in modules:
                            module_bindings[local] = imported_module
                        elif alias.name == symbol and source in exports:
                            symbol_bindings.add(local)

            relative = path.relative_to(self.repository).as_posix()

            class Visitor(ast.NodeVisitor):
                def __init__(self) -> None:
                    self.shadows: list[set[str]] = []

                def shadowed(self, name: str) -> bool:
                    return any(name in scope for scope in self.shadows)

                def emit(self, node: ast.AST) -> None:
                    line = int(getattr(node, "lineno", 0))
                    column = int(getattr(node, "col_offset", 0)) + 1
                    key = (relative, line, column)
                    if line <= 0 or key in seen:
                        return
                    seen.add(key)
                    end_line = int(getattr(node, "end_lineno", line))
                    end_column = getattr(node, "end_col_offset", None)
                    evidence = ast.get_source_segment(text, node) or symbol
                    results.append(Node(
                        id=f"python:reference:{target_module}:{symbol}:{relative}:{line}:{column}",
                        kind="reference",
                        name=symbol,
                        location=SourceRange(
                            relative, line, end_line, column,
                            int(end_column) + 1 if end_column is not None else None,
                        ),
                        provider=PythonExactReferenceProvider.name,
                        confidence=1.0,
                        evidence_hash=_hash(evidence),
                        attributes={
                            "strategy": "exact_import_or_reexport_binding",
                            "target_module": target_module,
                        },
                    ))

                def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                    for decorator in node.decorator_list:
                        self.visit(decorator)
                    self.shadows.append(_assigned_names(node))
                    for statement in node.body:
                        self.visit(statement)
                    self.shadows.pop()

                visit_AsyncFunctionDef = visit_FunctionDef

                def visit_Name(self, node: ast.Name) -> None:
                    if isinstance(node.ctx, ast.Load) and node.id in symbol_bindings and not self.shadowed(node.id):
                        self.emit(node)

                def visit_Attribute(self, node: ast.Attribute) -> None:
                    if (
                        node.attr == symbol and isinstance(node.value, ast.Name)
                        and not self.shadowed(node.value.id)
                        and module_bindings.get(node.value.id) in exports
                    ):
                        self.emit(node)
                        return
                    self.generic_visit(node)

            Visitor().visit(tree)
        return tuple(sorted(results, key=lambda node: (
            node.location.path, node.location.start_line, node.location.start_column or 0
        )))
