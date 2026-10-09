"""Pure injected artifact FD tests; no actual file operations or evaluation."""
import errno
import hashlib
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.rust_semantic_artifact_fd import ArtifactFD


class ArtifactFDTests(unittest.TestCase):
    def reader(self, clock=None, **overrides):
        self.raw = b'# fixed definitions\n'
        self.file = SimpleNamespace(st_mode=stat.S_IFREG | 0o444, st_dev=1,
                                    st_ino=11, st_uid=1000, st_size=len(self.raw),
                                    st_mtime_ns=100, st_ctime_ns=101, st_nlink=1)
        self.os = SimpleNamespace(O_ACCMODE=3, O_RDONLY=0, O_NONBLOCK=2048,
                                  SEEK_CUR=1, getpid=Mock(return_value=90),
                                  getuid=Mock(return_value=1000),
                                  fstat=Mock(side_effect=lambda fd: self.file),
                                  lseek=Mock(side_effect=[0, len(self.raw)]),
                                  read=Mock(return_value=self.raw))
        self.fc = SimpleNamespace(F_GETFL=3, F_GETFD=1, FD_CLOEXEC=1,
                                  fcntl=Mock(side_effect=lambda fd, op: 2048 if op == 3 else 1))
        args = dict(fd=11, expected=dict(device=1, inode=11, uid=1000,
                    size=len(self.raw), mtime_ns=100, ctime_ns=101,
                    sha256=hashlib.sha256(self.raw).hexdigest()),
                    source_sha='a'*40, active_deadline=20.)
        args.update(overrides)
        with patch('scripts.rust_semantic_artifact_fd.sys.platform', 'linux'):
            return ArtifactFD(**args, os_api=self.os, fcntl_api=self.fc,
                              clock=clock or (lambda: 1.))

    def test_same_fd_once_and_copied_nonqualifying_receipts(self):
        reader = self.reader()
        self.assertEqual(reader.read(), self.raw)
        self.os.read.assert_called_once_with(11, 524289)
        self.assertEqual(self.os.lseek.call_args_list[0].args, (11, 0, 1))
        report = reader.report()
        report['expected']['inode'] = 99
        self.assertEqual(reader.report()['expected']['inode'], 11)
        for key in ('qualified', 'source_authenticated', 'source_policy_audited'):
            self.assertFalse(report[key])
        with self.assertRaises(RuntimeError): reader.read()

    def test_invalid_receipts_before_read(self):
        for fd in (True, 0, 65536):
            with self.assertRaises(ValueError): self.reader(fd=fd)
        for kind in ('inode', 'uid', 'writable', 'link', 'symlink', 'offset', 'flags', 'cloexec'):
            reader = self.reader()
            if kind == 'inode': self.file.st_ino = 12
            if kind == 'uid': self.file.st_uid = 0
            if kind == 'writable': self.file.st_mode |= 0o200
            if kind == 'link': self.file.st_nlink = 2
            if kind == 'symlink': self.file.st_mode = stat.S_IFLNK
            if kind == 'offset': self.os.lseek.side_effect = [1]
            if kind == 'flags': self.fc.fcntl.side_effect = lambda *a: 2049
            if kind == 'cloexec': self.fc.fcntl.side_effect = lambda fd, op: 2048 if op == 3 else 0
            with self.assertRaises(ValueError): reader.read()
            self.os.read.assert_not_called()

    def test_short_wrong_overflow_and_eintr_no_retry(self):
        for raw in (b'', b'wrong', b'x'*524289):
            reader = self.reader()
            self.os.read.return_value = raw
            with self.assertRaises(ValueError): reader.read()
            row = reader.report()['sample']
            self.assertEqual(row['read_bytes'], len(raw))
            self.assertEqual(row['raw_hex'], raw[:524288].hex())
            self.assertEqual(row['omitted'], max(0, len(raw)-524288))
            with self.assertRaises(RuntimeError): reader.read()
        reader = self.reader()
        self.os.read.side_effect = OSError(errno.EINTR, 'uncertain')
        with self.assertRaises(OSError): reader.read()
        self.assertEqual(reader.report()['sample']['errno'], errno.EINTR)
        with self.assertRaises(RuntimeError): reader.read()
        self.os.read.assert_called_once()

    def test_post_identity_flags_offset_and_time_refusal(self):
        for kind in ('identity', 'offset', 'flags', 'late', 'backwards', 'expired', 'controller'):
            clock = iter([1., 1., 1.6] if kind == 'late' else
                         [1., 1., .9] if kind == 'backwards' else
                         [1., 1., 20.] if kind == 'expired' else [1., 1., 1.])
            reader = self.reader(clock=lambda: next(clock))
            if kind == 'identity':
                other = SimpleNamespace(**vars(self.file)); other.st_ino = 99
                self.os.fstat.side_effect = [self.file, other]
            if kind == 'offset': self.os.lseek.side_effect = [0, 0]
            if kind == 'flags': self.fc.fcntl.side_effect = [2048, 1, 2048, 0]
            if kind == 'controller': self.os.getpid.side_effect = [90, 91]
            with self.assertRaises(ValueError): reader.read()
            with self.assertRaises(RuntimeError): reader.read()


if __name__ == '__main__':
    unittest.main()
