from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from codebase_atlas.providers.python_references import PythonExactReferenceProvider
from codebase_atlas.providers.python_registrations import PythonRegistrationProvider


def git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    )


class GitAwarePythonInventoryTests(unittest.TestCase):
    def test_registration_inventory_uses_git_tracked_and_nonignored_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            git(repository, "init", "--quiet")
            (repository / ".gitignore").write_text("ignored/\n*.generated.py\n")
            (repository / "tracked.py").write_text("def tracked():\n    pass\n")
            (repository / "eligible.py").write_text("def eligible():\n    pass\n")
            (repository / "ignored").mkdir()
            (repository / "ignored/hidden.py").write_text("def hidden():\n    pass\n")
            (repository / "ignored.generated.py").write_text("def generated():\n    pass\n")
            try:
                (repository / "linked.py").symlink_to(repository / "tracked.py")
            except OSError:
                pass
            git(repository, "add", ".gitignore", "tracked.py")

            files = PythonRegistrationProvider(repository, "p").source_files()

        self.assertEqual(
            [path.relative_to(repository.resolve()).as_posix() for path in files],
            ["eligible.py", "tracked.py"],
        )

    def test_reference_inventory_cannot_return_git_ignored_facts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            git(repository, "init", "--quiet")
            (repository / ".gitignore").write_text("ignored/\n")
            package = repository / "pkg"
            package.mkdir()
            (package / "__init__.py").write_text("")
            (package / "target.py").write_text("def target():\n    return 1\n")
            (repository / "visible.py").write_text(
                "from pkg.target import target\nvalue = target()\n"
            )
            (repository / "ignored").mkdir()
            (repository / "ignored/hidden.py").write_text(
                "from pkg.target import target\nvalue = target()\n"
            )
            git(repository, "add", ".gitignore", "pkg/__init__.py", "pkg/target.py")

            rows = PythonExactReferenceProvider(repository).references(
                "target", target_path="pkg/target.py", timeout_ms=1000
            )

        self.assertEqual(
            [(row.location.path, row.location.start_line) for row in rows],
            [("visible.py", 2)],
        )


if __name__ == "__main__":
    unittest.main()
