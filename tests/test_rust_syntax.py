from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from codebase_atlas.providers.rust_syntax import (
    CANDIDATE_RECORD,
    ENGINE,
    PROVIDER_NAME,
    PROVIDER_VERSION,
    MAX_SOURCE_FILE_BYTES,
    NO_OWNER,
    RustSyntaxError,
    RustSyntaxIndex,
    RustSyntaxProvider,
    validate_scanner_output,
)
from codebase_atlas.refresh_planner import build_generation_manifest


def git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *args], check=True, capture_output=True
    )


class RustSyntaxProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repo"
        (self.repository / "src").mkdir(parents=True)
        (self.repository / "Cargo.toml").write_text(
            '[package]\nname = "syntax-fixture"\nversion = "0.1.0"\n',
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
        self.scanner = self.root / "atlas-rust-syntax"
        self.scanner.write_bytes(b"fixed scanner")
        self.scanner.chmod(0o755)
        self.data = self.root / "data"
        self.generation = build_generation_manifest(
            self.repository, "project", "rust",
            generation_id="generation-1",
            provider_identity={"status": "not_applicable", "tier": "T0"},
            sidecar_identity={"status": "not_applicable", "tier": "T0"},
            created_at="generation:generation-1",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def output(self, paths: list[str]) -> dict:
        return {
            "schema_version": 2,
            "provider": PROVIDER_NAME,
            "provider_version": PROVIDER_VERSION,
            "engine": ENGINE,
            "files": [
                {"path": path, "has_parse_error": False} for path in paths
            ],
            "facts": [{
                "path": "src/lib.rs", "kind": "function_item", "name": "run",
                "owner": None,
                "start": {"line": 1, "column": 8},
                "end": {"line": 1, "column": 11},
            }],
            "candidate_paths": paths,
            "candidate_names": ["run"],
            "candidate_owners": [],
            "identifier_candidate_count": 1 if paths else 0,
            "identifier_candidates_hex": (
                CANDIDATE_RECORD.pack(0, 0, NO_OWNER, 1, 8, 1, 11).hex()
                if paths else ""
            ),
            "error_files": [],
        }

    def test_stages_content_addressed_shard_and_rolls_back_publication(self) -> None:
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            scope = json.loads(Path(argv[2]).read_text())
            Path(argv[3]).write_text(
                json.dumps(self.output(scope["source_paths"])), encoding="utf-8"
            )
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        before = sorted(path.relative_to(self.repository) for path in self.repository.rglob("*"))
        provider = RustSyntaxProvider(
            self.scanner, self.repository, self.data, "project", runner=runner
        )
        with provider.stage(self.generation) as staged:
            self.assertTrue(staged.temporary.is_file())
            self.assertFalse(staged.destination.exists())
            self.assertEqual(staged.document["fact_tier"], "T1")
            self.assertEqual(staged.document["completeness"], "complete_exact")
            index = RustSyntaxIndex(staged.document)
            definitions = index.definition_candidates("run", target_path="src/lib.rs")
            references = index.reference_candidates("run")
            self.assertEqual(len(definitions), 1)
            self.assertEqual(len(references), 1)
            self.assertEqual(definitions[0].provenance.fact_tier, "T1")
            self.assertEqual(
                definitions[0].provenance.completeness, "syntactic_candidates"
            )
            staged.publish()
            self.assertTrue(staged.destination.is_file())
            staged.rollback()
            self.assertFalse(staged.destination.exists())
        self.assertEqual(
            sorted(path.relative_to(self.repository) for path in self.repository.rglob("*")),
            before,
        )
        self.assertEqual(calls[0][0][0], str(self.scanner))
        self.assertEqual(calls[0][1]["env"]["CARGO_NET_OFFLINE"], "true")

    def test_output_must_cover_exact_scope_and_match_provider_identity(self) -> None:
        valid = self.output(["src/lib.rs"])
        normalized = validate_scanner_output(valid, ("src/lib.rs",))
        self.assertEqual(normalized["facts"][0]["name"], "run")
        missing = self.output([])
        with self.assertRaisesRegex(RustSyntaxError, "exact source scope"):
            validate_scanner_output(missing, ("src/lib.rs",))
        wrong = self.output(["src/lib.rs"])
        wrong["provider_version"] = "9.9.9"
        with self.assertRaisesRegex(RustSyntaxError, "identity mismatch"):
            validate_scanner_output(wrong, ("src/lib.rs",))

    def test_failure_timeout_and_unsafe_generation_publish_nothing(self) -> None:
        def failed(argv, **_kwargs):
            return subprocess.CompletedProcess(argv, 1, b"", b"scanner failed")

        provider = RustSyntaxProvider(
            self.scanner, self.repository, self.data, "project", runner=failed
        )
        with self.assertRaisesRegex(RustSyntaxError, "scanner failed"):
            provider.stage(self.generation)
        unsafe = dict(self.generation, generation_id="../escape")
        with self.assertRaisesRegex(RustSyntaxError, "unsafe"):
            provider.stage(unsafe)
        self.assertFalse((self.data / "rust-syntax").exists())

    def test_resource_limit_rejects_candidate_before_scanner_start(self) -> None:
        called = False

        def runner(*_args, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("scanner must not start")

        oversized = copy.deepcopy(self.generation)
        oversized["files"][0]["size"] = MAX_SOURCE_FILE_BYTES + 1
        provider = RustSyntaxProvider(
            self.scanner, self.repository, self.data, "project", runner=runner
        )
        with self.assertRaisesRegex(RustSyntaxError, "per-file limit"):
            provider.stage(oversized)
        self.assertFalse(called)
        self.assertFalse((self.data / "rust-syntax").exists())


if __name__ == "__main__":
    unittest.main()
