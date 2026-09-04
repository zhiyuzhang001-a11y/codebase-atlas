"""Small, dependency-free matcher for the Provider's root ``.cbmignore``."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
import os
import stat


@dataclass(frozen=True)
class _Pattern:
    value: str
    negated: bool
    directory_only: bool
    rooted: bool


def _character_class(pattern: str, offset: int, value: str) -> tuple[bool, int]:
    end = pattern.find("]", offset + 1)
    if end < 0:
        return value == "[", offset + 1
    body = pattern[offset + 1:end]
    negated = body.startswith(("!", "^"))
    if negated:
        body = body[1:]
    matched = False
    index = 0
    while index < len(body):
        if index + 2 < len(body) and body[index + 1] == "-":
            matched |= body[index] <= value <= body[index + 2]
            index += 3
        else:
            matched |= body[index] == value
            index += 1
    return (not matched if negated else matched), end + 1


def _glob_matches(pattern: str, value: str) -> bool:
    budget = 20_000

    @lru_cache(maxsize=None)
    def match(pattern_offset: int, value_offset: int) -> bool:
        nonlocal budget
        budget -= 1
        if budget <= 0:
            return False
        if pattern_offset == len(pattern):
            return value_offset == len(value)
        if pattern.startswith("**", pattern_offset):
            after = pattern_offset + 2
            if after < len(pattern) and pattern[after] == "/":
                after += 1
                if match(after, value_offset):
                    return True
                return any(
                    value[index] == "/" and match(after, index + 1)
                    for index in range(value_offset, len(value))
                )
            return any(match(after, index) for index in range(value_offset, len(value) + 1))
        token = pattern[pattern_offset]
        if token == "*":
            return any(
                match(pattern_offset + 1, index)
                for index in range(value_offset, len(value) + 1)
                if "/" not in value[value_offset:index]
            )
        if value_offset == len(value):
            return False
        if token == "?":
            return value[value_offset] != "/" and match(pattern_offset + 1, value_offset + 1)
        if token == "[":
            matched, after = _character_class(pattern, pattern_offset, value[value_offset])
            return matched and match(after, value_offset + 1)
        return token == value[value_offset] and match(pattern_offset + 1, value_offset + 1)

    return match(0, 0)


class CbmIgnore:
    def __init__(self, patterns: tuple[_Pattern, ...] = ()) -> None:
        self.patterns = patterns

    @classmethod
    def load(cls, repository: Path) -> "CbmIgnore":
        path = repository / ".cbmignore"
        try:
            metadata = os.lstat(path)
            if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
                return cls()
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return cls()
        patterns: list[_Pattern] = []
        for raw in text.splitlines():
            line = raw.rstrip(" \t\r")
            if not line or line.startswith("#"):
                continue
            negated = line.startswith("!")
            if negated:
                line = line[1:]
            directory_only = line.endswith("/")
            if directory_only:
                line = line[:-1]
            rooted = line.startswith("/")
            if rooted:
                line = line[1:]
            rooted = rooted or "/" in line
            if line:
                patterns.append(_Pattern(line, negated, directory_only, rooted))
        return cls(tuple(patterns))

    def match_result(self, relative: str, *, is_dir: bool) -> int:
        path = PurePosixPath(relative).as_posix()
        matched = 0
        for pattern in self.patterns:
            if pattern.directory_only and not is_dir:
                continue
            candidates = (path,) if pattern.rooted else tuple(
                "/".join(PurePosixPath(path).parts[index:])
                for index in range(len(PurePosixPath(path).parts))
            )
            if any(_glob_matches(pattern.value, candidate) for candidate in candidates):
                matched = -1 if pattern.negated else 1
        return matched

    def ignores(self, relative: str, *, is_dir: bool = False) -> bool:
        path = PurePosixPath(relative)
        parts = path.parts if is_dir else path.parts[:-1]
        for count in range(1, len(parts) + 1):
            if self.match_result("/".join(parts[:count]), is_dir=True) > 0:
                return True
        return self.match_result(path.as_posix(), is_dir=is_dir) > 0
