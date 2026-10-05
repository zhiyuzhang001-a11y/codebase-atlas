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

from .provider_layout import (
    atlas_data_root,
    inspect_provider_root,
    provider_project_identity,
    shared_provider_root,
)
from .languages import default_language, get_language


CONFIG_NAME = ".codebase-atlas.toml"
LEGACY_PROVIDER_LAYOUT = "legacy-project-v0"
SHARED_PROVIDER_LAYOUT = "shared-v1"


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
    return atlas_data_root() / f"{repository.name}-{digest}"


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
    provider_layout: str = LEGACY_PROVIDER_LAYOUT
    legacy_project: str = ""
    rust_runtime_receipt: Path | None = None

    def __post_init__(self) -> None:
        get_language(self.language)
        for name in ("repository", "data_dir"):
            object.__setattr__(self, name, getattr(self, name).resolve())
        # Preserve virtualenv interpreter symlinks; resolving them bypasses
        # pyvenv.cfg and silently loses the installed Serena environment.
        for name in ("node", "cbm_binary", "serena_python"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, value.absolute())
            elif self.language != "rust" or self.rust_runtime_receipt is None:
                raise ValueError(f"missing runtime: {name}")
        if self.rust_runtime_receipt is not None:
            if self.language != "rust":
                raise ValueError("Rust runtime receipt requires language=rust")
            object.__setattr__(self, "rust_runtime_receipt", self.rust_runtime_receipt.absolute())
        if self.node_bin_dir is not None:
            object.__setattr__(self, "node_bin_dir", self.node_bin_dir.absolute())
        if self.tsconfig is not None:
            object.__setattr__(self, "tsconfig", self.tsconfig)
        if self.provider_layout not in {LEGACY_PROVIDER_LAYOUT, SHARED_PROVIDER_LAYOUT}:
            raise ValueError(f"unsupported Provider layout: {self.provider_layout}")
        if self.provider_layout == SHARED_PROVIDER_LAYOUT and self.project != self.shared_project:
            raise ValueError("shared Provider layout requires the deterministic project identity")

    @property
    def cache_dir(self) -> Path:
        return (
            self.shared_cache_dir
            if self.provider_layout == SHARED_PROVIDER_LAYOUT
            else self.legacy_cache_dir
        )

    @property
    def legacy_cache_dir(self) -> Path:
        return self.data_dir / "codebase-memory"

    @property
    def shared_cache_dir(self) -> Path:
        """M32 account-level target; activation remains an explicit migration step."""
        return shared_provider_root()

    @property
    def shared_project(self) -> str:
        return provider_project_identity(self.repository)

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
        rust_runtime_receipt: Path | None = None,
    ) -> "AtlasConfig":
        repo = repository.resolve()
        selected_language = language or default_language(repo, tsconfig=tsconfig)
        get_language(selected_language)
        if selected_language == "rust":
            if rust_runtime_receipt is None:
                raise ValueError("missing Rust runtime: verified installation receipt required")
            return cls(
                repo, "rust", None, None, None,
                (data_dir or default_data_dir(repo)).resolve(),
                project=provider_project_identity(repo),
                rust_runtime_receipt=rust_runtime_receipt,
            )
        discovered_node = node or _which("node", "ATLAS_NODE")
        discovered_cbm = cbm_binary or _which("codebase-memory-mcp", "ATLAS_CBM_BINARY")
        discovered_serena = serena_python or (
            Path(os.environ["ATLAS_SERENA_PYTHON"]).absolute()
            if os.environ.get("ATLAS_SERENA_PYTHON") else None
        )
        missing = [
            name for name, value in (
                ("Node.js (--node or ATLAS_NODE)", discovered_node),
                ("Codebase Memory (--cbm-binary or ATLAS_CBM_BINARY)", discovered_cbm),
                ("Serena Python (--serena-python or ATLAS_SERENA_PYTHON)", discovered_serena),
            ) if value is None
        ]
        if missing:
            raise ValueError("missing runtime: " + "; ".join(missing))
        return cls(
            repo, selected_language, discovered_node, discovered_cbm,
            discovered_serena, (data_dir or default_data_dir(repo)).resolve(),
            project=provider_project_identity(repo),
            node_bin_dir=node_bin_dir or discovered_node.parent,
            tsconfig=tsconfig,
            provider_layout=SHARED_PROVIDER_LAYOUT,
        )

    @classmethod
    def load(cls, path: Path) -> "AtlasConfig":
        value = tomllib.loads(path.read_text(encoding="utf-8"))
        runtime = value["runtime"]
        project = value["project"]
        schema = value.get("schema_version")
        if isinstance(schema, bool) or schema not in {1, 2} or (
            schema == 2 and (project.get("language") != "rust" or not runtime.get("rust_runtime_receipt"))
        ):
            raise ValueError("unsupported Atlas configuration schema")
        node_bin = runtime.get("node_bin_dir", "")
        tsconfig = project.get("tsconfig", "")
        return cls(
            Path(project["repository"]), project["language"],
            Path(runtime["node"]) if runtime.get("node") else None,
            Path(runtime["cbm_binary"]) if runtime.get("cbm_binary") else None,
            Path(runtime["serena_python"]) if runtime.get("serena_python") else None,
            Path(project["data_dir"]),
            project.get("cbm_project", ""), Path(node_bin) if node_bin else None,
            Path(tsconfig) if tsconfig else None,
            project.get("provider_layout", LEGACY_PROVIDER_LAYOUT),
            project.get("legacy_cbm_project", ""),
            Path(runtime["rust_runtime_receipt"]) if runtime.get("rust_runtime_receipt") else None,
        )

    def with_project(self, project: str) -> "AtlasConfig":
        return replace(self, project=project)

    def render(self) -> str:
        quote = lambda value: str(value).replace("\\", "\\\\").replace('"', '\\"')
        node_bin = quote(self.node_bin_dir) if self.node_bin_dir else ""
        schema = 2 if self.rust_runtime_receipt is not None else 1
        rendered = (
            f"schema_version = {schema}\n\n[project]\n"
            f'repository = "{quote(self.repository)}"\n'
            f'language = "{self.language}"\n'
            f'data_dir = "{quote(self.data_dir)}"\n'
            f'cbm_project = "{quote(self.project)}"\n'
            f'provider_layout = "{self.provider_layout}"\n'
            f'legacy_cbm_project = "{quote(self.legacy_project)}"\n'
            f'tsconfig = "{quote(self.tsconfig) if self.tsconfig else ""}"\n\n[runtime]\n'
            f'node = "{quote(self.node) if self.node else ""}"\n'
            f'node_bin_dir = "{node_bin}"\n'
            f'cbm_binary = "{quote(self.cbm_binary) if self.cbm_binary else ""}"\n'
            f'serena_python = "{quote(self.serena_python) if self.serena_python else ""}"\n'
        )
        if self.rust_runtime_receipt is not None:
            rendered += f'rust_runtime_receipt = "{quote(self.rust_runtime_receipt)}"\n'
        return rendered
    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as stream:
            stream.write(self.render())

    def write_exclusive(self, path: Path) -> None:
        """Create a new config without replacing an existing path."""
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
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
        with os.fdopen(descriptor, "r+", encoding="utf-8", newline="") as stream:
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
    kwargs = {} if runner is None else {"runner": runner}
    checks = runtime_checks(
        config.repository,
        language=config.language,
        node=config.node,
        cbm_binary=config.cbm_binary,
        serena_python=config.serena_python,
        node_bin_dir=config.node_bin_dir,
        tsconfig=config.tsconfig,
        rust_runtime_receipt=config.rust_runtime_receipt,
        **kwargs,
    )
    if config.language == "rust":
        from .languages import get_language
        from .rust_project import load_rust_service
        generation_ok, detail = False, "Rust is not product-enabled"
        if get_language("rust").public_enabled and all(
            item["ok"] for item in checks if item.get("required", True)
        ):
            try:
                service = load_rust_service(config)
                generation_ok = True
                detail = "exact Rust generation and checksum-verified T1 artifact agree"
                service.close()
            except (OSError, ValueError, RuntimeError) as exc:
                detail = str(exc)
        checks.extend([
            {"name": "indexed_project", "ok": bool(config.project), "required": True,
             "path": "", "version": "", "detail": config.project or "project identity missing",
             "remediation": "" if config.project else "enable the exact Rust project"},
            {"name": "index_freshness", "ok": bool(freshness["ok"]), "required": True,
             "path": str(config.data_dir / "index-state.json"), "version": "",
             "detail": f"{freshness['status']}: {freshness['reason']}",
             "remediation": "" if freshness["ok"] else "refresh this Rust project's index"},
            {"name": "rust_generation", "ok": generation_ok, "required": True,
             "path": str(config.data_dir), "version": "", "detail": detail,
             "remediation": "" if generation_ok else "verify or rebuild this Rust generation"},
        ])
        return checks
    provider_database = provider_database_health(config.cache_dir, config.project)
    shared_root = inspect_provider_root(config.shared_cache_dir)
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
        {
            "name": "shared_provider_target", "ok": shared_root.ready, "required": False,
            "path": str(shared_root.path), "version": "v1",
            "detail": (
                f"{shared_root.status}; project={config.shared_project}; "
                + (
                    "shared layout is active for this project"
                    if config.provider_layout == SHARED_PROVIDER_LAYOUT
                    else "target is not activated until explicit Provider migration"
                )
            ),
            "remediation": (
                "" if shared_root.ready else
                "do not change this path manually; run the explicit Provider migration"
            ),
        },
    ])
    return checks
