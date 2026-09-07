"""In-process routing rollback, to be enclosed by the project lifecycle lease.

This component does not claim crash recovery or index rollback. The lifecycle
coordinator must keep its wider transaction open until acceptance succeeds.
"""

from __future__ import annotations

import os
import base64
import binascii
import hashlib
from pathlib import Path
import tempfile

from .routing_assets import AssetPlan, BEGIN, END, KNOWN_RULES, KNOWN_SKILLS, _read, plan_routing


class RoutingTransaction:
    def __init__(self, repository: Path, *, remove: bool = False):
        self.repository = repository.resolve(strict=True)
        self.plans = plan_routing(self.repository, remove=remove)
        self.conflicts = tuple(str(p.path) for p in self.plans if p.status == "conflict")
        if self.conflicts and not remove:
            raise RuntimeError("foreign or modified routing assets: " + ", ".join(self.conflicts))
        self._applied: list[AssetPlan] = []
        self._directories: list[Path] = []
        self._started = False
        self._target_modes: dict[Path, int] = {}

    def recovery_record(self) -> list[dict]:
        """Serialize changed assets into the private project removal receipt."""
        return [{
            "path": str(plan.path.relative_to(self.repository)),
            "original": base64.b64encode(plan.before).decode("ascii") if plan.before is not None else None,
            "removed": base64.b64encode(plan.after).decode("ascii") if plan.after is not None else None,
            "mode": plan.mode,
        } for plan in self.plans if plan.before != plan.after]

    @classmethod
    def for_recovery(cls, repository: Path, records: object):
        if not isinstance(records, list) or len(records) > 2:
            raise RuntimeError("invalid routing recovery records")
        instance = cls.__new__(cls)
        instance.repository = repository.resolve(strict=True)
        instance.conflicts = ()
        instance._applied = []
        instance._directories = []
        instance._started = False
        instance._target_modes = {}
        plans = []
        seen = set()
        for record in records:
            if not isinstance(record, dict) or set(record) != {"path", "original", "removed", "mode"}:
                raise RuntimeError("invalid routing recovery record")
            relative = record["path"]
            if not isinstance(relative, str) or relative not in {
                "AGENTS.md", ".agents/skills/codebase-atlas/SKILL.md"
            } or relative in seen:
                raise RuntimeError("invalid routing recovery target")
            seen.add(relative)
            mode = record["mode"]
            if type(mode) is not int or not 0 <= mode <= 0o777:
                raise RuntimeError("invalid routing recovery permissions")
            payloads = []
            for field in ("removed", "original"):
                value = record[field]
                if value is None:
                    payloads.append(None)
                    continue
                if not isinstance(value, str) or len(value) > 1_398_104:
                    raise RuntimeError("invalid routing recovery payload")
                try:
                    payload = base64.b64decode(value, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise RuntimeError("invalid routing recovery encoding") from exc
                if len(payload) > 1024 * 1024:
                    raise RuntimeError("routing recovery payload exceeds budget")
                payloads.append(payload)
            before, after = payloads
            if after is None:
                raise RuntimeError("removal recovery requires original asset bytes")
            if relative.endswith("SKILL.md"):
                if before is not None or hashlib.sha256(after).hexdigest() not in KNOWN_SKILLS:
                    raise RuntimeError("routing recovery skill is not a known owned asset")
            else:
                begin, end = BEGIN.encode(), END.encode()
                if after.count(begin) != 1 or after.count(end) != 1:
                    raise RuntimeError("routing recovery rule markers are invalid")
                start, finish = after.index(begin), after.index(end) + len(end)
                if start > 0 and after[start - 1:start] == b"\n":
                    start -= 1
                if after[finish:finish + 1] == b"\n":
                    finish += 1
                if (hashlib.sha256(after[start:finish]).hexdigest() not in KNOWN_RULES
                        or after[:start] + after[finish:] != before):
                    raise RuntimeError("routing recovery rule is not a known owned change")
            path = instance.repository / relative
            plans.append(AssetPlan(path, "recovery", before, after,
                                   mode if before is not None else None))
            instance._target_modes[path] = mode
        instance.plans = tuple(plans)
        return instance

    def _after_mode(self, plan: AssetPlan) -> int | None:
        if plan.after is None:
            return None
        return self._target_modes.get(plan.path, plan.mode if plan.mode is not None else 0o644)

    def _check(self, plan: AssetPlan, expected: bytes | None, mode: int | None):
        _, actual, actual_mode = _read(self.repository, str(plan.path.relative_to(self.repository)))
        if actual != expected or actual_mode != mode:
            raise RuntimeError(f"routing asset changed since planning: {plan.path}")

    def _publish(self, plan: AssetPlan, expected: bytes | None,
                 expected_mode: int | None, content: bytes | None, mode: int | None):
        self._check(plan, expected, expected_mode)
        if content is None:
            if expected is not None:
                plan.path.unlink()
            return
        missing = []
        parent = plan.path.parent
        while parent != self.repository and not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for folder in reversed(missing):
            folder.mkdir()
            self._directories.append(folder)
        self._check(plan, expected, expected_mode)
        descriptor, temporary = tempfile.mkstemp(prefix=".atlas-routing-", dir=plan.path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, mode if mode is not None else 0o644)
            self._check(plan, expected, expected_mode)
            os.replace(temporary, plan.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def apply(self):
        if self._started:
            raise RuntimeError("routing transaction has already started")
        self._started = True
        try:
            # Validate every target before publishing the first one.
            for plan in self.plans:
                self._check(plan, plan.before, plan.mode)
            for plan in self.plans:
                if plan.before == plan.after:
                    continue
                self._applied.append(plan)
                self._publish(plan, plan.before, plan.mode, plan.after, self._after_mode(plan))
        except BaseException as exc:
            errors = self.rollback()
            if errors:
                raise RuntimeError("routing rollback incomplete: " + "; ".join(errors)) from exc
            raise

    def rollback(self) -> list[str]:
        errors = []
        for plan in reversed(self._applied):
            after_mode = self._after_mode(plan)
            try:
                # Publication may raise either before or after the rename.
                # A target still at its original state needs no restoration.
                _, current, current_mode = _read(
                    self.repository, str(plan.path.relative_to(self.repository))
                )
                if current == plan.before and current_mode == plan.mode:
                    continue
                self._publish(plan, plan.after, after_mode, plan.before, plan.mode)
            except (OSError, RuntimeError) as exc:
                errors.append(str(exc))
        self._applied.clear()
        for folder in reversed(self._directories):
            try:
                folder.rmdir()
            except OSError:
                # Nonempty directories may contain concurrent user additions.
                # Never recursively delete them to make rollback look complete.
                if folder.exists():
                    errors.append(f"routing directory preserved: {folder}")
        self._directories.clear()
        return errors
