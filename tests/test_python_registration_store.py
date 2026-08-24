from __future__ import annotations

import json
import ast
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codebase_atlas.python_registration_store import (
    RegistrationIndexError,
    load_registration_index,
    registration_index_health,
    registration_index_path,
    stage_registration_index,
)


class PythonRegistrationStoreTests(unittest.TestCase):
    def repository(self, root: Path) -> Path:
        repository = root / "repo"
        repository.mkdir()
        (repository / "routes.py").write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n\n"
            "def view():\n    pass\n\n"
            "app.add_url_rule('/view', view_func=view)\n",
            encoding="utf-8",
        )
        (repository / "other.py").write_text("value = 1\n", encoding="utf-8")
        return repository

    def test_stages_validates_and_atomically_loads_exact_relations(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = self.repository(root)
            data_dir = root / "data"
            with stage_registration_index(
                data_dir, repository, "project", "a" * 64
            ) as staged:
                self.assertFalse(registration_index_path(data_dir).exists())
                staged.publish()
            index = load_registration_index(
                data_dir, repository, "project", "a" * 64
            )
            self.assertEqual(len(index.registrations), 1)
            self.assertEqual(index.registrations[0].edge.relation, "registers")
            health = registration_index_health(
                data_dir, repository, "project", "a" * 64
            )
            self.assertTrue(health["ok"])
            self.assertEqual((health["files"], health["registrations"]), (2, 1))

    def test_windows_skips_unsupported_directory_fsync(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = self.repository(root)
            with stage_registration_index(
                root / "data", repository, "project", "a" * 64
            ) as staged:
                with patch(
                    "codebase_atlas.python_registration_store._DIRECTORY_FSYNC_SUPPORTED",
                    False,
                ), patch(
                    "codebase_atlas.python_registration_store.os.open"
                ) as open_directory:
                    staged.publish()
                open_directory.assert_not_called()

    def test_generation_is_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = self.repository(root)
            data_dir = root / "data"
            with stage_registration_index(
                data_dir, repository, "project", "b" * 64
            ) as staged:
                staged.publish()
            first = registration_index_path(data_dir).read_bytes()
            with stage_registration_index(
                data_dir, repository, "project", "b" * 64
            ) as staged:
                staged.publish()
            self.assertEqual(registration_index_path(data_dir).read_bytes(), first)

    def test_rejects_corruption_stale_identity_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = self.repository(root)
            data_dir = root / "data"
            with stage_registration_index(
                data_dir, repository, "project", "c" * 64
            ) as staged:
                staged.publish()
            path = registration_index_path(data_dir)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["files"][0]["content_sha256"] = "0" * 64
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(RegistrationIndexError, "generation hash"):
                load_registration_index(data_dir, repository, "project", "c" * 64)

            path.unlink()
            target = root / "foreign.json"
            target.write_text("{}", encoding="utf-8")
            path.symlink_to(target)
            health = registration_index_health(
                data_dir, repository, "project", "c" * 64
            )
            self.assertFalse(health["ok"])
            self.assertIn("safe regular file", health["reason"])

    def test_failed_stage_does_not_replace_previous_index(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = self.repository(root)
            data_dir = root / "data"
            with stage_registration_index(
                data_dir, repository, "project", "d" * 64
            ) as staged:
                staged.publish()
            before = registration_index_path(data_dir).read_bytes()
            (repository / "broken.py").write_bytes(b"\xff")
            # Invalid UTF-8 is tolerated by the analyzer, so force an invalid
            # identity before publication instead.
            with self.assertRaises(RegistrationIndexError):
                stage_registration_index(data_dir, repository, "", "e" * 64)
            self.assertEqual(registration_index_path(data_dir).read_bytes(), before)

    def test_incremental_generation_matches_clean_rebuild_and_parses_only_changes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = self.repository(root)
            bulk = repository / "bulk"
            bulk.mkdir()
            for index in range(6):
                (bulk / f"module_{index}.py").write_text(
                    f"def value_{index}():\n    return {index}\n", encoding="utf-8"
                )
            incremental = root / "incremental"
            clean = root / "clean"
            with stage_registration_index(
                incremental, repository, "project", "1" * 64
            ) as staged:
                staged.publish()

            (bulk / "added.py").write_text("def added():\n    return 1\n")
            (bulk / "module_1.py").write_text(
                "def value_1():\n    return 2\n\ndef edited():\n    return 3\n"
            )
            (bulk / "module_2.py").unlink()
            (bulk / "module_3.py").rename(bulk / "renamed.py")
            routes = repository / "routes.py"
            routes.write_text(
                routes.read_text()
                + "\ndef second():\n    pass\n\n"
                + "app.add_url_rule('/second', view_func=second)\n"
            )

            with patch(
                "codebase_atlas.providers.python_registrations.ast.parse",
                wraps=ast.parse,
            ) as parse:
                with stage_registration_index(
                    incremental,
                    repository,
                    "project",
                    "2" * 64,
                    previous_source_fingerprint="1" * 64,
                ) as staged:
                    staged.publish()
            parsed = {
                Path(call.kwargs.get("filename", call.args[1] if len(call.args) > 1 else "")).name
                for call in parse.call_args_list
            }
            self.assertEqual(
                parsed, {"added.py", "module_1.py", "renamed.py", "routes.py"}
            )

            with stage_registration_index(
                clean, repository, "project", "2" * 64
            ) as staged:
                staged.publish()
            self.assertEqual(
                registration_index_path(incremental).read_bytes(),
                registration_index_path(clean).read_bytes(),
            )
            self.assertEqual(
                len(load_registration_index(
                    incremental, repository, "project", "2" * 64
                ).registrations),
                2,
            )


if __name__ == "__main__":
    unittest.main()
