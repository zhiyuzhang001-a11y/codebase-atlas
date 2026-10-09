"""All OS calls injected, including creation and close; no real FD operations."""
import errno
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.rust_semantic_outer_pipes import OuterPipes
from scripts.rust_semantic_control_pipes import ControlPipes
from scripts.rust_semantic_control_budget import ControlBudget


class OuterPipesTests(unittest.TestCase):
    def setUp(self):
        linux = patch('scripts.rust_semantic_outer_pipes.sys.platform', 'linux')
        linux.start()
        self.addCleanup(linux.stop)
        self.stats, self.blocking = {}, {}
        for read in (10, 12, 14):
            for fd in (read, read+1):
                self.stats[fd] = SimpleNamespace(st_dev=1, st_ino=read, st_uid=1000,
                                                st_mode=stat.S_IFIFO | 0o600)
                self.blocking[fd] = True
        self.os = SimpleNamespace(
            O_CLOEXEC=524288, O_ACCMODE=3, O_RDONLY=0, O_NONBLOCK=2048,
            pipe2=Mock(side_effect=[(10, 11), (12, 13), (14, 15)]),
            fstat=Mock(side_effect=lambda fd: self.stats[fd]),
            set_blocking=Mock(side_effect=lambda fd, flag: self.blocking.update({fd: flag})),
            get_blocking=Mock(side_effect=lambda fd: self.blocking[fd]),
            get_inheritable=Mock(return_value=False), getuid=Mock(return_value=1000),
            close=Mock(), read=Mock(return_value=b''))
        self.owner = OuterPipes(self.os)

    def test_created_receipts_feed_existing_borrowed_drain(self):
        reads, writes = self.owner.allocate()
        self.assertEqual(self.os.pipe2.call_args_list[0].args, (524288,))
        self.assertEqual(len(self.owner.report()['receipts']), 3)
        flags = SimpleNamespace(F_GETFL=3, fcntl=Mock(return_value=2048))
        drain = ControlPipes(reads, ControlBudget(0), self.os, flags)
        self.assertEqual(drain.tick(lambda: 1), 0)
        reads.clear()  # caller copy cannot rewrite owner map
        self.assertEqual(len(self.owner.borrow_reads()), 3)
        self.owner.close_parent_writes()
        self.assertEqual(set(self.owner.report()['owned_fds']), {10, 12, 14})
        with self.assertRaises(ValueError):
            self.owner.borrow_writes()
        self.owner.close_all()
        self.assertEqual(self.os.close.call_count, 6)
        self.owner.close_all()
        self.assertEqual(self.os.close.call_count, 6)
        self.assertFalse(self.owner.report()['qualified'])

    def test_partial_allocation_failure_closes_created_fds_not_foreign_fds(self):
        self.os.pipe2.side_effect = [(10, 11), OSError(errno.EMFILE, 'mock')]
        with self.assertRaises(OSError):
            self.owner.allocate()
        self.assertEqual({row.args[0] for row in self.os.close.call_args_list}, {10, 11})
        self.assertFalse(self.owner.ready)
        self.assertEqual(len(self.owner.report()['receipts']), 1)
        with self.assertRaises(RuntimeError):
            self.owner.allocate()
        self.assertEqual(self.os.pipe2.call_count, 2)

    def test_flag_or_identity_failure_still_closes_created_pair(self):
        for cause in ('inheritable', 'nonpipe', 'foreign', 'wrongpair'):
            with self.subTest(cause=cause):
                self.setUp()
                if cause == 'inheritable':
                    self.os.get_inheritable.return_value = True
                elif cause == 'nonpipe':
                    self.stats[10].st_mode = stat.S_IFREG
                elif cause == 'foreign':
                    self.stats[10].st_uid = 1001
                else:
                    self.stats[11].st_ino = 99
                with self.assertRaises(ValueError):
                    self.owner.allocate()
                self.assertEqual({row.args[0] for row in self.os.close.call_args_list}, {10, 11})
                self.assertFalse(self.owner.ready)

    def test_uncertain_close_not_retried_and_other_fds_are_closed(self):
        self.owner.allocate()
        def close(fd):
            if fd == 10:
                raise OSError(errno.EINTR, 'uncertain close')
        self.os.close.side_effect = close
        self.owner.close_all()
        self.assertEqual(self.os.close.call_count, 6)
        self.owner.close_all()
        self.assertEqual(self.os.close.call_count, 6)
        self.assertEqual(self.owner.report()['errors'][0]['errno'], errno.EINTR)

    def test_changed_object_not_borrowed_or_closed_and_platform_guard(self):
        self.owner.allocate()
        self.stats[10].st_ino = 99
        with self.assertRaises(ValueError):
            self.owner.borrow_reads()
        self.owner.close_all()
        self.assertNotIn(10, [row.args[0] for row in self.os.close.call_args_list])
        self.assertEqual(len(self.owner.errors), 1)
        self.assertFalse(self.owner.report()['outer_cleanup_complete'])
        with patch('scripts.rust_semantic_outer_pipes.sys.platform', 'win32'):
            with self.assertRaises(ValueError):
                OuterPipes(self.os)

    def test_final_borrow_failure_rolls_back_every_created_descriptor(self):
        for failed_call in (7, 10):  # final read and write handoff validation
            with self.subTest(failed_call=failed_call):
                self.setUp()
                calls = 0
                def fstat(fd):
                    nonlocal calls
                    calls += 1
                    if calls == failed_call:
                        raise OSError(errno.EIO, 'final handoff verification failed')
                    return self.stats[fd]
                self.os.fstat.side_effect = fstat
                with self.assertRaises(OSError):
                    self.owner.allocate()
                self.assertEqual(self.os.close.call_count, 6)
                self.assertEqual(self.owner.report()['owned_fds'], [])
                self.assertFalse(self.owner.ready)
                self.assertEqual(self.owner.errors[0]['operation'], 'allocate')
                with self.assertRaises(RuntimeError):
                    self.owner.allocate()
                self.assertEqual(self.os.pipe2.call_count, 3)


if __name__ == '__main__':
    unittest.main()
