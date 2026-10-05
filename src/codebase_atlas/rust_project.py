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


def load_rust_service(config: AtlasConfig, *, session_continuations: bool = False) -> AtlasService:
    """Bind T1 and T2 to one exact generation without legacy providers.

    This factory is internal while the public language gate is closed. It is
    not a bypass for normal installation or product qualification.
    """
    if config.language != "rust" or not config.project or config.rust_runtime_receipt is None:
        raise RustRuntimeError("Rust service requires an exact project and installation receipt")
    runtime = runtime_from_receipt(config.rust_runtime_receipt, repository=config.repository)
    generation = load_generation_manifest(config.data_dir, config.repository, config.project)
    pointer = load_rust_syntax_pointer(config.data_dir, config.repository, config.project)
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
    if syntax.document["project"] != config.project:
        raise RustRuntimeError("Rust syntax project identity mismatch")
    analyzer = RustAnalyzerProvider(
        runtime.analyzer.path, config.repository, config.project, generation, runtime=runtime,
    )
    return AtlasService(
        repository=config.repository, indexed_language="rust", rust_provider=analyzer,
        rust_syntax_index=syntax, session_continuations=session_continuations,
    )
