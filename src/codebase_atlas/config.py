"""Portable project configuration and local runtime discovery."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tomllib


CONFIG_NAME = ".codebase-atlas.toml"


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
    node: Path
    cbm_binary: Path
    serena_python: Path
    data_dir: Path
    project: str = ""
    node_bin_dir: Path | None = None

    def __post_init__(self) -> None:
        for name in ("repository", "data_dir"):
            object.__setattr__(self, name, getattr(self, name).resolve())
        # Preserve virtualenv interpreter symlinks; resolving them bypasses
        # pyvenv.cfg and silently loses the installed Serena environment.
        for name in ("node", "cbm_binary", "serena_python"):
            object.__setattr__(self, name, getattr(self, name).absolute())
        if self.node_bin_dir is not None:
            object.__setattr__(self, "node_bin_dir", self.node_bin_dir.absolute())

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
        data_dir: Path | None = None,
    ) -> "AtlasConfig":
        repo = repository.resolve()
        selected_language = language or ("typescript" if (repo / "tsconfig.json").is_file() else "python")
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
            node_bin_dir=node_bin_dir or discovered_node.parent,
        )

    @classmethod
    def load(cls, path: Path) -> "AtlasConfig":
        value = tomllib.loads(path.read_text(encoding="utf-8"))
        runtime = value["runtime"]
        project = value["project"]
        node_bin = runtime.get("node_bin_dir", "")
        return cls(
            Path(project["repository"]), project["language"],
            Path(runtime["node"]), Path(runtime["cbm_binary"]),
            Path(runtime["serena_python"]), Path(project["data_dir"]),
            project.get("cbm_project", ""), Path(node_bin) if node_bin else None,
        )

    def with_project(self, project: str) -> "AtlasConfig":
        return replace(self, project=project)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        quote = lambda value: str(value).replace("\\", "\\\\").replace('"', '\\"')
        node_bin = quote(self.node_bin_dir) if self.node_bin_dir else ""
        text = (
            "schema_version = 1\n\n[project]\n"
            f'repository = "{quote(self.repository)}"\n'
            f'language = "{self.language}"\n'
            f'data_dir = "{quote(self.data_dir)}"\n'
            f'cbm_project = "{quote(self.project)}"\n\n[runtime]\n'
            f'node = "{quote(self.node)}"\n'
            f'node_bin_dir = "{node_bin}"\n'
            f'cbm_binary = "{quote(self.cbm_binary)}"\n'
            f'serena_python = "{quote(self.serena_python)}"\n'
        )
        path.write_text(text, encoding="utf-8")


def diagnose(config: AtlasConfig) -> list[dict[str, str | bool]]:
    checks = [
        ("python_version", sys.version_info >= (3, 11), f"{sys.version_info.major}.{sys.version_info.minor}"),
        ("repository", config.repository.is_dir(), str(config.repository)),
        ("node", config.node.is_file(), str(config.node)),
        ("codebase_memory", config.cbm_binary.is_file(), str(config.cbm_binary)),
        ("serena_python", config.serena_python.is_file(), str(config.serena_python)),
        ("node_bin_dir", bool(config.node_bin_dir and config.node_bin_dir.is_dir()), str(config.node_bin_dir or "")),
        ("ts_analyzer", config.analyzer.is_file(), str(config.analyzer)),
        ("serena_runner", config.serena_runner.is_file(), str(config.serena_runner)),
        ("indexed_project", bool(config.project), config.project or "run codebase-atlas index"),
    ]
    return [{"name": name, "ok": ok, "detail": detail} for name, ok, detail in checks]
