"""In-process enable snapshots; callers must own operation and refresh leases.

Provider backups rely on the Provider's atomic database-generation publication,
as does RefreshCoordinator. This is not a crash-recovery journal.
"""

from pathlib import Path
import os
import stat

from .config import AtlasConfig
from .refresh_coordinator import ProviderDatabaseBackup, _snapshot_file, _restore_file
from .refresh_planner import manifest_path
from .python_registration_store import registration_index_path
from .codex_integration import PROJECT_SCOPE_BEGIN, PROJECT_SCOPE_END


def snapshot(path: Path):
    payload = _snapshot_file(path)
    mode = stat.S_IMODE(path.lstat().st_mode) if payload is not None else None
    return payload, mode


class EnableTransaction:
    def __init__(self, config: AtlasConfig, config_path: Path):
        if not config.project or Path(config.project).name != config.project:
            raise RuntimeError("enable transaction requires an exact path-safe project")
        self.paths = (
            config_path, config.repository / ".codex/config.toml",
            config.data_dir / "lifecycle-state.json",
            config.data_dir / "index-state.json",
            manifest_path(config.data_dir), registration_index_path(config.data_dir),
        )
        self.before = {path: snapshot(path) for path in self.paths}
        self.expected = dict(self.before)
        self.allowed = {
            config_path: {self.before[config_path][0], config.render().encode("utf-8")},
            self.paths[1]: {self.before[self.paths[1]][0]},
        }
        self.database = config.cache_dir / f"{config.project}.db"
        self.backup = ProviderDatabaseBackup.create(self.database)
        self.database_identity = self._database_identity()
        self.database_touched = False

    def allow_codex_plan(self, plan: dict):
        block = plan.get("managed_block")
        if not isinstance(block, str):
            return
        target = self.paths[1]
        original = (self.before[target][0] or b"").decode("utf-8")
        if plan.get("existing") == "managed_different":
            start = original.index(PROJECT_SCOPE_BEGIN)
            finish = original.index(PROJECT_SCOPE_END, start) + len(PROJECT_SCOPE_END)
            if original[finish:finish + 1] == "\n":
                finish += 1
            candidate = original[:start] + block + original[finish:]
        elif plan.get("existing") == "matching":
            candidate = original
        else:
            separator = "" if not original or original.endswith("\n\n") else "\n" if original.endswith("\n") else "\n\n"
            candidate = original + separator + block
        self.allowed[target].add(candidate.encode("utf-8"))

    def _database_identity(self):
        try:
            metadata = self.database.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("Provider database is not a regular file")
        return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns

    def run(self, action, *, indexes: bool = False):
        """Record published state even when a lifecycle component raises."""
        try:
            return action()
        finally:
            conflicts = []
            for path in self.paths:
                current = snapshot(path)
                if path in self.allowed and current[0] not in self.allowed[path]:
                    conflicts.append(str(path))
                    continue
                self.expected[path] = current
            if indexes:
                self.database_touched = True
                self.database_identity = self._database_identity()
            if conflicts:
                raise RuntimeError("unrecognized configuration write preserved: " + ", ".join(conflicts))

    def rollback(self) -> list[str]:
        errors = []
        if self.database_touched:
            try:
                if self._database_identity() != self.database_identity:
                    raise RuntimeError("Provider generation changed after indexing")
                self.backup.rollback()
            except (OSError, RuntimeError) as exc:
                errors.append(f"Provider: {exc}")
        for path in reversed(self.paths):
            try:
                if snapshot(path) != self.expected[path]:
                    raise RuntimeError("file changed after lifecycle publication")
                payload, mode = self.before[path]
                if self.before[path] != self.expected[path]:
                    _restore_file(path, payload)
                    if mode is not None:
                        os.chmod(path, mode)
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"{path.name}: {exc}")
        if not errors:
            self.backup.commit()
        return errors

    def commit(self) -> bool:
        return self.backup.commit()
