"""Portable project configuration and local runtime discovery."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import os
from pathlib import Path
import shutil
import stat
import sys
import tomllib

from .languages import capability, go_workspace_root, select_language


CONFIG_NAME = ".codebase-atlas.toml"


def _require_regular_identity(path: Path, expected_identity: tuple[int, int]) -> None:
    """Require the literal path to retain one expected regular-file identity."""
    current = os.lstat(path)
    identity = (current.st_dev, current.st_ino)
    if not stat.S_ISREG(current.st_mode) or identity != expected_identity:
        raise ValueError("config identity changed before publication")


def _asset(name: str) -> Path:
    source = Path(__file__).resolve().parents[2] / "scripts" / name
    if source.is_file():
        return source
    return Path(sys.prefix) / "share" / "codebase-atlas" / name


def _which(name: str, environment_name: str) -> Path | None:
    explicit = os.environ.get(environment_name)
    found = explicit or shutil.which(name)
    return Path(found).absolute() if found else None


def default_data_dir(repository: Path) -> Path:
    digest = hashlib.sha256(str(repository.resolve()).encode()).hexdigest()[:12]
    root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return root / "codebase-atlas" / f"{repository.name}-{digest}"


@dataclass(frozen=True)
class AtlasConfig:
    repository: Path
    language: str
    node: Path | None
    cbm_binary: Path | None
    serena_python: Path | None
    data_dir: Path
    project: str = ""
    node_bin_dir: Path | None = None
    tsconfig: Path | None = None
    go: Path | None = None
    gopls: Path | None = None
    go_workspace: Path | None = None

    def __post_init__(self) -> None:
        for name in ("repository", "data_dir"):
            object.__setattr__(self, name, getattr(self, name).resolve())
        # Preserve virtualenv interpreter symlinks; resolving them bypasses
        # pyvenv.cfg and silently loses the installed Serena environment.
        for name in ("node", "cbm_binary", "serena_python"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, value.absolute())
        if self.node_bin_dir is not None:
            object.__setattr__(self, "node_bin_dir", self.node_bin_dir.absolute())
        if self.tsconfig is not None:
            object.__setattr__(self, "tsconfig", self.tsconfig)
        for name in ("go", "gopls"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, value.absolute())
        if self.go_workspace is not None:
            object.__setattr__(self, "go_workspace", self.go_workspace.resolve())

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "codebase-memory"

    @property
    def serena_home(self) -> Path:
        return self.data_dir / "serena-home"

    @property
    def metadata_root(self) -> Path:
        return self.data_dir / "serena-metadata"

    @property
    def analyzer(self) -> Path:
        return _asset("ts_test_analyzer.mjs")

    @property
    def serena_runner(self) -> Path:
        return _asset("serena_runner.py")

    @classmethod
    def discover(
        cls,
        repository: Path,
        *,
        language: str | None = None,
        node: Path | None = None,
        cbm_binary: Path | None = None,
        serena_python: Path | None = None,
        node_bin_dir: Path | None = None,
        tsconfig: Path | None = None,
        data_dir: Path | None = None,
        go: Path | None = None,
        gopls: Path | None = None,
        go_workspace: Path | None = None,
    ) -> "AtlasConfig":
        repo = repository.resolve()
        selected_language = select_language(repo, language)
        selected_capability = capability(selected_language)
        discovered_node = node or _which("node", "ATLAS_NODE")
        discovered_cbm = cbm_binary or _which("codebase-memory-mcp", "ATLAS_CBM_BINARY")
        discovered_serena = serena_python or (
            Path(os.environ["ATLAS_SERENA_PYTHON"]).absolute()
            if os.environ.get("ATLAS_SERENA_PYTHON") else None
        )
        discovered_go = go or _which("go", "ATLAS_GO")
        discovered_gopls = gopls or _which("gopls", "ATLAS_GOPLS")
        missing = [
            name for name, value in (
                ("Node.js (--node or ATLAS_NODE)", discovered_node if selected_capability.requires_node else True),
                ("Codebase Memory (--cbm-binary or ATLAS_CBM_BINARY)", discovered_cbm if selected_capability.requires_cbm else True),
                ("Serena Python (--serena-python or ATLAS_SERENA_PYTHON)", discovered_serena if selected_capability.requires_serena else True),
                ("Go (--go or ATLAS_GO)", discovered_go if selected_capability.requires_go else True),
                ("gopls (--gopls or ATLAS_GOPLS)", discovered_gopls if selected_capability.requires_gopls else True),
            ) if value is None
        ]
        if missing:
            raise ValueError("missing runtime: " + "; ".join(missing))
        return cls(
            repo, selected_language, discovered_node, discovered_cbm,
            discovered_serena, (data_dir or default_data_dir(repo)).resolve(),
            node_bin_dir=node_bin_dir or (discovered_node.parent if discovered_node else None),
            tsconfig=tsconfig,
            go=discovered_go if selected_capability.requires_go else go,
            gopls=discovered_gopls if selected_capability.requires_gopls else gopls,
            go_workspace=(
                go_workspace_root(repo, go_workspace)
                if selected_capability.requires_go else None
            ),
        )

    @classmethod
    def load(cls, path: Path) -> "AtlasConfig":
        value = tomllib.loads(path.read_text(encoding="utf-8"))
        runtime = value["runtime"]
        project = value["project"]
        node_bin = runtime.get("node_bin_dir", "")
        tsconfig = project.get("tsconfig", "")
        go = runtime.get("go", "")
        gopls = runtime.get("gopls", "")
        workspace = project.get("go_workspace", "")
        return cls(
            Path(project["repository"]), project["language"],
            Path(runtime["node"]) if runtime.get("node") else None,
            Path(runtime["cbm_binary"]) if runtime.get("cbm_binary") else None,
            Path(runtime["serena_python"]) if runtime.get("serena_python") else None,
            Path(project["data_dir"]),
            project.get("cbm_project", ""), Path(node_bin) if node_bin else None,
            Path(tsconfig) if tsconfig else None,
            Path(go) if go else None, Path(gopls) if gopls else None,
            Path(workspace) if workspace else None,
        )

    def with_project(self, project: str) -> "AtlasConfig":
        return replace(self, project=project)

    def render(self) -> str:
        quote = lambda value: str(value).replace("\\", "\\\\").replace('"', '\\"')
        node_bin = quote(self.node_bin_dir) if self.node_bin_dir else ""
        return (
            "schema_version = 1\n\n[project]\n"
            f'repository = "{quote(self.repository)}"\n'
            f'language = "{self.language}"\n'
            f'data_dir = "{quote(self.data_dir)}"\n'
            f'cbm_project = "{quote(self.project)}"\n'
            f'tsconfig = "{quote(self.tsconfig) if self.tsconfig else ""}"\n'
            f'go_workspace = "{quote(self.go_workspace) if self.go_workspace else ""}"\n\n[runtime]\n'
            f'node = "{quote(self.node) if self.node else ""}"\n'
            f'node_bin_dir = "{node_bin}"\n'
            f'cbm_binary = "{quote(self.cbm_binary) if self.cbm_binary else ""}"\n'
            f'serena_python = "{quote(self.serena_python) if self.serena_python else ""}"\n'
            f'go = "{quote(self.go) if self.go else ""}"\n'
            f'gopls = "{quote(self.gopls) if self.gopls else ""}"\n'
        )
    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render(), encoding="utf-8")

    def write_exclusive(self, path: Path) -> None:
        """Create a new config without replacing an existing path."""
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(self.render())

    def write_verified(self, path: Path, expected_identity: tuple[int, int]) -> None:
        """Rewrite only the already-approved regular file, on every supported OS."""
        _require_regular_identity(path, expected_identity)
        nofollow = getattr(os, "O_NOFOLLOW", None)
        flags = os.O_RDWR | (nofollow if isinstance(nofollow, int) else 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            opened_identity = (opened.st_dev, opened.st_ino)
            if not stat.S_ISREG(opened.st_mode) or opened_identity != expected_identity:
                raise ValueError("config identity changed before publication")
            # Windows has no O_NOFOLLOW. Rechecking the literal path after open
            # rejects a symlink or replacement race while all writes remain
            # bound to the already-verified handle.
            _require_regular_identity(path, expected_identity)
        except (OSError, ValueError):
            os.close(descriptor)
            raise
        with os.fdopen(descriptor, "r+", encoding="utf-8") as stream:
            original = stream.read()
            try:
                stream.seek(0)
                stream.truncate()
                stream.write(self.render())
                stream.flush()
                os.fsync(stream.fileno())
                _require_regular_identity(path, expected_identity)
            except (OSError, ValueError):
                stream.seek(0)
                stream.truncate()
                stream.write(original)
                stream.flush()
                os.fsync(stream.fileno())
                raise

    @staticmethod
    def restore_verified(
        path: Path, expected_identity: tuple[int, int], payload: bytes
    ) -> None:
        """Restore exact prior bytes only through the approved config file."""
        _require_regular_identity(path, expected_identity)
        nofollow = getattr(os, "O_NOFOLLOW", None)
        flags = os.O_RDWR | (nofollow if isinstance(nofollow, int) else 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != expected_identity
            ):
                raise ValueError("config identity changed before restoration")
            _require_regular_identity(path, expected_identity)
            with os.fdopen(descriptor, "r+b") as stream:
                descriptor = -1
                stream.seek(0)
                stream.truncate()
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
                _require_regular_identity(path, expected_identity)
        finally:
            if descriptor >= 0:
                os.close(descriptor)


def diagnose(config: AtlasConfig, *, runner=None) -> list[dict[str, object]]:
    from .index_state import index_freshness, provider_database_health
    from .runtime import runtime_checks

    freshness = index_freshness(config.data_dir, config.repository, config.project)
    provider_database = provider_database_health(config.cache_dir, config.project)
    kwargs = {} if runner is None else {"runner": runner}
    checks = runtime_checks(
        config.repository,
        language=config.language,
        node=config.node,
        cbm_binary=config.cbm_binary,
        serena_python=config.serena_python,
        node_bin_dir=config.node_bin_dir,
        tsconfig=config.tsconfig,
        go=config.go,
        gopls=config.gopls,
        go_workspace=config.go_workspace,
        **kwargs,
    )
    provider_database = (
        {"status": "live", "ok": True, "reason": "provider_is_live"}
        if capability(config.language).live_provider else provider_database
    )
    checks.extend([
        {
            "name": "indexed_project", "ok": bool(config.project), "required": True,
            "path": "", "version": "",
            "detail": config.project or "project identity has not been indexed",
            "remediation": "" if config.project else "run 'codebase-atlas index'",
        },
        {
            "name": "index_freshness", "ok": bool(freshness["ok"]), "required": True,
            "path": str(config.data_dir / "index-state.json"), "version": "",
            "detail": f"{freshness['status']}: {freshness['reason']}",
            "remediation": "" if freshness["ok"] else "run 'codebase-atlas update'",
        },
        {
            "name": "provider_database", "ok": bool(provider_database["ok"]), "required": True,
            "path": str(config.cache_dir), "version": "",
            "detail": f"{provider_database['status']}: {provider_database['reason']}",
            "remediation": "" if provider_database["ok"] else "run 'codebase-atlas index'",
        },
    ])
    return checks
