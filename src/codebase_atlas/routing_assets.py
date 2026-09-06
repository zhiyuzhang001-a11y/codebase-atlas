"""Versioned project routing assets and read-only ownership classification.

Markers alone never authorize replacement. Every owned byte sequence must be
known to this release; edited or hand-installed assets remain foreign.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import stat


BEGIN = "<!-- codebase-atlas managed routing v1 begin -->"
END = "<!-- codebase-atlas managed routing v1 end -->"
RULE = f"""
{BEGIN}
For cross-file behavior, callers, impact or test selection, use the project-local
codebase-atlas skill when available. Known single-file questions can use source
directly. Atlas evidence never authorizes enabling or modifying a project.
{END}
""".encode()
SKILL = b"""---
name: codebase-atlas
description: Use this project's Atlas index for cross-file behavior, callers, change impact and evidence-backed test selection. Known single-file questions can be answered directly from source.
---

<!-- codebase-atlas managed skill v1 -->
Call project_status before relying on Atlas evidence. Confirm its exact repository
is the requested project and inspect lifecycle, freshness and generation.
If Atlas is unavailable, stopped or reports a different repository, use direct
source inspection and disclose the limitation. Never enable or repair Atlas as
part of a read-only question.

Discover an exact symbol and path from source, then use analyze_change for
cross-file questions. Read returned source regions before editing. Preserve
partial, unresolved, stale, truncation, continuation and auto_update caveats;
incomplete evidence is not proof of no callers or no impact. Resolve ambiguity
from source or ask when the target cannot be determined.

Use tests supported by the actual affected behavior. Do not create navigation
caches, project metadata or source changes for a read-only task. Atlas runtime
files outside the repository follow its managed runtime policy.
"""

# Append older published payloads here when introducing a new asset revision.
# A locally supplied hash or marker is not a published ownership authority.
KNOWN_RULES = frozenset({hashlib.sha256(RULE).hexdigest()})
KNOWN_SKILLS = frozenset({hashlib.sha256(SKILL).hexdigest()})


@dataclass(frozen=True)
class AssetPlan:
    path: Path
    status: str
    before: bytes | None
    after: bytes | None
    mode: int | None


def _read(repository: Path, relative: str) -> tuple[Path, bytes | None, int | None]:
    path = repository / relative
    for parent in path.parents:
        if parent == repository:
            break
        if parent.is_symlink():
            raise RuntimeError("routing asset ancestor must not be a symlink")
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return path, None, None
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("routing asset must be a regular file")
    if metadata.st_size > 1024 * 1024:
        raise RuntimeError("routing asset exceeds the 1 MiB inspection budget")
    return path, path.read_bytes(), stat.S_IMODE(metadata.st_mode)


def plan_routing(repository: Path, *, remove: bool = False) -> tuple[AssetPlan, ...]:
    """Plan without creating directories or changing any project content."""
    repository = repository.resolve(strict=True)
    path, before, mode = _read(repository, "AGENTS.md")
    body = before or b""
    begin, end = BEGIN.encode(), END.encode()
    if begin not in body and end not in body:
        rule = AssetPlan(path, "absent", before,
                         before if remove else body + RULE, mode)
    elif body.count(begin) != 1 or body.count(end) != 1:
        rule = AssetPlan(path, "conflict", before, before, mode)
    else:
        start, finish = body.index(begin), body.index(end) + len(end)
        if start > 0 and body[start - 1:start] == b"\n":
            start -= 1
        if body[finish:finish + 1] == b"\n":
            finish += 1
        block = body[start:finish]
        owned = hashlib.sha256(block).hexdigest() in KNOWN_RULES
        status = "matching" if block == RULE else "owned-old" if owned else "conflict"
        replacement = b"" if remove else RULE
        after = body[:start] + replacement + body[finish:] if owned else before
        # AGENTS.md may predate Atlas even when empty. Remove only our block,
        # never infer ownership of the surrounding file from its contents.
        rule = AssetPlan(path, status, before, after, mode)
    path, before, mode = _read(repository, ".agents/skills/codebase-atlas/SKILL.md")
    owned = before is not None and hashlib.sha256(before).hexdigest() in KNOWN_SKILLS
    status = "absent" if before is None else "matching" if before == SKILL else "owned-old" if owned else "conflict"
    after = before if status == "conflict" else None if remove else SKILL
    return rule, AssetPlan(path, status, before, after, mode)
