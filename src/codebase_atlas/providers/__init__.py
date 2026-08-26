"""Provider adapters owned by Codebase Atlas."""

from pathlib import Path

from ..languages import capability

from .cbm_impact import CodebaseMemoryImpactProvider
from .go import GoAdapterError, GoSemanticProvider
from .python_callers import PythonExactCallerProvider
from .serena import SerenaSemanticProvider
from .ts_tests import TypeScriptTestProvider


def direct_provider_for(
    language: str,
    *,
    repository: Path,
    data_root: Path,
    go: Path | None,
    gopls: Path | None,
    workspace_root: Path | None,
) -> GoSemanticProvider | None:
    selected = capability(language)
    if not selected.live_provider:
        return None
    if not go or not gopls or not workspace_root:
        raise ValueError(f"{language} Provider runtime is incomplete")
    if selected.provider != GoSemanticProvider.name:
        raise ValueError(f"no direct Provider factory for {language}")
    return GoSemanticProvider(repository, data_root, go, gopls, workspace_root)

__all__ = [
    "CodebaseMemoryImpactProvider",
    "GoAdapterError",
    "GoSemanticProvider",
    "PythonExactCallerProvider",
    "SerenaSemanticProvider",
    "TypeScriptTestProvider",
    "direct_provider_for",
]
