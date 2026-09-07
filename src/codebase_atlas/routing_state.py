"""Durable ownership state for repository routing assets."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any


FILENAME = "routing-state-v1.json"


def routing_state_path(data_dir: Path) -> Path:
    return data_dir.resolve() / FILENAME


def load_routing_state(data_dir: Path, repository: Path) -> dict[str, Any] | None:
    path = routing_state_path(data_dir)
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError("routing state must be a regular non-symlink file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("routing state is unreadable") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "repository", "created_rule_file"}
        or value.get("schema_version") != 1
        or value.get("repository") != str(repository.resolve())
        or type(value.get("created_rule_file")) is not bool
    ):
        raise RuntimeError("routing state schema or identity is invalid")
    return value


def publish_routing_state(
    data_dir: Path, repository: Path, *, created_rule_file: bool
) -> Path:
    path = routing_state_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise RuntimeError("routing state directory must not be a symlink")
    value = {
        "schema_version": 1,
        "repository": str(repository.resolve()),
        "created_rule_file": created_rule_file,
    }
    descriptor, temporary = tempfile.mkstemp(
        prefix=".routing-state-", suffix=".json", dir=path.parent
    )
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path
