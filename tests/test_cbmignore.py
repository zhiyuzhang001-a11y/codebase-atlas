from pathlib import Path
import tempfile
import unittest

from codebase_atlas.cbmignore import CbmIgnore


class CbmIgnoreTests(unittest.TestCase):
    def matcher(self, text: str) -> CbmIgnore:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / ".cbmignore").write_text(text, encoding="utf-8")
        return CbmIgnore.load(root)

    def test_provider_style_globs_and_rooting(self) -> None:
        matcher = self.matcher("*.log\n**/build/\n/a/**/b\nfile?.txt\n")
        self.assertTrue(matcher.ignores("logs/error.log"))
        self.assertTrue(matcher.ignores("src/build", is_dir=True))
        self.assertTrue(matcher.ignores("a/x/y/b"))
        self.assertTrue(matcher.ignores("file1.txt"))
        self.assertFalse(matcher.ignores("src/a/b"))
        self.assertFalse(matcher.ignores("file12.txt"))

    def test_last_match_wins_and_directory_rules_cover_children(self) -> None:
        matcher = self.matcher("output/\n!output/\n*.tmp\n!important.tmp\n")
        self.assertFalse(matcher.ignores("output/result.py"))
        self.assertTrue(matcher.ignores("nested/result.tmp"))
        self.assertFalse(matcher.ignores("important.tmp"))

    def test_character_classes_comments_and_missing_file(self) -> None:
        matcher = self.matcher("# generated\nfile[0-9].txt\n")
        self.assertTrue(matcher.ignores("file3.txt"))
        self.assertFalse(matcher.ignores("fileA.txt"))
        with tempfile.TemporaryDirectory() as raw:
            self.assertFalse(CbmIgnore.load(Path(raw)).ignores("anything"))


if __name__ == "__main__":
    unittest.main()
