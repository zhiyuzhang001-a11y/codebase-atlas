"""Versioned project routing assets and read-only ownership classification.

Markers alone never authorize replacement. Every owned byte sequence must be
known to this release; edited or hand-installed assets remain foreign.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
import binascii
import hashlib
import os
from pathlib import Path
import re
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


def routing_bundle() -> dict[str, object]:
    return {
        "schema_version": 1,
        "rule": base64.b64encode(RULE).decode("ascii"),
        "skill": base64.b64encode(SKILL).decode("ascii"),
        "known_rules": sorted(KNOWN_RULES),
        "known_skills": sorted(KNOWN_SKILLS),
    }


def decode_routing_bundle(value: object) -> tuple[bytes, bytes, frozenset[str], frozenset[str]]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "rule", "skill", "known_rules", "known_skills"
    } or value.get("schema_version") != 1:
        raise RuntimeError("routing asset bundle schema is invalid")
    try:
        rule = base64.b64decode(value["rule"], validate=True)
        skill = base64.b64decode(value["skill"], validate=True)
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise RuntimeError("routing asset bundle encoding is invalid") from exc
    known_rules, known_skills = value["known_rules"], value["known_skills"]
    if (len(rule) > 1024 * 1024 or len(skill) > 1024 * 1024
            or not isinstance(known_rules, list) or not isinstance(known_skills, list)
            or not all(isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item)
                       for item in [*known_rules, *known_skills])
            or hashlib.sha256(rule).hexdigest() not in known_rules
            or hashlib.sha256(skill).hexdigest() not in known_skills):
        raise RuntimeError("routing asset bundle content is invalid")
    return rule, skill, frozenset(known_rules), frozenset(known_skills)


@dataclass(frozen=True)
class AssetPlan:
    path: Path
    status: str
    before: bytes | None
    after: bytes | None
    mode: int | None


def portable_mode(mode: int) -> int:
    """Return the permission representation the current OS can round-trip."""
    value = stat.S_IMODE(mode)
    if os.name == "nt":
        return 0o666 if value & stat.S_IWRITE else 0o444
    return value


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
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (metadata.st_dev, metadata.st_ino, metadata.st_mode) != (
            opened.st_dev, opened.st_ino, opened.st_mode
        ):
            raise RuntimeError("routing asset changed during inspection")
        content = stream.read(1024 * 1024 + 1)
        final = os.fstat(stream.fileno())
    current = path.lstat()
    # st_ctime has platform-specific semantics and Windows may refresh the
    # path/handle value without a content mutation. Identity, mode, size,
    # mtime, and the bytes themselves provide the portable race check.
    signature = lambda value: (value.st_dev, value.st_ino, value.st_mode,
                               value.st_size, value.st_mtime_ns)
    if len(content) > 1024 * 1024 or signature(metadata) != signature(final) or signature(final) != signature(current):
        raise RuntimeError("routing asset changed or exceeded budget during inspection")
    return path, content, portable_mode(metadata.st_mode)


def plan_routing(
    repository: Path, *, remove: bool = False,
    bundle: tuple[bytes, bytes, frozenset[str], frozenset[str]] | None = None,
    remove_created_rule_file: bool = False,
) -> tuple[AssetPlan, ...]:
    """Plan without creating directories or changing any project content."""
    repository = repository.resolve(strict=True)
    desired_rule, desired_skill, known_rules, known_skills = bundle or (
        RULE, SKILL, KNOWN_RULES, KNOWN_SKILLS
    )
    path, before, mode = _read(repository, "AGENTS.md")
    body = before or b""
    begin, end = BEGIN.encode(), END.encode()
    if begin not in body and end not in body:
        rule = AssetPlan(path, "absent", before,
                         before if remove else body + desired_rule, mode)
    elif body.count(begin) != 1 or body.count(end) != 1:
        rule = AssetPlan(path, "conflict", before, before, mode)
    else:
        start, finish = body.index(begin), body.index(end) + len(end)
        if start > 0 and body[start - 1:start] == b"\n":
            start -= 1
        if body[finish:finish + 1] == b"\n":
            finish += 1
        block = body[start:finish]
        owned = hashlib.sha256(block).hexdigest() in known_rules
        status = "matching" if block == desired_rule else "owned-old" if owned else "conflict"
        replacement = b"" if remove else desired_rule
        after = body[:start] + replacement + body[finish:] if owned else before
        if remove and owned and remove_created_rule_file and after == b"":
            after = None
        # AGENTS.md may predate Atlas even when empty. Remove only our block,
        # never infer ownership of the surrounding file from its contents.
        rule = AssetPlan(path, status, before, after, mode)
    path, before, mode = _read(repository, ".agents/skills/codebase-atlas/SKILL.md")
    owned = before is not None and hashlib.sha256(before).hexdigest() in known_skills
    status = "absent" if before is None else "matching" if before == desired_skill else "owned-old" if owned else "conflict"
    after = before if status == "conflict" else None if remove else desired_skill
    return rule, AssetPlan(path, status, before, after, mode)
