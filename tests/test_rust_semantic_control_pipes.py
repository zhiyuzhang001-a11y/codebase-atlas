"""All OS calls injected: no real pipes, file descriptors, reads or signals."""
import errno
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.rust_semantic_control_budget import ControlBudget
from scripts.rust_semantic_control_pipes import ControlPipes


class ControlPipesTests(unittest.TestCase):
    def setUp(self):
        platform = patch('scripts.rust_semantic_control_pipes.sys.platform', 'linux')
        platform.start()
        self.addCleanup(platform.stop)
        self.budget = ControlBudget(0)
        self.descriptors = {'stdout': 10, 'stderr': 11, 'trace': 12}
        self.stats = {fd: SimpleNamespace(st_dev=1, st_ino=fd, st_uid=1000,
                                         st_mode=stat.S_IFIFO | 0o600)
                      for fd in self.descriptors.values()}
        self.os = SimpleNamespace(fstat=Mock(side_effect=lambda fd: self.stats[fd]),
                                  getuid=Mock(return_value=1000),
                                  get_inheritable=Mock(return_value=False),
                                  O_ACCMODE=3, O_RDONLY=0, O_NONBLOCK=2048,
                                  read=Mock(return_value=b'x'))
        self.fc = SimpleNamespace(F_GETFL=3, fcntl=Mock(return_value=2048))

    def make(self):
        return ControlPipes(self.descriptors, self.budget, self.os, self.fc)

    def test_three_bounded_nonblocking_reads_and_eof_not_reap(self):
        pipes = self.make()
        self.assertEqual(pipes.tick(lambda: 1), 3)
        self.assertEqual(self.os.read.call_count, 3)
        for fd in (10, 11, 12):
            self.os.read.assert_any_call(fd, 65536)
        self.os.read.return_value = b''
        self.assertEqual(pipes.tick(lambda: 2), 0)
        self.assertTrue(self.budget.report()['all_eof'])
        self.os.read.reset_mock()
        pipes.tick(lambda: 3)
        self.os.read.assert_not_called()
        self.assertFalse(pipes.report()['outer_cleanup_complete'])

    def test_invalid_blocking_write_foreign_nonpipe_and_alias_rejected(self):
        for flags in (0, 2049, 2050):
            self.fc.fcntl.return_value = flags
            with self.assertRaises(ValueError):
                self.make()
        self.fc.fcntl.return_value = 2048
        for attr, value in (('st_uid', 1001), ('st_mode', stat.S_IFREG | 0o600), ('st_ino', 10)):
            old = getattr(self.stats[11], attr)
            setattr(self.stats[11], attr, value)
            with self.assertRaises(ValueError):
                self.make()
            setattr(self.stats[11], attr, old)
        self.os.get_inheritable.return_value = True
        with self.assertRaises(ValueError):
            self.make()
        self.os.read.assert_not_called()

    def test_fd_and_platform_bounds(self):
        for descriptors in ({}, {**self.descriptors, 'trace': 10},
                            {**self.descriptors, 'trace': True},
                            {**self.descriptors, 'trace': 1},
                            {**self.descriptors, 'trace': 65536}):
            with self.assertRaises(ValueError):
                ControlPipes(descriptors, self.budget, self.os, self.fc)
        with patch('scripts.rust_semantic_control_pipes.sys.platform', 'win32'):
            with self.assertRaises(ValueError):
                self.make()

    def test_changed_fd_before_read_rejected_and_after_read_bytes_retained(self):
        pipes = self.make()
        self.stats[11].st_ino = 99
        with self.assertRaises(ValueError):
            pipes.tick(lambda: 1)
        self.os.read.assert_not_called()  # stderr is first sorted stream
        self.stats[11].st_ino = 11
        with self.assertRaises(RuntimeError):
            pipes.tick(lambda: 1)
        self.budget.begin_cleanup(1)
        def read(fd, size):
            self.stats[fd].st_ino = 99
            return b'observed'
        self.os.read.side_effect = read
        with self.assertRaises(ValueError):
            pipes.tick(lambda: 2, cleanup=True)
        self.assertEqual(self.budget.report()['streams']['stderr']['seen'], 8)
        self.assertEqual(len(pipes.errors), 2)

    def test_eagain_eintr_no_retry_and_unknown_error_kept(self):
        pipes = self.make()
        for error in (errno.EAGAIN, errno.EINTR):
            self.os.read.reset_mock()
            self.os.read.side_effect = OSError(error, 'mock')
            self.assertEqual(pipes.tick(lambda: 1), 0)
            self.assertEqual(self.os.read.call_count, 3)
        self.os.read.side_effect = OSError(errno.EBADF, 'mock')
        with self.assertRaises(OSError):
            pipes.tick(lambda: 2)
        self.assertEqual(pipes.errors[-1]['errno'], errno.EBADF)
        self.assertFalse(self.budget.report()['all_eof'])

    def test_postread_deadline_and_cleanup_still_account_raw(self):
        pipes = self.make()
        times = iter((19, 20))
        with self.assertRaises(TimeoutError):
            pipes.tick(lambda: next(times))
        self.assertEqual(self.budget.total_seen, 1)
        self.budget.begin_cleanup(20)
        self.assertEqual(pipes.tick(lambda: 21, cleanup=True), 3)
        with self.assertRaises(TimeoutError):
            pipes.tick(lambda: 30, cleanup=True)
        self.assertEqual(self.budget.total_seen, 4)
        with self.assertRaises(ValueError):
            pipes.tick(lambda: 30, cleanup=1)

    def test_overflow_keeps_chunk_and_error_then_cleanup_can_drain(self):
        pipes = self.make()
        self.budget.total_seen = self.budget.total_retained = self.budget.LIMIT
        with self.assertRaises(OverflowError):
            pipes.tick(lambda: 1)
        self.assertEqual(self.budget.report()['streams']['stderr']['omitted'], 1)
        self.budget.begin_cleanup(2)
        self.os.read.return_value = b''
        pipes.tick(lambda: 3, cleanup=True)
        self.assertTrue(self.budget.report()['all_eof'])
        self.assertFalse(pipes.report()['qualified'])
