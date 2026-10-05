"""Receipt-bound Rust service construction; no downloads or process startup."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .config import AtlasConfig
from .providers.rust_analyzer import RustAnalyzerProvider
from .providers.rust_syntax import RustSyntaxIndex, load_rust_syntax_pointer
from .refresh_planner import load_generation_manifest
from .rust_installation import runtime_from_receipt
from .rust_runtime import RustRuntimeError
from .service import AtlasService


def _load_index(data_dir: Path, repository: Path, project: str):
    generation = load_generation_manifest(data_dir, repository, project)
    pointer = load_rust_syntax_pointer(data_dir, repository, project)
    if generation is None or pointer is None or generation.get("language") != "rust":
        raise RustRuntimeError("Rust service requires a published Rust generation")
    if (generation["generation_id"] != pointer["generation_id"]
            or generation.get("provider_identity", {}).get("artifact") != pointer["artifact"]):
        raise RustRuntimeError("Rust generation and syntax artifact identity mismatch")
    artifact = pointer["artifact"]
    payload = Path(artifact["path"]).read_bytes()
    # Recheck the bytes actually parsed, not just a previous filesystem read.
    if len(payload) != artifact["size"] or hashlib.sha256(payload).hexdigest() != artifact["sha256"]:
        raise RustRuntimeError("Rust syntax artifact changed while loading")
    syntax = RustSyntaxIndex(json.loads(payload))
    if any(syntax.document[key] != expected for key, expected in (
        ("project", project), ("repository", str(repository.resolve())),
        ("generation_id", generation["generation_id"]),
        ("source_fingerprint", generation["source_fingerprint"]),
    )):
        raise RustRuntimeError("Rust syntax generation identity mismatch")
    return generation, syntax


def rust_index_health(data_dir: Path, repository: Path, project: str) -> dict:
    """Read-only T0/T1 integrity check, not proof of semantic readiness."""
    try:
        generation, _syntax = _load_index(data_dir, repository, project)
        return {"status": "healthy", "ok": True, "reason": "rust_generation_verified",
                "generation_id": generation["generation_id"], "fact_tier": "T1"}
    except (OSError, ValueError, RuntimeError) as exc:
        return {"status": "rebuild_required", "ok": False,
                "reason": "rust_generation_unavailable", "detail": str(exc)}


def load_rust_service(config: AtlasConfig, *, session_continuations: bool = False) -> AtlasService:
    """Bind T1 and T2 to one exact generation without legacy providers.

    This factory is internal while the public language gate is closed. It is
    not a bypass for normal installation or product qualification.
    """
    if config.language != "rust" or not config.project or config.rust_runtime_receipt is None:
        raise RustRuntimeError("Rust service requires an exact project and installation receipt")
    runtime = runtime_from_receipt(config.rust_runtime_receipt, repository=config.repository)
    generation, syntax = _load_index(config.data_dir, config.repository, config.project)
    analyzer = RustAnalyzerProvider(
        runtime.analyzer.path, config.repository, config.project, generation, runtime=runtime,
    )
    return AtlasService(
        repository=config.repository, indexed_language="rust", rust_provider=analyzer,
        rust_syntax_index=syntax, session_continuations=session_continuations,
    )
