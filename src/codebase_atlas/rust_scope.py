"""Static, Git-aware Cargo and Rust module source scope discovery.

This module intentionally never executes Cargo, rustc, rust-analyzer, build
scripts, proc macros, applications, or tests.  It derives a conservative T0
scope from repository files only and makes every omission explicit.
"""

from __future__ import annotations

from fnmatch import fnmatch
import hashlib
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import stat
import tomllib
from typing import Any

from .providers.python_inventory import SourceInventoryError, supported_source_files


RUST_SCOPE_SCHEMA_VERSION = 1
RUST_SCOPE_INVENTORY = "git_aware_cargo_module_scope_v1"
RUST_BUILD_CONTEXT = {
    "schema_version": 1,
    "cargo_features": "all",
    "cargo_no_deps": True,
    "cargo_cfgs": "all_package_features",
    "all_targets": False,
    "build_scripts": False,
    "proc_macros": False,
}
_MODULE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:#\s*\[\s*path\s*=\s*\"(?P<path>[^\"]+)\"\s*\]\s*)?"
    r"(?:(?:pub(?:\s*\([^)]*\))?)\s+)?mod\s+"
    r"(?P<name>r#[A-Za-z_][A-Za-z0-9_]*|[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?P<delimiter>[;{])"
)
_MACRO_RULES = re.compile(
    r"(?<![A-Za-z0-9_])macro_rules!\s*"
    r"(?P<name>r#[A-Za-z_][A-Za-z0-9_]*|[A-Za-z_][A-Za-z0-9_]*)\s*\{"
)
_MACRO_CALL = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?P<name>r#[A-Za-z_][A-Za-z0-9_]*|[A-Za-z_][A-Za-z0-9_]*)\s*!\s*[([{]"
)
_LITERAL_INCLUDE = re.compile(r"\binclude!\s*\(\s*\"([^\"]+\.rs)\"\s*\)")
_ANY_INCLUDE = re.compile(r"\binclude!\s*\(")


class RustScopeError(ValueError):
    """Rust source scope cannot be bounded safely to one repository."""


def validate_rust_build_context(value: Any) -> dict[str, Any]:
    """Validate the fixed, non-executing semantic build context."""
    if value != RUST_BUILD_CONTEXT:
        raise RustScopeError("Rust build context is invalid")
    return dict(RUST_BUILD_CONTEXT)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=True).relative_to(root).as_posix()
    except (OSError, ValueError) as exc:
        raise RustScopeError(f"Rust scope path escapes repository: {path}") from exc


def _regular(root: Path, path: Path, *, required: bool = True) -> Path | None:
    try:
        metadata = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError as exc:
        if required:
            raise RustScopeError(f"Rust scope input is missing: {path}") from exc
        return None
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise RustScopeError(f"Rust scope input is not a safe regular file: {path}")
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RustScopeError(f"Rust scope input escapes repository: {path}") from exc
    return resolved


def _load_manifest(root: Path, path: Path) -> tuple[Path, dict[str, Any]]:
    safe = _regular(root, path)
    assert safe is not None
    try:
        value = tomllib.loads(safe.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise RustScopeError(
            f"Cargo manifest cannot be parsed: {_relative(root, safe)}"
        ) from exc
    if not isinstance(value, dict):
        raise RustScopeError(f"Cargo manifest is invalid: {_relative(root, safe)}")
    return safe, value


def _workspace_manifests(
    root: Path, root_manifest: Path, document: dict[str, Any]
) -> tuple[tuple[Path, ...], list[dict[str, str]]]:
    workspace = document.get("workspace")
    package = document.get("package")
    if workspace is None:
        return ((root_manifest,) if isinstance(package, dict) else ()), []
    if not isinstance(workspace, dict):
        raise RustScopeError("Cargo workspace must be an object")
    raw_members = workspace.get("members", [])
    raw_exclude = workspace.get("exclude", [])
    if not isinstance(raw_members, list) or not all(
        isinstance(item, str) and item for item in raw_members
    ):
        raise RustScopeError("Cargo workspace members must be strings")
    if not isinstance(raw_exclude, list) or not all(
        isinstance(item, str) and item for item in raw_exclude
    ):
        raise RustScopeError("Cargo workspace excludes must be strings")

    manifests: set[Path] = {root_manifest}
    exclusions: list[dict[str, str]] = []
    for pattern in raw_members:
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise RustScopeError(f"unsafe Cargo workspace member pattern: {pattern}")
        matches: list[Path] = []
        for candidate in sorted(root.glob(pattern)):
            manifest = candidate if candidate.name == "Cargo.toml" else candidate / "Cargo.toml"
            if manifest.is_file():
                relative_dir = manifest.parent.relative_to(root).as_posix() or "."
                if not any(fnmatch(relative_dir, excluded) for excluded in raw_exclude):
                    matches.append(manifest)
        if not matches:
            exclusions.append({
                "path": pattern,
                "reason": "workspace_member_pattern_unmatched",
            })
        manifests.update(matches)
    return tuple(sorted(manifests)), exclusions


def _target(
    root: Path,
    package_root: Path,
    package: str,
    kind: str,
    name: str,
    raw_path: str,
) -> dict[str, str]:
    candidate = package_root / raw_path
    if Path(raw_path).is_absolute() or ".." in Path(raw_path).parts:
        raise RustScopeError(f"unsafe Cargo target path: {raw_path}")
    try:
        path = candidate.resolve(strict=False)
        path.relative_to(root)
        path.relative_to(package_root)
    except ValueError as exc:
        raise RustScopeError(f"Cargo target escapes its package: {raw_path}") from exc
    return {
        "package": package,
        "kind": kind,
        "name": name,
        "root": path.relative_to(root).as_posix(),
    }


def _explicit_targets(
    root: Path,
    package_root: Path,
    package: str,
    document: dict[str, Any],
) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    tables = (("lib", "lib"), ("bin", "bin"), ("test", "test"),
              ("bench", "bench"), ("example", "example"))
    for key, kind in tables:
        raw = document.get(key)
        entries = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                name = package if kind == "lib" else f"{kind}-{index}"
            targets.append(_target(
                root, package_root, package, kind, name, entry["path"]
            ))
    return targets


def _automatic_targets(
    root: Path,
    package_root: Path,
    package: str,
    package_table: dict[str, Any],
) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    defaults = (
        ("lib", "lib", "src/lib.rs", "autolib"),
        ("bin", package, "src/main.rs", "autobins"),
    )
    for kind, name, relative, switch in defaults:
        if package_table.get(switch, True) is not False and (package_root / relative).is_file():
            targets.append(_target(root, package_root, package, kind, name, relative))
    directories = (
        ("bin", "src/bin", "autobins"),
        ("test", "tests", "autotests"),
        ("example", "examples", "autoexamples"),
        ("bench", "benches", "autobenches"),
    )
    for kind, directory, switch in directories:
        if package_table.get(switch, True) is False:
            continue
        base = package_root / directory
        if not base.is_dir():
            continue
        roots = list(base.glob("*.rs")) + list(base.glob("*/main.rs"))
        for path in sorted(roots):
            relative = path.relative_to(package_root).as_posix()
            name = path.stem if path.name != "main.rs" else path.parent.name
            targets.append(_target(root, package_root, package, kind, name, relative))
    build = package_table.get("build", "build.rs")
    if build is not False and isinstance(build, str) and (package_root / build).is_file():
        targets.append(_target(root, package_root, package, "custom-build", "build-script", build))
    return targets


def _without_comments(text: str) -> str:
    result = list(text)
    index = 0
    block_depth = 0
    while index < len(text):
        pair = text[index:index + 2]
        if block_depth:
            if pair == "/*":
                block_depth += 1
                result[index:index + 2] = "  "
                index += 2
            elif pair == "*/":
                block_depth -= 1
                result[index:index + 2] = "  "
                index += 2
            else:
                if text[index] != "\n":
                    result[index] = " "
                index += 1
            continue
        literal_end = _rust_literal_end(text, index)
        if literal_end is not None:
            index = literal_end
            continue
        if pair == "//":
            end = text.find("\n", index)
            end = len(text) if end < 0 else end
            result[index:end] = " " * (end - index)
            index = end
        elif pair == "/*":
            block_depth = 1
            result[index:index + 2] = "  "
            index += 2
        else:
            index += 1
    return "".join(result)


def _document_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RustScopeError(f"Rust scope {label} path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or ".." in path.parts:
        raise RustScopeError(f"Rust scope {label} path is not repository-relative")
    return value


def validate_rust_source_scope(value: Any) -> dict[str, Any]:
    """Validate and normalize a persisted Rust source-scope document."""
    if not isinstance(value, dict) or value.get("schema_version") != RUST_SCOPE_SCHEMA_VERSION:
        raise RustScopeError("Rust source scope schema is invalid")
    if value.get("inventory") != RUST_SCOPE_INVENTORY:
        raise RustScopeError("Rust source scope inventory is invalid")
    status = value.get("status")
    if status not in {"complete_exact", "exact_hits_partial_scope"}:
        raise RustScopeError("Rust source scope status is invalid")

    def hashed_entries(name: str) -> list[dict[str, Any]]:
        raw_entries = value.get(name)
        if not isinstance(raw_entries, list):
            raise RustScopeError(f"Rust source scope {name} are invalid")
        entries = []
        for raw in raw_entries:
            if not isinstance(raw, dict):
                raise RustScopeError(f"Rust source scope {name} entry is invalid")
            path = _document_path(raw.get("path"), name)
            digest = raw.get("content_sha256")
            size = raw.get("size")
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise RustScopeError(f"Rust source scope {name} hash is invalid")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise RustScopeError(f"Rust source scope {name} size is invalid")
            entries.append({"path": path, "content_sha256": digest, "size": size})
        if len({entry["path"] for entry in entries}) != len(entries):
            raise RustScopeError(f"Rust source scope {name} paths are duplicated")
        return sorted(entries, key=lambda item: item["path"])

    manifests = hashed_entries("manifests")
    raw_lock = value.get("lockfile")
    if not isinstance(raw_lock, dict) or raw_lock.get("status") not in {"absent", "present"}:
        raise RustScopeError("Rust source scope lockfile is invalid")
    lockfile: dict[str, Any] = {"status": raw_lock["status"]}
    if raw_lock["status"] == "present":
        path = _document_path(raw_lock.get("path"), "lockfile")
        digest = raw_lock.get("content_sha256")
        size = raw_lock.get("size")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise RustScopeError("Rust source scope lockfile hash is invalid")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise RustScopeError("Rust source scope lockfile size is invalid")
        lockfile.update(path=path, content_sha256=digest, size=size)

    packages = []
    raw_packages = value.get("packages")
    if not isinstance(raw_packages, list):
        raise RustScopeError("Rust source scope packages are invalid")
    for raw in raw_packages:
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not raw["name"]:
            raise RustScopeError("Rust source scope package is invalid")
        package_root = raw.get("root")
        if package_root != ".":
            package_root = _document_path(package_root, "package root")
        packages.append({
            "name": raw["name"],
            "root": package_root,
            "manifest": _document_path(raw.get("manifest"), "package manifest"),
        })

    targets = []
    raw_targets = value.get("targets")
    if not isinstance(raw_targets, list):
        raise RustScopeError("Rust source scope targets are invalid")
    for raw in raw_targets:
        if not isinstance(raw, dict):
            raise RustScopeError("Rust source scope target is invalid")
        for name in ("package", "kind", "name"):
            if not isinstance(raw.get(name), str) or not raw[name]:
                raise RustScopeError(f"Rust source scope target {name} is invalid")
        targets.append({
            "package": raw["package"], "kind": raw["kind"], "name": raw["name"],
            "root": _document_path(raw.get("root"), "target root"),
        })

    raw_paths = value.get("source_paths")
    if not isinstance(raw_paths, list):
        raise RustScopeError("Rust source paths are invalid")
    source_paths = sorted({_document_path(path, "source") for path in raw_paths})
    if len(source_paths) != len(raw_paths):
        raise RustScopeError("Rust source paths are duplicated")

    exclusions = []
    raw_exclusions = value.get("exclusions")
    if not isinstance(raw_exclusions, list):
        raise RustScopeError("Rust source scope exclusions are invalid")
    for raw in raw_exclusions:
        if not isinstance(raw, dict) or not isinstance(raw.get("reason"), str) or not raw["reason"]:
            raise RustScopeError("Rust source scope exclusion is invalid")
        raw_path = raw.get("path")
        path = (
            raw_path
            if isinstance(raw_path, str) and any(char in raw_path for char in "*?[")
            else _document_path(raw_path, "exclusion")
        )
        exclusions.append({"path": path, "reason": raw["reason"]})

    execution = value.get("execution")
    required_execution = {
        "cargo", "rustc", "rust_analyzer", "build_scripts", "proc_macros", "network"
    }
    if not isinstance(execution, dict) or set(execution) != required_execution or any(
        execution[name] is not False for name in required_execution
    ):
        raise RustScopeError("Rust source scope execution boundary is invalid")
    return {
        "schema_version": RUST_SCOPE_SCHEMA_VERSION,
        "inventory": RUST_SCOPE_INVENTORY,
        "status": status,
        "manifests": manifests,
        "lockfile": lockfile,
        "packages": sorted(packages, key=lambda item: (item["root"], item["name"])),
        "targets": sorted(targets, key=lambda item: (
            item["package"], item["kind"], item["name"], item["root"]
        )),
        "source_paths": source_paths,
        "exclusions": sorted(exclusions, key=lambda item: (item["path"], item["reason"])),
        "execution": {name: False for name in sorted(required_execution)},
    }


def _child_module_dir(path: Path) -> Path:
    return path.parent if path.name in {"lib.rs", "main.rs", "mod.rs"} else path.parent / path.stem


def _rust_literal_end(text: str, index: int) -> int | None:
    """Return the end of a Rust string/character literal starting at index."""
    prefix = index
    if text.startswith("br", index):
        prefix += 1
    if prefix < len(text) and text[prefix] == "r":
        cursor = prefix + 1
        while cursor < len(text) and text[cursor] == "#":
            cursor += 1
        if cursor < len(text) and text[cursor] == '"':
            hashes = text[prefix + 1:cursor]
            closing = '"' + hashes
            end = text.find(closing, cursor + 1)
            return len(text) if end < 0 else end + len(closing)
    quote_index = index + 1 if text.startswith(("b\"", "b'", "c\""), index) else index
    if quote_index >= len(text) or text[quote_index] not in {'"', "'"}:
        return None
    quote = text[quote_index]
    cursor = quote_index + 1
    if quote == "'":
        if cursor >= len(text) or text[cursor] == "\n":
            return None
        if text[cursor] == "\\":
            cursor += 1
            if cursor < len(text) and text[cursor] in {"u", "x"}:
                cursor += 1
                if cursor < len(text) and text[cursor] == "{":
                    closing = text.find("}", cursor + 1)
                    if closing < 0:
                        return None
                    cursor = closing + 1
                else:
                    while cursor < len(text) and text[cursor].isalnum():
                        cursor += 1
            else:
                cursor += 1
        else:
            cursor += 1
        return cursor + 1 if cursor < len(text) and text[cursor] == "'" else None
    while cursor < len(text):
        if text[cursor] == "\\":
            cursor += 2
        elif text[cursor] == quote:
            return cursor + 1
        else:
            cursor += 1
    return len(text)


def _module_events(
    visible: str,
) -> tuple[
    list[tuple[str, str | None, tuple[str, ...], str | None]],
    list[tuple[str, tuple[str, ...]]],
]:
    """Find modules and macro calls with their lexical expansion context."""
    declarations: list[
        tuple[str, str | None, tuple[str, ...], str | None]
    ] = []
    invocations: list[tuple[str, tuple[str, ...]]] = []
    inline_modules: list[tuple[int, str]] = []
    macro_definitions: list[tuple[int, str]] = []
    depth = 0
    index = 0
    while index < len(visible):
        match = _MODULE.match(visible, index)
        if match is not None:
            name = match.group("name").removeprefix("r#")
            if match.group("delimiter") == ";":
                declarations.append((
                    name,
                    match.group("path"),
                    tuple(module for _, module in inline_modules),
                    macro_definitions[-1][1] if macro_definitions else None,
                ))
            else:
                depth += 1
                inline_modules.append((depth, name))
            index = match.end()
            continue
        macro_definition = _MACRO_RULES.match(visible, index)
        if macro_definition is not None:
            depth += 1
            macro_definitions.append((
                depth, macro_definition.group("name").removeprefix("r#")
            ))
            index = macro_definition.end()
            continue
        macro_call = _MACRO_CALL.match(visible, index)
        if macro_call is not None:
            if not macro_definitions:
                invocations.append((
                    macro_call.group("name").removeprefix("r#"),
                    tuple(module for _, module in inline_modules),
                ))
            index = macro_call.end()
            continue
        literal_end = _rust_literal_end(visible, index)
        if literal_end is not None:
            index = literal_end
            continue
        if visible[index] == "{":
            depth += 1
        elif visible[index] == "}":
            while inline_modules and inline_modules[-1][0] == depth:
                inline_modules.pop()
            while macro_definitions and macro_definitions[-1][0] == depth:
                macro_definitions.pop()
            depth = max(0, depth - 1)
        index += 1
    return declarations, invocations


def _reachable_sources(
    root: Path,
    candidates: set[Path],
    targets: list[dict[str, str]],
) -> tuple[set[Path], list[dict[str, str]], bool]:
    reachable: set[Path] = set()
    exclusions: list[dict[str, str]] = []
    pending: list[Path] = []
    target_roots: set[Path] = set()
    macro_invocation_dirs: dict[str, set[Path]] = {}
    deferred_macro_modules: list[
        tuple[Path, str, str | None, tuple[str, ...], str]
    ] = []
    partial = False
    for target in targets:
        path = root / target["root"]
        safe = _regular(root, path, required=False)
        if safe is None or safe not in candidates:
            exclusions.append({"path": target["root"], "reason": "missing_or_ignored_target_root"})
            partial = True
        else:
            pending.append(safe)
            target_roots.add(safe)
    def enqueue_module(
        source: Path,
        name: str,
        explicit: str | None,
        inline_path: tuple[str, ...],
        base_dirs: set[Path],
    ) -> None:
        nonlocal partial
        choices = []
        for base_dir in sorted(base_dirs):
            declaration_dir = base_dir.joinpath(*inline_path)
            choices.extend(
                [declaration_dir / explicit]
                if explicit
                else [
                    declaration_dir / f"{name}.rs",
                    declaration_dir / name / "mod.rs",
                ]
            )
        existing = sorted({
            candidate.resolve()
            for candidate in choices
            if candidate.resolve() in candidates
        })
        if len(existing) == 1:
            pending.append(existing[0])
        else:
            exclusions.append({
                "path": _relative(root, source),
                "reason": (
                    "ambiguous_module_path"
                    if len(existing) > 1 else f"unresolved_module:{name}"
                ),
            })
            partial = True

    while pending or deferred_macro_modules:
        if not pending:
            remaining = []
            for source, name, explicit, inline_path, macro_name in deferred_macro_modules:
                base_dirs = macro_invocation_dirs.get(macro_name)
                if base_dirs:
                    enqueue_module(source, name, explicit, inline_path, base_dirs)
                else:
                    remaining.append((source, name, explicit, inline_path, macro_name))
            deferred_macro_modules = remaining
            if not pending:
                for source, name, _, _, _ in deferred_macro_modules:
                    exclusions.append({
                        "path": _relative(root, source),
                        "reason": f"unresolved_module:{name}",
                    })
                partial = partial or bool(deferred_macro_modules)
                break
        source = pending.pop()
        if source in reachable:
            continue
        reachable.add(source)
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            exclusions.append({"path": _relative(root, source), "reason": "source_unreadable"})
            partial = True
            continue
        visible = _without_comments(text)
        module_dir = source.parent if source in target_roots else _child_module_dir(source)
        declarations, macro_invocations = _module_events(visible)
        for macro_name, inline_path in macro_invocations:
            macro_invocation_dirs.setdefault(macro_name, set()).add(
                module_dir.joinpath(*inline_path)
            )
        for name, explicit, inline_path, macro_name in declarations:
            if macro_name is not None and not macro_invocation_dirs.get(macro_name):
                deferred_macro_modules.append(
                    (source, name, explicit, inline_path, macro_name)
                )
            else:
                enqueue_module(
                    source,
                    name,
                    explicit,
                    inline_path,
                    (
                        macro_invocation_dirs[macro_name]
                        if macro_name is not None else {module_dir}
                    ),
                )
        literal_includes = _LITERAL_INCLUDE.findall(visible)
        for relative in literal_includes:
            included = (source.parent / relative).resolve()
            if included in candidates:
                pending.append(included)
            else:
                exclusions.append({
                    "path": _relative(root, source),
                    "reason": f"unresolved_literal_include:{relative}",
                })
                partial = True
        if len(_ANY_INCLUDE.findall(visible)) > len(literal_includes):
            exclusions.append({
                "path": _relative(root, source),
                "reason": "dynamic_include_scope_unresolved",
            })
            partial = True
    return reachable, exclusions, partial


def build_rust_source_scope(repository: Path) -> dict[str, Any]:
    """Build a deterministic Rust T0 scope without executing Rust tooling."""
    root = repository.resolve()
    root_manifest, root_document = _load_manifest(root, root / "Cargo.toml")
    manifest_paths, exclusions = _workspace_manifests(
        root, root_manifest, root_document
    )
    try:
        candidates = set(supported_source_files(root, {".rs"}, reject_unsafe=True))
    except SourceInventoryError as exc:
        raise RustScopeError(str(exc)) from exc
    manifests: list[dict[str, Any]] = []
    packages: list[dict[str, str]] = []
    targets: list[dict[str, str]] = []
    partial = bool(exclusions)
    for manifest_path in manifest_paths:
        safe, document = (
            (root_manifest, root_document)
            if manifest_path.resolve() == root_manifest
            else _load_manifest(root, manifest_path)
        )
        package_table = document.get("package")
        relative_manifest = _relative(root, safe)
        manifests.append({
            "path": relative_manifest,
            "content_sha256": _sha256(safe),
            "size": os.lstat(safe).st_size,
        })
        if (
            not isinstance(package_table, dict)
            and safe == root_manifest
            and isinstance(document.get("workspace"), dict)
        ):
            continue
        if not isinstance(package_table, dict) or not isinstance(package_table.get("name"), str):
            exclusions.append({"path": relative_manifest, "reason": "workspace_member_has_no_package"})
            partial = True
            continue
        package_name = package_table["name"]
        package_root = safe.parent
        packages.append({
            "name": package_name,
            "root": package_root.relative_to(root).as_posix() or ".",
            "manifest": relative_manifest,
        })
        package_targets = _explicit_targets(
            root, package_root, package_name, document
        ) + _automatic_targets(root, package_root, package_name, package_table)
        targets.extend(package_targets)
    deduplicated = {
        (item["package"], item["kind"], item["name"], item["root"]): item
        for item in targets
    }
    targets = [deduplicated[key] for key in sorted(deduplicated)]
    reachable, module_exclusions, module_partial = _reachable_sources(
        root, candidates, targets
    )
    exclusions.extend(module_exclusions)
    partial = partial or module_partial or not packages or not targets
    for path in sorted(candidates - reachable):
        exclusions.append({
            "path": _relative(root, path),
            "reason": "outside_cargo_module_scope",
        })
    lock = _regular(root, root / "Cargo.lock", required=False)
    lockfile: dict[str, Any] = {"status": "absent"}
    if lock is not None:
        lockfile = {
            "status": "present",
            "path": _relative(root, lock),
            "content_sha256": _sha256(lock),
            "size": os.lstat(lock).st_size,
        }
    return validate_rust_source_scope({
        "schema_version": RUST_SCOPE_SCHEMA_VERSION,
        "inventory": RUST_SCOPE_INVENTORY,
        "status": "exact_hits_partial_scope" if partial else "complete_exact",
        "manifests": sorted(manifests, key=lambda item: item["path"]),
        "lockfile": lockfile,
        "packages": sorted(packages, key=lambda item: (item["root"], item["name"])),
        "targets": targets,
        "source_paths": sorted(_relative(root, path) for path in reachable),
        "exclusions": sorted(exclusions, key=lambda item: (item["path"], item["reason"])),
        "execution": {
            "cargo": False,
            "rustc": False,
            "rust_analyzer": False,
            "build_scripts": False,
            "proc_macros": False,
            "network": False,
        },
    })
