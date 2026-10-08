"""Mock-only tests: never spawn, signal, or inspect native process identities."""
import errno
import stat
import types
import unittest
from unittest.mock import patch

from scripts.rust_semantic_owned_group import OwnedGroup


class FakeOS:
    O_RDONLY, O_DIRECTORY, O_CLOEXEC, O_NOFOLLOW = 0, 1, 2, 4

    def __init__(self):
        self.calls, self.closed = [], []
        self.info = b'Pid:\t200\n'
        self.signal_error = None
        self.inode = 8
        self.inherit = False
        self.close_error = False
        self.read_error = self.fdinfo_close_error = False

    def getpid(self): return 100
    def getpgrp(self): return 100
    def getsid(self, pid): return 100
    def getuid(self): return 501
    def open(self, path, flags): return 10 if path == '/proc/200' else 11
    def read(self, fd, bound):
        if self.read_error:
            raise OSError(errno.EIO, 'read failure')
        return self.info
    def get_inheritable(self, fd): return self.inherit
    def fstat(self, fd):
        return types.SimpleNamespace(st_dev=1, st_ino=self.inode if fd == 8 else 10,
                                     st_uid=501, st_mode=stat.S_IFDIR)
    def close(self, fd):
        self.closed.append(fd)
        if self.close_error and fd == 10:
            raise OSError(errno.EINTR, 'uncertain close')
        if self.fdinfo_close_error and fd == 11:
            raise OSError(errno.EINTR, 'fdinfo uncertain close')
    def killpg(self, group, sig):
        self.calls.append((group, sig))
        if self.signal_error:
            raise OSError(self.signal_error, 'signal error')


class Resources:
    def __init__(self):
        self.identity = dict(pid=200, ppid=100, pgrp=200, session=200,
                             starttime=77, state='T')
        self.status = b'Pid: 200\nTgid: 200\nUid: 501 501 501 501\nTracerPid: 0\n'
    def _read_at(self, fd, name, bound): return self.status if name == 'status' else b'stat'
    def proc_identity(self, raw): return dict(self.identity)


class OwnedGroupTests(unittest.TestCase):
    def setUp(self):
        self.platform = patch('scripts.rust_semantic_owned_group.sys.platform', 'linux')
        self.platform.start()
        self.addCleanup(self.platform.stop)
        self.os, self.resources = FakeOS(), Resources()
        self.wait = types.SimpleNamespace(pid=200, pidfd=8, reap_attempted=False, reaped=False)

    def bind(self, clock=lambda: 1):
        return OwnedGroup(self.wait, self.resources, self.os, clock)

    def test_cancel_unreaped_once_then_read_only_absence(self):
        owner = self.bind()
        self.resources.identity['state'] = 'Z'
        self.assertTrue(owner.cancel_owned_group())
        with self.assertRaises(RuntimeError): owner.cancel_owned_group()
        with self.assertRaises(ValueError): owner.group_absent_after_reap()
        self.wait.reaped = self.wait.reap_attempted = True
        self.os.signal_error = errno.ESRCH
        self.assertTrue(owner.group_absent_after_reap())
        self.assertEqual(self.os.calls, [(200, 9), (200, 0)])
        self.assertFalse(owner.report()['qualified'])
        packet = owner.report()['verifications'][-1]
        self.assertTrue(packet['verified'])
        self.assertEqual(packet['stat_after']['state'], 'Z')
        self.assertIn('fdinfo_hex', packet)
        self.assertIn('status_hex', packet)
        owner.close()
        self.assertNotIn(8, self.os.closed)

    def test_binding_requires_stopped_owned_child_session(self):
        for field, value in [('state', 'S'), ('ppid', 99), ('pgrp', 99),
                             ('session', 99), ('starttime', 0)]:
            resources = Resources()
            resources.identity[field] = value
            with self.assertRaises(ValueError):
                OwnedGroup(self.wait, resources, self.os, lambda: 1)
        for pid in (0, 1, 100, True):
            self.wait.pid = pid
            with self.assertRaises(ValueError): self.bind()
        self.assertEqual(self.os.calls, [])

    def test_changed_lifetime_fd_or_reap_refuses_signal(self):
        for mutation in ('starttime', 'pidfd', 'reap', 'status', 'fdinfo'):
            self.setUp_case()
            owner = self.bind()
            if mutation == 'starttime': self.resources.identity['starttime'] = 78
            if mutation == 'pidfd': self.os.inode = 9
            if mutation == 'reap': self.wait.reap_attempted = True
            if mutation == 'status': self.resources.status += b'Pid: 200\n'
            if mutation == 'fdinfo': self.os.info = b'Pid: -1\n'
            with self.assertRaises(ValueError): owner.cancel_owned_group()
            self.assertEqual(self.os.calls, [])
            self.assertEqual(owner.report()['verifications'][-1]['error'], 'ValueError')
            owner.close()

    def setUp_case(self):
        self.os, self.resources = FakeOS(), Resources()
        self.wait = types.SimpleNamespace(pid=200, pidfd=8, reap_attempted=False, reaped=False)

    def test_signal_errors_never_imply_terminal_or_reap(self):
        for error in (errno.ESRCH, errno.EPERM):
            self.setUp_case()
            owner = self.bind()
            self.os.signal_error = error
            with self.assertRaises(OSError): owner.cancel_owned_group()
            self.assertFalse(self.wait.reaped)
            self.assertEqual(owner.report()['records'][0]['errno'], error)
            with self.assertRaises(RuntimeError): owner.cancel_owned_group()

    def test_absence_permission_error_is_not_absence(self):
        owner = self.bind()
        self.wait.reaped = True
        self.assertFalse(owner.group_absent_after_reap())
        self.os.signal_error = errno.EPERM
        with self.assertRaises(OSError): owner.group_absent_after_reap()
        self.assertTrue(all(sig == 0 for group, sig in self.os.calls))

    def test_late_clock_and_uncertain_close_fail_closed(self):
        ticks = iter([1, 2])
        with self.assertRaises(ValueError): self.bind(lambda: next(ticks))
        with self.assertRaises(ValueError): self.bind(lambda: float('nan'))
        owner = self.bind()
        self.os.close_error = True
        with self.assertRaises(OSError): owner.close()
        owner.close()
        self.assertIsNone(owner.fd)
        self.assertEqual(owner.report()['records'][-1]['errno'], errno.EINTR)

    def test_failed_binding_preserves_primary_failure_with_close_error(self):
        self.resources.identity['state'] = 'S'
        self.os.close_error = True
        evidence = {}
        with self.assertRaisesRegex(ValueError, 'lifetime'):
            OwnedGroup(self.wait, self.resources, self.os, lambda: 1, evidence)
        self.assertEqual(evidence['verifications'][0]['error'], 'ValueError')
        self.assertEqual(evidence['records'][-1]['errno'], errno.EINTR)

    def test_fdinfo_read_and_close_failures_preserve_both(self):
        self.os.read_error = self.os.fdinfo_close_error = True
        evidence = {}
        with self.assertRaises(OSError) as raised:
            OwnedGroup(self.wait, self.resources, self.os, lambda: 1, evidence)
        self.assertEqual(raised.exception.errno, errno.EIO)
        packet = evidence['verifications'][0]
        self.assertEqual(packet['errno'], errno.EIO)
        self.assertEqual(packet['fdinfo_close_errno'], errno.EINTR)


if __name__ == '__main__':
    unittest.main()
