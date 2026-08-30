"""Account-scoped Codebase Memory layout and project identity contracts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Mapping


PROVIDER_LAYOUT = "v1"


def atlas_data_root() -> Path:
    configured = os.environ.get("XDG_DATA_HOME")
    base = Path(configured) if configured else Path.home() / ".local/share"
    return (base / "codebase-atlas").resolve()


def shared_provider_root() -> Path:
    """Return the one default Provider root for this account and layout ABI."""
    return atlas_data_root() / "_shared" / "codebase-memory" / PROVIDER_LAYOUT


def provider_project_identity(repository: Path) -> str:
    """Return a readable, path-stable Provider project identity."""
    canonical = repository.resolve()
    readable = re.sub(r"[^A-Za-z0-9._-]+", "-", canonical.name).strip("-._")
    readable = (readable or "project")[:40]
    digest = hashlib.sha256(str(canonical).encode("utf-8")).hexdigest()[:24]
    return f"atlas-{readable}-{digest}"


def provider_environment(
    cache_dir: Path,
    repository: Path,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build one session environment without broadening its repository root."""
    environment = dict(os.environ if base is None else base)
    environment["CBM_CACHE_DIR"] = str(cache_dir.resolve())
    environment["CBM_ALLOWED_ROOT"] = str(repository.resolve())
    return environment


@dataclass(frozen=True)
class ProviderRootStatus:
    status: str
    path: Path
    detail: str = ""

    @property
    def ready(self) -> bool:
        return self.status in {"missing", "ready"}


def inspect_provider_root(path: Path) -> ProviderRootStatus:
    """Read-only validation; creation/permission repair belongs to explicit apply."""
    candidate = path.absolute()
    try:
        metadata = os.lstat(candidate)
    except FileNotFoundError:
        return ProviderRootStatus("missing", candidate)
    if stat.S_ISLNK(metadata.st_mode):
        return ProviderRootStatus("unsafe_symlink", candidate, "provider root must not be a symlink")
    if not stat.S_ISDIR(metadata.st_mode):
        return ProviderRootStatus("not_directory", candidate, "provider root must be a directory")
    if os.name != "nt" and metadata.st_mode & 0o077:
        return ProviderRootStatus(
            "permissions_too_broad", candidate, "provider root must not grant group/other access"
        )
    return ProviderRootStatus("ready", candidate.resolve())
