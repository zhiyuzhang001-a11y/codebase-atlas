"""All filesystem/clock calls mocked, no real source access or execution."""
import errno
import hashlib
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.rust_semantic_source_file import SourceFile


class SourceFileTests(unittest.TestCase):
    def reader(self, clock=None, **overrides):
        self.raw = b'# fixed trusted definitions\n'
        self.directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_dev=1,
                                         st_ino=10, st_uid=1000)
        self.file = SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_dev=1,
                                    st_ino=11, st_uid=1000, st_size=len(self.raw),
                                    st_mtime_ns=100, st_ctime_ns=101, st_nlink=1)
        self.os = SimpleNamespace(O_RDONLY=0, O_CLOEXEC=524288, O_NOFOLLOW=131072,
                                  O_NONBLOCK=2048, getpid=Mock(return_value=90),
                                  getuid=Mock(return_value=1000), open=Mock(return_value=11),
                                  fstat=Mock(side_effect=lambda fd: self.directory if fd == 10 else self.file),
                                  stat=Mock(side_effect=lambda *a, **kw: self.file),
                                  get_inheritable=Mock(return_value=False),
                                  read=Mock(return_value=self.raw), close=Mock())
        args = dict(name='rust_semantic_ptrace.py', directory_fd=10,
                    directory=dict(device=1, inode=10, uid=1000),
                    expected=dict(device=1, inode=11, uid=1000, size=len(self.raw),
                                  mtime_ns=100, ctime_ns=101,
                                  sha256=hashlib.sha256(self.raw).hexdigest()),
                    source_sha='a'*40, active_deadline=20.)
        args.update(overrides)
        with patch('scripts.rust_semantic_source_file.sys.platform', 'linux'):
            return SourceFile(**args, os_api=self.os, clock=clock or (lambda: 1.))

    def test_same_fd_exact_bytes_and_copied_receipt_not_source_policy(self):
        reader = self.reader()
        self.assertEqual(reader.read(), self.raw)
        self.os.open.assert_called_once_with('rust_semantic_ptrace.py', 657408, dir_fd=10)
        self.os.read.assert_called_once_with(11, 65537)
        self.os.stat.assert_called_once_with('rust_semantic_ptrace.py', dir_fd=10, follow_symlinks=False)
        self.os.close.assert_called_once_with(11)
        row = reader.report()
        row['sample']['before']['inode'] = 99
        self.assertEqual(reader.report()['sample']['before']['inode'], 11)
        self.assertFalse(reader.report()['qualified'])
        self.assertFalse(reader.report()['source_policy_audited'])
        with self.assertRaises(RuntimeError): reader.read()

    def test_unknown_names_hash_receipts_and_directory_rejected_before_open(self):
        for name in ('../rust_semantic_ptrace.py', '/tmp/evil.py', 'evil.py', True):
            with self.assertRaises(ValueError): self.reader(name=name)
        for kind in ('inode', 'uid', 'mode', 'inherit'):
            reader = self.reader()
            if kind == 'inode': self.directory.st_ino = 99
            if kind == 'uid': self.directory.st_uid = 0
            if kind == 'mode': self.directory.st_mode |= 0o020
            if kind == 'inherit': self.os.get_inheritable.return_value = True
            with self.assertRaises(ValueError): reader.read()
            self.os.open.assert_not_called()

    def test_created_fd_bad_file_or_flags_closed_once(self):
        for kind in ('inode', 'uid', 'mode', 'link', 'symlink', 'inherit', 'size'):
            reader = self.reader()
            if kind == 'inode': self.file.st_ino = 99
            if kind == 'uid': self.file.st_uid = 0
            if kind == 'mode': self.file.st_mode |= 0o002
            if kind == 'link': self.file.st_nlink = 2
            if kind == 'symlink': self.file.st_mode = stat.S_IFLNK
            if kind == 'inherit': self.os.get_inheritable.side_effect = [False, True]
            if kind == 'size': self.file.st_size = 65537
            with self.assertRaises(ValueError): reader.read()
            self.os.read.assert_not_called()
            self.os.close.assert_called_once_with(11)
        for fd, closes in ((True, 0), (10, 0), (0, 1), (65536, 1)):
            reader = self.reader()
            self.os.open.return_value = fd
            with self.assertRaises(ValueError): reader.read()
            self.assertEqual(self.os.close.call_count, closes)

    def test_wrong_short_overflow_raw_preserved_before_refusal(self):
        for raw in (b'wrong', b'', b'x'*65537):
            reader = self.reader()
            self.os.read.return_value = raw
            with self.assertRaises(ValueError): reader.read()
            self.assertEqual(reader.report()['sample']['read_bytes'], len(raw))
            self.assertEqual(reader.report()['sample']['raw_hex'], raw[:65536].hex())
            self.os.close.assert_called_once_with(11)
        reader = self.reader()
        self.os.read.side_effect = OSError(errno.EINTR, 'uncertain')
        self.os.close.side_effect = OSError(errno.EIO, 'close')
        with self.assertRaises(OSError) as caught: reader.read()
        self.assertEqual(caught.exception.errno, errno.EINTR)
        self.assertEqual(reader.report()['sample']['close_errno'], errno.EIO)
        self.os.close.assert_called_once_with(11)

    def test_changed_file_path_directory_and_uncertain_success_close(self):
        for kind in ('time', 'path', 'directory'):
            reader = self.reader()
            def read(fd, size):
                if kind == 'time': self.file.st_ctime_ns = 102
                if kind == 'directory': self.directory.st_ino = 12
                if kind == 'path':
                    self.os.stat.side_effect = lambda *a, **kw: SimpleNamespace(**dict(
                        self.file.__dict__, st_ino=12))
                return self.raw
            self.os.read.side_effect = read
            with self.assertRaises(ValueError): reader.read()
            self.assertEqual(reader.report()['sample']['raw_hex'], self.raw.hex())
            self.os.close.assert_called_once_with(11)
        reader = self.reader()
        self.os.close.side_effect = OSError(errno.EINTR, 'uncertain')
        with self.assertRaises(OSError): reader.read()
        with self.assertRaises(RuntimeError): reader.read()
        self.os.close.assert_called_once_with(11)
        self.assertEqual(reader.report()['sample']['close_errno'], errno.EINTR)

    def test_final_clock_includes_close_and_never_reopens(self):
        now = [1.]
        reader = self.reader(lambda: now[0])
        self.os.close.side_effect = lambda fd: now.__setitem__(0, 1.6)
        with self.assertRaises(ValueError): reader.read()
        self.assertEqual(reader.report()['sample']['finished'], 1.6)
        self.os.close.assert_called_once_with(11)
        for times in ((1., 0.), (1., 20.)):
            ticks = iter(times)
            reader = self.reader(lambda: next(ticks))
            with self.assertRaises(ValueError): reader.read()
            self.os.open.assert_not_called()
        reader = self.reader()
        self.os.getpid.return_value = 91
        with self.assertRaises(ValueError): reader.read()
        self.os.open.assert_not_called()


if __name__ == '__main__':
    unittest.main()
