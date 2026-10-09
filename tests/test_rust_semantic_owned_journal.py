"""Injected mocks only; no native pipe creation, read, write or close."""
import errno
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.rust_semantic_owned_journal import OwnedJournalPipe
from scripts.rust_semantic_journal_pipe import JournalPipe


class OwnedJournalTests(unittest.TestCase):
    def owner(self, clock=None):
        self.infos = {fd: SimpleNamespace(st_dev=1, st_ino=50, st_uid=1000,
                                         st_mode=stat.S_IFIFO) for fd in (10, 11)}
        self.os = SimpleNamespace(O_RDONLY=0, O_WRONLY=1, O_ACCMODE=3,
                                  O_NONBLOCK=2048, O_CLOEXEC=524288,
                                  getpid=Mock(return_value=90), getuid=Mock(return_value=1000),
                                  pipe2=Mock(return_value=(10, 11)),
                                  fstat=Mock(side_effect=lambda fd: self.infos[fd]),
                                  get_inheritable=Mock(return_value=False), close=Mock())
        self.flags = SimpleNamespace(F_GETFL=3, fcntl=Mock(side_effect=lambda fd, op: 2048 + (fd == 11)))
        with patch('scripts.rust_semantic_owned_journal.sys.platform', 'linux'):
            return OwnedJournalPipe(20., os_api=self.os, fcntl_api=self.flags,
                                    clock=clock or (lambda: 1.))

    def test_created_receipt_composes_borrowed_adapter_and_closes_once(self):
        owner = self.owner()
        receipt = owner.allocate()
        self.os.pipe2.assert_called_once_with(526336)
        with patch('scripts.rust_semantic_journal_pipe.sys.platform', 'linux'):
            adapter = JournalPipe('reader', receipt['read_fd'], receipt['expected'], 90, 100,
                                  20., os_api=self.os, fcntl_api=self.flags, clock=lambda: 1.)
        self.assertEqual(adapter.report()['expected']['inode'], 50)
        receipt['expected']['inode'] = 99
        self.assertEqual(owner.borrow()['expected']['inode'], 50)
        report = owner.report()
        report['receipt']['identity']['inode'] = 99
        self.assertEqual(owner.report()['receipt']['identity']['inode'], 50)
        owner.close_parent_writer()
        with self.assertRaises(ValueError): owner.borrow()
        owner.close_all()
        owner.close_all()
        self.assertEqual([c.args[0] for c in self.os.close.call_args_list], [11, 10])
        self.assertFalse(owner.report()['qualified'])
        self.assertFalse(owner.report()['source_authenticated'])

    def test_invalid_pair_registers_all_valid_members_before_refusal(self):
        for pair, closed in (((True, 11), [11]), ((10, -1), [10]),
                             ((0, 11), [0, 11]), ((10, 65536), [10, 65536]),
                             ((10, 10), [10]), ((10, 'foreign'), [10])):
            owner = self.owner()
            self.os.pipe2.return_value = pair
            with self.assertRaises(ValueError): owner.allocate()
            self.assertEqual([c.args[0] for c in self.os.close.call_args_list], closed)
            self.assertEqual(owner.report()['owned_fds'], [])
            with self.assertRaises(RuntimeError): owner.allocate()
            self.os.pipe2.assert_called_once()

    def test_flag_identity_failures_rollback_both(self):
        for bad in ('inode', 'uid', 'file', 'mode', 'blocking', 'inherit'):
            owner = self.owner()
            if bad == 'inode': self.infos[11].st_ino = 51
            if bad == 'uid': self.infos[11].st_uid = 1001
            if bad == 'file': self.infos[11].st_mode = stat.S_IFREG
            if bad == 'mode': self.flags.fcntl.side_effect = lambda fd, op: 2048
            if bad == 'blocking': self.flags.fcntl.side_effect = lambda fd, op: int(fd == 11)
            if bad == 'inherit': self.os.get_inheritable.return_value = True
            with self.assertRaises(ValueError): owner.allocate()
            self.assertEqual(self.os.close.call_count, 2)
            self.assertFalse(owner.report()['ready'])

    def test_close_error_retired_no_retry_and_other_fd_still_closed(self):
        owner = self.owner()
        owner.allocate()
        self.os.close.side_effect = [OSError(errno.EINTR, 'uncertain'), None]
        owner.close_all()
        owner.close_all()
        self.assertEqual(self.os.close.call_count, 2)
        self.assertEqual(owner.report()['errors'][0]['errno'], errno.EINTR)
        self.assertEqual(owner.report()['owned_fds'], [])

    def test_replaced_fd_refused_and_same_controller_required(self):
        owner = self.owner()
        owner.allocate()
        self.infos[10].st_ino = 999
        with self.assertRaises(ValueError): owner.borrow()
        owner.close_all()
        self.os.close.assert_called_once_with(11)
        self.assertEqual(owner.report()['errors'][0]['fd'], 10)
        owner = self.owner()
        owner.allocate()
        self.os.getpid.return_value = 91
        with self.assertRaises(RuntimeError): owner.borrow()
        with self.assertRaises(RuntimeError): owner.close_all()
        self.os.close.assert_not_called()

    def test_clock_failure_and_allocation_error_never_reopen(self):
        for times, calls in (((1., 1., 1.6), 1), ((1., 0.), 0), ((1., 20.), 0)):
            clock = iter(times)
            owner = self.owner(lambda: next(clock))
            with self.assertRaises(ValueError): owner.allocate()
            self.assertEqual(self.os.pipe2.call_count, calls)
            self.assertEqual(self.os.close.call_count, calls * 2)
            with self.assertRaises(RuntimeError): owner.allocate()
        owner = self.owner()
        self.os.pipe2.side_effect = OSError(errno.EMFILE, 'limit')
        with self.assertRaises(OSError): owner.allocate()
        self.os.close.assert_not_called()
        with self.assertRaises(RuntimeError): owner.allocate()

    def test_final_borrow_failure_inside_rollback_and_late_final_check(self):
        for nth in (3, 4):
            owner = self.owner()
            calls = 0
            def fstat(fd):
                nonlocal calls
                calls += 1
                if calls == nth:
                    raise OSError(errno.EIO, 'final borrow')
                return self.infos[fd]
            self.os.fstat.side_effect = fstat
            with self.assertRaises(OSError): owner.allocate()
            self.assertEqual(self.os.close.call_count, 2)
            self.assertFalse(owner.report()['ready'])
        now = [1.]
        owner = self.owner(lambda: now[0])
        def final_flags(fd, op):
            if self.flags.fcntl.call_count == 4:
                now[0] = 1.6
            return 2048 + (fd == 11)
        self.flags.fcntl.side_effect = final_flags
        with self.assertRaises(ValueError): owner.allocate()
        self.assertEqual(owner.report()['receipt']['finished'], 1.6)
        self.assertEqual(self.os.close.call_count, 2)


if __name__ == '__main__':
    unittest.main()
