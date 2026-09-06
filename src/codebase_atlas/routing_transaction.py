"""In-process routing rollback, to be enclosed by the project lifecycle lease.

This component does not claim crash recovery or index rollback. The lifecycle
coordinator must keep its wider transaction open until acceptance succeeds.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

from .routing_assets import AssetPlan, _read, plan_routing


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
                self._publish(plan, plan.before, plan.mode, plan.after, plan.mode)
        except BaseException as exc:
            errors = self.rollback()
            if errors:
                raise RuntimeError("routing rollback incomplete: " + "; ".join(errors)) from exc
            raise

    def rollback(self) -> list[str]:
        errors = []
        for plan in reversed(self._applied):
            after_mode = (plan.mode if plan.mode is not None else 0o644) if plan.after is not None else None
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
