"""Immutable language capability registry for product routing decisions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class LanguageSpec:
    language_id: str
    source_extensions: frozenset[str]
    manifest_inputs: tuple[str, ...]
    capabilities: Mapping[str, str]
    public_enabled: bool

    def capability(self, name: str) -> str:
        try:
            return self.capabilities[name]
        except KeyError as exc:
            raise ValueError(
                f"unknown capability for {self.language_id}: {name}"
            ) from exc


def _capabilities(**values: str) -> Mapping[str, str]:
    return MappingProxyType(dict(values))


_LANGUAGES: Mapping[str, LanguageSpec] = MappingProxyType({
    "python": LanguageSpec(
        "python",
        frozenset({".py"}),
        ("pyproject.toml",),
        _capabilities(
            source_scope="enabled", syntax_map="enabled",
            definition="enabled", references="enabled",
            callers="enabled", callees="enabled", related_tests="enabled",
            impact="enabled", scip="unsupported",
        ),
        True,
    ),
    "typescript": LanguageSpec(
        "typescript",
        frozenset({".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs"}),
        ("tsconfig.json", "package.json"),
        _capabilities(
            source_scope="enabled", syntax_map="enabled",
            definition="enabled", references="enabled",
            callers="enabled", callees="enabled", related_tests="enabled",
            impact="enabled", scip="unsupported",
        ),
        True,
    ),
    "rust": LanguageSpec(
        "rust",
        frozenset({".rs"}),
        ("Cargo.toml", "Cargo.lock"),
        _capabilities(
            source_scope="required", syntax_map="required",
            definition="required", references="required",
            callers="unsupported", callees="unsupported",
            related_tests="unsupported", impact="unsupported",
            implementations="unsupported", scip="evaluation_only",
        ),
        False,
    ),
})


def get_language(language: str) -> LanguageSpec:
    try:
        return _LANGUAGES[language]
    except KeyError as exc:
        raise ValueError(f"unsupported project language: {language}") from exc


def all_language_ids() -> tuple[str, ...]:
    return tuple(_LANGUAGES)


def public_language_choices() -> tuple[str, ...]:
    return tuple(
        language_id
        for language_id, spec in _LANGUAGES.items()
        if spec.public_enabled
    )


def default_language(repository: Path, *, tsconfig: Path | None = None) -> str:
    return (
        "typescript"
        if tsconfig is not None or (repository / "tsconfig.json").is_file()
        else "python"
    )
