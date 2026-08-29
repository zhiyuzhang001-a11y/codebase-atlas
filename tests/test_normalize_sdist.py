from __future__ import annotations

import gzip
from io import BytesIO
from pathlib import Path
import tarfile
import tempfile
import unittest

from scripts.normalize_sdist import normalize_sdist


class NormalizeSdistTests(unittest.TestCase):
    def _write_archive(self, path: Path, *, mtime: int, owner: str) -> None:
        payload = b"same release payload\n"
        with tarfile.open(path, mode="w:gz") as archive:
            root = tarfile.TarInfo("package-1.0/")
            root.type = tarfile.DIRTYPE
            root.mtime = mtime
            root.uname = owner
            archive.addfile(root)
            member = tarfile.TarInfo("package-1.0/file.txt")
            member.size = len(payload)
            member.mtime = mtime
            member.uname = owner
            archive.addfile(member, BytesIO(payload))

    def test_different_source_metadata_normalizes_to_identical_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / "first.tar.gz"
            second = root / "second.tar.gz"
            self._write_archive(first, mtime=10, owner="first")
            self._write_archive(second, mtime=20, owner="second")
            first_output = root / "first-normalized.tar.gz"
            second_output = root / "second-normalized.tar.gz"

            normalize_sdist(first, first_output, 1_767_225_600)
            normalize_sdist(second, second_output, 1_767_225_600)

            self.assertEqual(first_output.read_bytes(), second_output.read_bytes())
            with gzip.open(first_output) as stream:
                self.assertTrue(stream.read())

    def test_unsafe_member_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "unsafe.tar.gz"
            with tarfile.open(source, mode="w:gz") as archive:
                archive.addfile(tarfile.TarInfo("../escape"))
            with self.assertRaisesRegex(ValueError, "unsafe sdist member"):
                normalize_sdist(source, root / "output.tar.gz", 0)


if __name__ == "__main__":
    unittest.main()
