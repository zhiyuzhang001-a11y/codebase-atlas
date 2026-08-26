"""Central language capability registry and deterministic discovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LanguageCapability:
    name: str
    suffixes: tuple[str, ...]
    markers: tuple[str, ...]
    provider: str
    requires_node: bool = True
    requires_cbm: bool = True
    requires_serena: bool = True
    requires_tsconfig: bool = False
    requires_go: bool = False
    requires_gopls: bool = False
    live_provider: bool = False


LANGUAGES: dict[str, LanguageCapability] = {
    "python": LanguageCapability(
        "python", (".py",), ("pyproject.toml", "setup.py", "setup.cfg"),
        "serena+codebase-memory",
    ),
    "typescript": LanguageCapability(
        "typescript", (".ts", ".tsx", ".js", ".jsx"),
        ("tsconfig.json",), "serena+typescript", requires_tsconfig=True,
    ),
    "go": LanguageCapability(
        "go", (".go",), ("go.work", "go.mod"), "gopls-0.23.0",
        requires_node=False, requires_cbm=False, requires_serena=False,
        requires_go=True, requires_gopls=True, live_provider=True,
    ),
}

LANGUAGE_CHOICES = tuple(LANGUAGES)


def capability(language: str) -> LanguageCapability:
    try:
        return LANGUAGES[language]
    except KeyError as exc:
        raise ValueError(f"unsupported language: {language}") from exc


def detected_languages(repository: Path) -> tuple[str, ...]:
    """Return deterministic project languages while excluding dependency trees."""

    repo = repository.resolve()
    excluded = {".git", "node_modules", "vendor", ".codebase-atlas", ".evaluation-data"}
    files = tuple(
        path for path in repo.rglob("*")
        if path.is_file() and not excluded.intersection(path.relative_to(repo).parts)
    )
    found: list[str] = []
    for name, item in LANGUAGES.items():
        marker = any(path.name in item.markers for path in files)
        source = any(path.suffix in item.suffixes for path in files)
        if marker or source:
            found.append(name)
    return tuple(found)


def select_language(repository: Path, explicit: str | None = None) -> str:
    if explicit is not None:
        capability(explicit)
        return explicit
    found = detected_languages(repository)
    if len(found) > 1:
        raise ValueError(
            "language_ambiguous: detected " + ", ".join(found)
            + "; pass --language explicitly"
        )
    if found:
        return found[0]
    # Preserve the historic fallback for unmarked repositories.
    return "python"


def go_workspace_root(repository: Path, explicit: Path | None = None) -> Path:
    repo = repository.resolve()
    if explicit is not None:
        selected = explicit if explicit.is_absolute() else repo / explicit
        selected = selected.resolve()
        if selected != repo and repo not in selected.parents:
            raise ValueError("go_workspace_out_of_scope")
        if not (selected / "go.work").is_file() and not (selected / "go.mod").is_file():
            raise ValueError("go_build_context_incomplete")
        return selected
    workspaces = sorted(path.parent for path in repo.rglob("go.work") if "vendor" not in path.parts)
    if len(workspaces) == 1:
        return workspaces[0]
    if len(workspaces) > 1:
        raise ValueError("go_workspace_ambiguous: pass --go-workspace")
    modules = sorted(path.parent for path in repo.rglob("go.mod") if "vendor" not in path.parts)
    if len(modules) == 1:
        return modules[0]
    if not modules:
        raise ValueError("go_build_context_incomplete: no go.work or go.mod")
    raise ValueError("go_workspace_ambiguous: pass --go-workspace")
