from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from codebase_atlas.config import AtlasConfig
from codebase_atlas.index_state import state_path
from codebase_atlas.providers.rust_syntax import (
    ENGINE,
    PROVIDER_NAME,
    PROVIDER_VERSION,
    load_rust_syntax_pointer,
    rust_syntax_pointer_path,
)
from codebase_atlas.refresh_planner import load_generation_manifest, manifest_path
from codebase_atlas.rust_refresh import RustRefreshCoordinator
from codebase_atlas.rust_refresh_recovery import (
    RustRefreshRecoveryJournal,
    recover_rust_refresh_transaction,
    rust_refresh_journal_path,
)


def git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *args], check=True, capture_output=True
    )


class RustRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repo"
        (self.repository / "src").mkdir(parents=True)
        (self.repository / "Cargo.toml").write_text(
            '[package]\nname = "refresh-fixture"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        (self.repository / "src/lib.rs").write_text(
            "pub fn run() {}\n", encoding="utf-8"
        )
        git(self.repository, "init", "-q")
        git(self.repository, "config", "user.email", "atlas@example.invalid")
        git(self.repository, "config", "user.name", "Atlas Test")
        git(self.repository, "add", ".")
        git(self.repository, "commit", "-qm", "initial")
        self.data = self.root / "data"
        self.scanner = self.root / "atlas-rust-syntax"
        self.scanner.write_bytes(b"fixed scanner")
        self.scanner.chmod(0o755)
        self.config = AtlasConfig(
            self.repository,
            "rust",
            self.root / "node",
            self.root / "cbm",
            self.root / "serena",
            self.data,
            project="rust-project",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def runner(self, argv, **_kwargs):
        scope = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        output = {
            "schema_version": 2,
            "provider": PROVIDER_NAME,
            "provider_version": PROVIDER_VERSION,
            "engine": ENGINE,
            "files": [
                {"path": path, "has_parse_error": False}
                for path in scope["source_paths"]
            ],
            "facts": [{
                "path": "src/lib.rs",
                "kind": "function_item",
                "name": "run",
                "owner": None,
                "start": {"line": 1, "column": 8},
                "end": {"line": 1, "column": 11},
            }],
            "candidate_paths": scope["source_paths"],
            "candidate_names": [],
            "candidate_owners": [],
            "identifier_candidate_count": 0,
            "identifier_candidates_hex": "",
            "error_files": [],
        }
        Path(argv[3]).write_text(json.dumps(output), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    def published_bytes(self) -> dict[str, bytes]:
        return {
            "manifest": manifest_path(self.data).read_bytes(),
            "pointer": rust_syntax_pointer_path(self.data).read_bytes(),
            "state": state_path(self.data).read_bytes(),
        }

    def replace_bytes(self, path: Path, payload: bytes) -> None:
        temporary = path.with_name(f".{path.name}.test-replacement")
        temporary.write_bytes(payload)
        temporary.replace(path)

    def test_refresh_publishes_one_consistent_t0_t1_generation(self) -> None:
        result = RustRefreshCoordinator(
            self.config, self.scanner, runner=self.runner
        ).refresh()
        self.assertEqual(result["status"], "refreshed")
        manifest = load_generation_manifest(
            self.data, self.repository, self.config.project
        )
        pointer = load_rust_syntax_pointer(
            self.data, self.repository, self.config.project
        )
        self.assertIsNotNone(manifest)
        self.assertIsNotNone(pointer)
        self.assertEqual(manifest["generation_id"], pointer["generation_id"])
        self.assertEqual(result["generation_after"], manifest["generation_id"])
        self.assertEqual(manifest["provider_identity"]["artifact"], pointer["artifact"])
        self.assertEqual(manifest["provider_identity"]["fact_tier"], "T1")
        self.assertFalse(rust_refresh_journal_path(self.data).exists())

    def test_failure_after_pointer_publication_restores_previous_generation(self) -> None:
        first = RustRefreshCoordinator(
            self.config, self.scanner, runner=self.runner
        ).refresh()
        self.assertEqual(first["status"], "refreshed")
        before = self.published_bytes()
        before_shards = sorted((self.data / "rust-syntax").iterdir())

        for injected_phase in ("pointer_published", "state_replaced"):
            with self.subTest(injected_phase=injected_phase):
                def observe(phase: str) -> None:
                    if phase == injected_phase:
                        raise RuntimeError("injected publication failure")

                failed = RustRefreshCoordinator(
                    self.config,
                    self.scanner,
                    runner=self.runner,
                    phase_observer=observe,
                ).refresh()
                self.assertEqual(failed["status"], "failed")
                self.assertTrue(failed["previous_generation_preserved"])
                self.assertEqual(self.published_bytes(), before)
                self.assertEqual(
                    sorted((self.data / "rust-syntax").iterdir()), before_shards
                )
                self.assertFalse(rust_refresh_journal_path(self.data).exists())

    def test_recovery_rolls_back_pre_acceptance_and_accepts_state_publication(self) -> None:
        manifest = manifest_path(self.data)
        pointer = rust_syntax_pointer_path(self.data)
        state = state_path(self.data)
        self.data.mkdir(parents=True)
        for path, payload in (
            (manifest, b"old-manifest"),
            (pointer, b"old-pointer"),
            (state, b"old-state"),
        ):
            path.write_bytes(payload)
        shard = (
            self.config.data_dir / "rust-syntax"
            / "new-generation-deadbeefdeadbeef.json"
        )
        shard.parent.mkdir()
        journal = RustRefreshRecoveryJournal.begin(
            self.config, "old-generation", "new-generation", shard
        )
        shard.write_bytes(b"new-shard")
        self.replace_bytes(manifest, b"new-manifest")
        self.replace_bytes(pointer, b"new-pointer")
        self.replace_bytes(state, b"new-state")
        for phase in ("shard_published", "manifest_published", "pointer_published"):
            journal.advance(phase)
        recovered = recover_rust_refresh_transaction(self.config)
        self.assertEqual(recovered["action"], "restored_previous_generation")
        self.assertEqual(manifest.read_bytes(), b"old-manifest")
        self.assertEqual(pointer.read_bytes(), b"old-pointer")
        self.assertEqual(state.read_bytes(), b"old-state")
        self.assertFalse(shard.exists())

        journal = RustRefreshRecoveryJournal.begin(
            self.config, "old-generation", "new-generation", shard
        )
        shard.write_bytes(b"new-shard")
        self.replace_bytes(manifest, b"new-manifest")
        self.replace_bytes(pointer, b"new-pointer")
        self.replace_bytes(state, b"new-state")
        for phase in (
            "shard_published", "manifest_published", "pointer_published",
            "state_published",
        ):
            journal.advance(phase)
        recovered = recover_rust_refresh_transaction(self.config)
        self.assertEqual(recovered["action"], "accepted_published_generation")
        self.assertEqual(manifest.read_bytes(), b"new-manifest")
        self.assertEqual(pointer.read_bytes(), b"new-pointer")
        self.assertEqual(state.read_bytes(), b"new-state")
        self.assertTrue(shard.exists())


if __name__ == "__main__":
    unittest.main()
