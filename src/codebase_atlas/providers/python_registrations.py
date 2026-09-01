"""Exact, source-declared Python callable registration relationships."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from time import monotonic
from typing import Any

from ..contracts import Edge, Node, SourceRange
from ..graph import ImpactHit, ImpactTraversal
from .python_inventory import python_source_files


_CONSTRUCTORS = {"fastapi.FastAPI", "flask.Flask"}
_CALL_SPECS = {
    "fastapi.FastAPI.add_api_route": (1, "endpoint"),
    "flask.Flask.add_url_rule": (2, "view_func"),
    "django.urls.path": (1, "view"),
    "django.urls.re_path": (1, "view"),
    "homeassistant.helpers.dispatcher.async_dispatcher_connect": (2, "target"),
}
_DECORATOR_APIS = {"flask.Flask.route"}


def _hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class Registration:
    source: Node
    target: Node
    edge: Edge


@dataclass(frozen=True)
class RegistrationIndex:
    registrations: tuple[Registration, ...]

    def callers(self, seed: Node) -> ImpactTraversal:
        matches = [
            item for item in self.registrations
            if item.target.name == seed.name
            and item.target.location.path == seed.location.path
            and item.target.location.start_line == seed.location.start_line
        ]
        return ImpactTraversal(tuple(
            ImpactHit(item.source, 1, (item.edge,)) for item in matches
        ), examined_nodes=len(matches), examined_edges=len(matches))

    def callers_for(
        self, symbol: str, *, target_path: str = "", target_owner: str = ""
    ) -> ImpactTraversal:
        matches = []
        for item in self.registrations:
            target = item.target
            if target.name != symbol:
                continue
            if target_path and target.location.path != target_path:
                continue
            owner = str(target.attributes.get("owner", ""))
            if target_owner and target_owner not in {owner, target.name}:
                continue
            matches.append(item)
        return ImpactTraversal(tuple(
            ImpactHit(item.source, 1, (item.edge,)) for item in matches
        ), examined_nodes=len(matches), examined_edges=len(matches))

    def callees(
        self, symbol: str, *, target_path: str = "", target_owner: str = ""
    ) -> ImpactTraversal:
        matches = []
        for item in self.registrations:
            source = item.source
            if source.name != symbol:
                continue
            if target_path and source.location.path != target_path:
                continue
            owner = str(source.attributes.get("owner", ""))
            if target_owner and target_owner not in {owner, source.name}:
                continue
            matches.append(item)
        return ImpactTraversal(tuple(
            ImpactHit(item.target, 1, (item.edge,)) for item in matches
        ), examined_nodes=len(matches), examined_edges=len(matches))


@dataclass(frozen=True)
class _Binding:
    kind: str
    identity: str = ""
    node: Node | None = None


@dataclass(frozen=True)
class _Module:
    path: Path
    relative: str
    name: str
    text: str
    tree: ast.Module


class PythonRegistrationProvider:
    """Resolve a closed specification of registration APIs without execution."""

    name = "atlas-python-registrations"

    def __init__(self, repository: Path, project: str) -> None:
        self.repository = repository.resolve()
        self.project = project
        self._known_files: tuple[Path, ...] | None = None

    def _files(self) -> tuple[Path, ...]:
        self._known_files = python_source_files(self.repository)
        return self._known_files

    def source_files(self) -> tuple[Path, ...]:
        """Return the deterministic Python source inventory used by this provider."""
        return self._files()

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

    @staticmethod
    def _deadline(timeout_ms: int | None) -> float | None:
        return None if timeout_ms is None else monotonic() + timeout_ms / 1000.0

    @staticmethod
    def _check(deadline: float | None) -> None:
        if deadline is not None and monotonic() >= deadline:
            raise TimeoutError("Python registration scan exceeded the query time budget")

    def source_fingerprint(self, *, timeout_ms: int | None = None) -> str:
        deadline = self._deadline(timeout_ms)
        digest = hashlib.sha256(b"codebase-atlas-python-registrations-v1\0")
        for path in self._files():
            self._check(deadline)
            relative = path.relative_to(self.repository).as_posix()
            try:
                content = path.read_bytes()
            except OSError:
                content = b"<unreadable>"
            digest.update(relative.encode("utf-8", errors="surrogateescape"))
            digest.update(b"\0")
            digest.update(content)
            digest.update(b"\0")
        return digest.hexdigest()

    def source_signature(self, *, timeout_ms: int | None = None) -> str:
        """Return a cheap invalidation signature for a content-hash cache."""
        deadline = self._deadline(timeout_ms)
        digest = hashlib.sha256(b"codebase-atlas-python-registration-stat-v1\0")
        for path in self._files():
            self._check(deadline)
            relative = path.relative_to(self.repository).as_posix()
            try:
                metadata = path.stat()
                identity = (
                    metadata.st_mode,
                    metadata.st_ino,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                    metadata.st_ctime_ns,
                )
            except OSError:
                identity = ("unreadable",)
            digest.update(relative.encode("utf-8", errors="surrogateescape"))
            digest.update(b"\0")
            digest.update(repr(identity).encode())
            digest.update(b"\0")
        return digest.hexdigest()

    def scan(self, *, timeout_ms: int | None = None) -> RegistrationIndex:
        return self._scan_files(self._files(), timeout_ms=timeout_ms)

    def scan_files(
        self,
        relative_paths: set[str],
        *,
        known_nodes: tuple[Node, ...] = (),
        timeout_ms: int | None = None,
    ) -> RegistrationIndex:
        """Analyze selected files while reusing exact dependency identities."""
        selected = tuple(
            path for path in self._files()
            if path.relative_to(self.repository).as_posix() in relative_paths
        )
        return self._scan_files(
            selected, known_nodes=known_nodes, timeout_ms=timeout_ms
        )

    def _scan_files(
        self,
        files: tuple[Path, ...],
        *,
        known_nodes: tuple[Node, ...] = (),
        timeout_ms: int | None = None,
    ) -> RegistrationIndex:
        deadline = self._deadline(timeout_ms)
        modules: list[_Module] = []
        for path in files:
            self._check(deadline)
            try:
                text = path.read_text(encoding="utf-8")
                tree = ast.parse(text, filename=str(path))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            modules.append(_Module(
                path, path.relative_to(self.repository).as_posix(),
                self._module_name(path), text, tree,
            ))

        definitions: dict[str, Node] = {
            self._node_identity(node): node for node in known_nodes
        }
        nodes_by_ast: dict[int, Node] = {}
        for module in modules:
            self._collect_definitions(
                module, module.tree.body, (), False, definitions, nodes_by_ast
            )

        registrations: list[Registration] = []
        for module in modules:
            self._check(deadline)
            self._analyze_statements(
                module, module.tree.body, {}, (), None, definitions,
                nodes_by_ast, registrations, deadline,
            )
        unique = {
            (item.edge.source_id, item.edge.target_id, item.edge.evidence_hash): item
            for item in registrations
        }
        ordered = tuple(sorted(unique.values(), key=lambda item: (
            item.source.location.path, item.source.location.start_line,
            item.target.location.path, item.target.location.start_line,
        )))
        return RegistrationIndex(ordered)

    def _collect_definitions(
        self,
        module: _Module,
        statements: list[ast.stmt],
        owners: tuple[str, ...],
        in_class: bool,
        definitions: dict[str, Node],
        nodes_by_ast: dict[int, Node],
    ) -> None:
        for statement in statements:
            if isinstance(statement, ast.ClassDef):
                self._collect_definitions(
                    module, statement.body, (*owners, statement.name), True,
                    definitions, nodes_by_ast,
                )
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = ".".join(filter(None, (module.name, *owners, statement.name)))
                node = Node(
                    id=".".join(filter(None, (self.project, qualified))),
                    kind="method" if in_class else "function",
                    name=statement.name,
                    location=SourceRange(
                        module.relative, statement.lineno,
                        statement.end_lineno or statement.lineno,
                    ),
                    provider=self.name,
                    confidence=1.0,
                    evidence_hash=_hash({
                        "identity": qualified,
                        "path": module.relative,
                        "lines": (statement.lineno, statement.end_lineno),
                    }),
                    attributes={"project": self.project, "owner": ".".join((*owners, statement.name))},
                )
                definitions[qualified] = node
                nodes_by_ast[id(statement)] = node
                self._collect_definitions(
                    module, statement.body, (*owners, statement.name), False,
                    definitions, nodes_by_ast,
                )

    @staticmethod
    def _bind(environment: dict[str, _Binding], name: str, binding: _Binding) -> None:
        existing = environment.get(name)
        if existing is None or existing == binding:
            environment[name] = binding
        else:
            environment[name] = _Binding("ambiguous")

    def _expr_identity(self, node: ast.AST, environment: dict[str, _Binding]) -> str:
        if isinstance(node, ast.Name):
            binding = environment.get(node.id)
            return binding.identity if binding and binding.kind != "ambiguous" else ""
        if isinstance(node, ast.Attribute):
            base = self._expr_identity(node.value, environment)
            return f"{base}.{node.attr}" if base else ""
        return ""

    def _callback(
        self,
        expression: ast.AST,
        environment: dict[str, _Binding],
        definitions: dict[str, Node],
    ) -> Node | None:
        if isinstance(expression, ast.Name):
            binding = environment.get(expression.id)
            return binding.node if binding and binding.kind == "callable" else None
        identity = self._expr_identity(expression, environment)
        return definitions.get(identity)

    def _source_node(
        self,
        module: _Module,
        site: ast.AST,
        enclosing: Node | None,
    ) -> Node:
        if enclosing is not None:
            return enclosing
        line = int(getattr(site, "lineno", 1))
        end_line = int(getattr(site, "end_lineno", line))
        name = f"registration@{line}"
        identity = ".".join(filter(None, (self.project, module.name, name)))
        return Node(
            identity, "registration", name,
            SourceRange(module.relative, line, end_line),
            self.name, 1.0,
            _hash({"identity": identity, "path": module.relative, "lines": (line, end_line)}),
            {"project": self.project, "owner": name},
        )

    def _emit(
        self,
        module: _Module,
        site: ast.AST,
        api: str,
        target: Node,
        enclosing: Node | None,
        registrations: list[Registration],
    ) -> None:
        source = self._source_node(module, site, enclosing)
        segment = ast.get_source_segment(module.text, site) or api
        edge = Edge(
            source.id, target.id, "registers", self.name, 1.0,
            _hash({
                "source": source.id, "target": target.id, "api": api,
                "path": module.relative, "line": getattr(site, "lineno", 1),
                "source_hash": hashlib.sha256(segment.encode()).hexdigest(),
            }),
            resolution="exact",
            attributes={
                "strategy": "exact_registration_api_binding",
                "registration_api": api,
                "registration_path": module.relative,
                "registration_line": int(getattr(site, "lineno", 1)),
            },
        )
        registrations.append(Registration(source, target, edge))

    def _analyze_call(
        self,
        module: _Module,
        call: ast.Call,
        environment: dict[str, _Binding],
        enclosing: Node | None,
        definitions: dict[str, Node],
        registrations: list[Registration],
    ) -> None:
        api = self._expr_identity(call.func, environment)
        spec = _CALL_SPECS.get(api)
        if spec is None:
            return
        position, keyword = spec
        expression = next(
            (item.value for item in call.keywords if item.arg == keyword),
            call.args[position] if len(call.args) > position else None,
        )
        if expression is None or isinstance(expression, (ast.Call, ast.Lambda, ast.Constant)):
            return
        target = self._callback(expression, environment, definitions)
        if target is not None:
            self._emit(module, call, api, target, enclosing, registrations)

    def _analyze_statements(
        self,
        module: _Module,
        statements: list[ast.stmt],
        outer_environment: dict[str, _Binding],
        owners: tuple[str, ...],
        enclosing: Node | None,
        definitions: dict[str, Node],
        nodes_by_ast: dict[int, Node],
        registrations: list[Registration],
        deadline: float | None,
    ) -> None:
        environment = dict(outer_environment)
        current_module = module.name
        is_package = module.path.name == "__init__.py"

        class CallVisitor(ast.NodeVisitor):
            def __init__(visitor_self) -> None:
                visitor_self.calls: list[ast.Call] = []

            def visit_Call(visitor_self, node: ast.Call) -> None:
                visitor_self.calls.append(node)
                visitor_self.generic_visit(node)

            def visit_FunctionDef(visitor_self, node: ast.FunctionDef) -> None:
                return

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_ClassDef(visitor_self, node: ast.ClassDef) -> None:
                return

            def visit_Lambda(visitor_self, node: ast.Lambda) -> None:
                return

        for statement in statements:
            self._check(deadline)
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    local = alias.asname or alias.name.split(".")[0]
                    identity = alias.name if alias.asname else alias.name.split(".")[0]
                    self._bind(environment, local, _Binding("import", identity))
                continue
            if isinstance(statement, ast.ImportFrom):
                source = self._import_module(current_module, is_package, statement)
                for alias in statement.names:
                    if alias.name == "*":
                        continue
                    local = alias.asname or alias.name
                    identity = ".".join(filter(None, (source, alias.name)))
                    node = definitions.get(identity)
                    self._bind(
                        environment, local,
                        _Binding("callable" if node else "import", identity, node),
                    )
                continue
            if isinstance(statement, ast.ClassDef):
                identity = ".".join(filter(None, (current_module, *owners, statement.name)))
                self._bind(environment, statement.name, _Binding("class", identity))
                self._analyze_statements(
                    module, statement.body, environment, (*owners, statement.name),
                    enclosing, definitions, nodes_by_ast, registrations, deadline,
                )
                continue
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                target = nodes_by_ast[id(statement)]
                for decorator in statement.decorator_list:
                    if not isinstance(decorator, ast.Call):
                        continue
                    api = self._expr_identity(decorator.func, environment)
                    if api in _DECORATOR_APIS:
                        self._emit(
                            module, decorator, api, target, enclosing, registrations
                        )
                self._bind(
                    environment, statement.name,
                    _Binding("callable", self._node_identity(target), target),
                )
                inner = dict(environment)
                args = (
                    *statement.args.posonlyargs, *statement.args.args,
                    *statement.args.kwonlyargs,
                )
                for argument in args:
                    identity = self._expr_identity(argument.annotation, environment) if argument.annotation else ""
                    if identity in _CONSTRUCTORS:
                        inner[argument.arg] = _Binding("instance", identity)
                    else:
                        inner[argument.arg] = _Binding("ambiguous")
                if statement.args.vararg:
                    inner[statement.args.vararg.arg] = _Binding("ambiguous")
                if statement.args.kwarg:
                    inner[statement.args.kwarg.arg] = _Binding("ambiguous")
                self._analyze_statements(
                    module, statement.body, inner, (*owners, statement.name), target,
                    definitions, nodes_by_ast, registrations, deadline,
                )
                continue

            visitor = CallVisitor()
            visitor.visit(statement)
            for call in visitor.calls:
                self._analyze_call(
                    module, call, environment, enclosing, definitions, registrations
                )

            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value = statement.value
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                binding = _Binding("ambiguous")
                if isinstance(value, ast.Call):
                    identity = self._expr_identity(value.func, environment)
                    if identity in _CONSTRUCTORS:
                        binding = _Binding("instance", identity)
                elif isinstance(value, (ast.Name, ast.Attribute)):
                    identity = self._expr_identity(value, environment)
                    existing = (
                        environment.get(value.id) if isinstance(value, ast.Name) else None
                    )
                    node = definitions.get(identity) or (existing.node if existing else None)
                    if node:
                        binding = _Binding("callable", identity, node)
                for target in targets:
                    if isinstance(target, ast.Name):
                        self._bind(environment, target.id, binding)

    def _node_identity(self, node: Node) -> str:
        prefix = f"{self.project}." if self.project else ""
        return node.id[len(prefix):] if prefix and node.id.startswith(prefix) else node.id
