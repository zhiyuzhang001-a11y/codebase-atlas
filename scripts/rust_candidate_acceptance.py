#!/usr/bin/env python3
"""Exercise installed-wheel Rust T0/T1/T2 behavior without public enablement."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import textwrap
import venv


PROBE = r'''
import json
from pathlib import Path
import sys

from codebase_atlas.config import AtlasConfig
from codebase_atlas.languages import get_language, public_language_choices
from codebase_atlas.providers.rust_analyzer import RustAnalyzerProvider
from codebase_atlas.providers.rust_syntax import RustSyntaxIndex, load_rust_syntax_pointer
from codebase_atlas.refresh_planner import load_generation_manifest
from codebase_atlas.rust_refresh import RustRefreshCoordinator

repository = Path(sys.argv[1]).resolve()
data = Path(sys.argv[2]).resolve()
scanner = Path(sys.argv[3]).resolve()
analyzer = Path(sys.argv[4]).resolve()
project = "installed-rust-candidate"
config = AtlasConfig(
    repository, "rust", Path(sys.executable), Path(sys.executable),
    Path(sys.executable), data, project=project,
)
assert get_language("rust").public_enabled is False
assert "rust" not in public_language_choices()
refresh = RustRefreshCoordinator(config, scanner).refresh(timeout_seconds=120)
assert refresh["status"] == "refreshed", refresh
generation = load_generation_manifest(data, repository, project)
pointer = load_rust_syntax_pointer(data, repository, project)
assert generation is not None and pointer is not None
assert generation["generation_id"] == pointer["generation_id"]
document = json.loads(Path(pointer["artifact"]["path"]).read_text(encoding="utf-8"))
index = RustSyntaxIndex(document)
t1_definitions = index.definition_candidates("leaf", target_path="src/lib.rs")
t1_references = index.reference_candidates("leaf", target_path="src/lib.rs")
assert t1_definitions and len(t1_references) >= 2

source = (repository / "src/lib.rs").read_text(encoding="utf-8").splitlines()
definition_column = source[0].index("leaf") + 1
call_column = source[1].index("leaf") + 1
provider = RustAnalyzerProvider(analyzer, repository, project, generation)
try:
    provider.start(timeout_seconds=30)
    t2_definitions = provider.query(
        "definition", "leaf", source_path="src/lib.rs",
        source_line=2, source_column=call_column, timeout_ms=30000,
    )
    t2_references = provider.query(
        "references", "leaf", source_path="src/lib.rs",
        source_line=1, source_column=definition_column, timeout_ms=30000,
    )
    assert t2_definitions and t2_references
    assert all(node.provenance.fact_tier == "T2" for node in (*t2_definitions, *t2_references))
finally:
    provider.close()
assert not provider.running
print(json.dumps({
    "status": "passed",
    "public_rust_enabled": False,
    "generation": generation["generation_id"],
    "source_scope": generation["source_scope"]["status"],
    "t1_definitions": len(t1_definitions),
    "t1_references": len(t1_references),
    "t2_definitions": len(t2_definitions),
    "t2_references": len(t2_references),
    "rust_analyzer_cleaned_up": not provider.running,
}))
'''


def executable(environment: Path, name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    directory = "Scripts" if os.name == "nt" else "bin"
    return environment / directory / f"{name}{suffix}"


def require_executable(path: Path, label: str) -> Path:
    selected = Path(os.path.abspath(path.expanduser()))
    if not selected.is_file() or not os.access(selected, os.X_OK):
        raise RuntimeError(f"{label} must be an executable file: {selected}")
    return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--scanner", type=Path, required=True)
    parser.add_argument("--analyzer", type=Path, required=True)
    args = parser.parse_args()
    wheel = args.wheel.expanduser().resolve(strict=True)
    scanner = require_executable(args.scanner, "Rust syntax scanner")
    analyzer = require_executable(args.analyzer, "rust-analyzer")
    parent = "/private/tmp" if platform.system() == "Darwin" else None
    with tempfile.TemporaryDirectory(prefix="atlas-rust-candidate-", dir=parent) as raw:
        root = Path(raw).resolve()
        repository = root / "repo"
        environment_dir = root / "venv"
        home = root / "home"
        data = root / "data"
        (repository / "src").mkdir(parents=True)
        home.mkdir()
        (repository / "Cargo.toml").write_text(textwrap.dedent('''
            [package]
            name = "atlas-installed-fixture"
            version = "0.1.0"
            edition = "2024"
        ''').lstrip(), encoding="utf-8")
        (repository / "src/lib.rs").write_text(
            "pub fn leaf() -> u32 { 41 }\n"
            "pub fn target() -> u32 { leaf() + 1 }\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
        subprocess.run([
            "git", "-C", str(repository), "-c", "user.name=Atlas Acceptance",
            "-c", "user.email=atlas@example.invalid", "commit", "-qm", "fixture",
        ], check=True)
        venv.EnvBuilder(with_pip=True).create(environment_dir)
        python = executable(environment_dir, "python")
        environment = os.environ.copy()
        environment.update({
            "HOME": str(home), "USERPROFILE": str(home),
            "XDG_DATA_HOME": str(root / "xdg"), "PYTHONNOUSERSITE": "1",
            "CARGO_NET_OFFLINE": "true",
        })
        subprocess.run(
            [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
            check=True, env=environment, capture_output=True, text=True,
        )
        completed = subprocess.run(
            [str(python), "-c", PROBE, str(repository), str(data), str(scanner), str(analyzer)],
            check=False, env=environment, capture_output=True, text=True, timeout=240,
        )
        if completed.returncode:
            raise RuntimeError(
                f"installed Rust candidate failed ({completed.returncode})\n"
                f"stdout={completed.stdout[-4000:]}\nstderr={completed.stderr[-8000:]}"
            )
        result = json.loads(completed.stdout)
        result["repository_source_unchanged"] = (
            repository / "src/lib.rs"
        ).read_text(encoding="utf-8").startswith("pub fn leaf")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
