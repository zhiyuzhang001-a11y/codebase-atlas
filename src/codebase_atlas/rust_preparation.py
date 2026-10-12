"""Separate, non-executing preparation of an existing official Rust install.

This does not install a missing toolchain, mutate rustup/Cargo configuration,
prepare project dependencies, or enable a project. Archives alone never confer
trust: every installed component file and license is compared to the source lock.
"""
from __future__ import annotations

from pathlib import Path

from .release_installation import current_platform_target
from .rust_acquisition import acquire_components, component_acquisition_plan
from .rust_installation import (
    _private_directory, _runtime_from_document, load_toolchain_receipt,
    save_toolchain_receipt, toolchain_store, verify_existing_toolchain,
)
from .rust_runtime import PINNED_TOOLCHAIN, RustRuntimeError


def _locations(repository: Path, root: Path) -> tuple[Path, Path, Path, str, Path]:
    repo = repository.resolve(strict=True)
    root = root.absolute()
    store = toolchain_store().absolute()
    for path in (root, store):
        if path.resolve() != path or any(ancestor.is_symlink() for ancestor in (path, *path.parents)):
            raise RustRuntimeError("Rust preparation requires canonical non-symlink paths")
        if path.is_relative_to(repo):
            raise RustRuntimeError("Rust preparation tools and store must be outside the project")
    if not repo.is_dir() or not root.is_dir():
        raise RustRuntimeError("Rust preparation requires an existing toolchain; installation needs a separate plan")
    if store.exists():
        _private_directory(store)
    target = current_platform_target()
    receipt = store / PINNED_TOOLCHAIN / target / "receipt.json"
    return repo, root, store, target, receipt


def _document(repo: Path, root: Path, store: Path, target: str, receipt: Path,
              archives: dict[str, Path] | None) -> tuple[dict, bool]:
    if receipt.exists() or receipt.is_symlink():
        document = load_toolchain_receipt(receipt, store=store)
        if document["root"] != str(root):
            raise RustRuntimeError("Rust preparation conflicts with the existing toolchain receipt")
        reused = True
    else:
        if archives is None:
            plan = component_acquisition_plan(repo, target)
            if any(not identity["cached"] for identity in plan.values()):
                raise RustRuntimeError("Rust preparation needs verified component archives; authorize acquisition separately")
            archives = {name: identity["path"] for name, identity in plan.items()}
        document = verify_existing_toolchain(root, archives, target)
        reused = False
    # Detect unsafe project/ancestor/user configuration and inherited overrides
    # BEFORE writing a receipt. No version/metadata/LSP probes are executed.
    _runtime_from_document(document).environment(repo)
    return document, reused


def plan_existing_toolchain(repository: Path, root: Path, *,
                            archives: dict[str, Path] | None = None) -> dict:
    repo, root, store, target, receipt = _locations(repository, root)
    result = {"schema_version": 1, "mode": "plan", "repository": str(repo),
              "toolchain": PINNED_TOOLCHAIN, "target": target, "root": str(root),
              "receipt": str(receipt), "project_writes": [], "executes_tools": False}
    if archives is None and not (receipt.exists() or receipt.is_symlink()):
        acquisition = component_acquisition_plan(repo, target)
        missing = [name for name, identity in acquisition.items() if not identity["cached"]]
        if missing:
            return {**result, "status": "needs_archives", "missing_components": sorted(missing),
                    "next_action": "Provide source-lock-verified archives or explicitly authorize acquisition"}
    document, reused = _document(repo, root, store, target, receipt, archives)
    return {**result, "status": "planned", "reuse_receipt": reused,
            "verified_files": len(document["files"]), "verified_licenses": len(document["licenses"])}


def prepare_existing_toolchain(repository: Path, root: Path, *,
                               archives: dict[str, Path] | None = None,
                               network_authorized: bool = False) -> dict:
    repo, root, store, target, receipt = _locations(repository, root)
    # A valid existing receipt needs no downloads, even if acquisition authority
    # was supplied. Revalidation still checks all installed bytes and configuration.
    if archives is None and not (receipt.exists() or receipt.is_symlink()):
        archives = acquire_components(repo, target, network_authorized=network_authorized)
    document, reused = _document(repo, root, store, target, receipt, archives)
    published = save_toolchain_receipt(document, store)
    return {"schema_version": 1, "mode": "apply", "status": "prepared",
            "repository": str(repo), "toolchain": PINNED_TOOLCHAIN, "target": target,
            "root": str(root), "receipt": str(published), "reuse_receipt": reused,
            "verified_files": len(document["files"]), "verified_licenses": len(document["licenses"]),
            "project_writes": [], "executes_tools": False, "project_enabled": False}
