"""Injected/mocked pidfd adapter tests: no proc, ptrace or actual signals."""
import errno
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.rust_semantic_ptrace import NativeStops


class PidfdTests(unittest.TestCase):
    def setUp(self):
        self.native = object.__new__(NativeStops)
        self.native.session = 9
        self.native.observations = []
        self.native.handles = {}
        self.native.wait_stops = {10}
        self.native.wait_terminals = {}
        self.identity = {'pid': 10, 'ppid': 9, 'state': 't', 'starttime': 123,
                         'session': 9, 'pgrp': 9}
        self.status = b'Pid:\t10\nTgid:\t10\nTracerPid:\t9\nUid:\t1000 1000 1000 1000\n'
        self.info = b'pos:\t0\nflags:\t02000002\nPid:\t10\nNSpid:\t10\n'
        self.native.resources = SimpleNamespace(proc_identity=lambda raw: self.identity.copy(),
                                                 _read_at=self.read_at)
        self.patches = [patch('scripts.rust_semantic_ptrace.os.open', side_effect=[20, 21]*10),
                        patch('scripts.rust_semantic_ptrace.os.close'),
                        patch('scripts.rust_semantic_ptrace.os.getuid', return_value=1000, create=True),
                        patch('scripts.rust_semantic_ptrace.os.pidfd_open', return_value=30, create=True),
                        patch('scripts.rust_semantic_ptrace.os.get_inheritable', return_value=False),
                        patch('scripts.rust_semantic_ptrace.signal.pidfd_send_signal', create=True),
                        patch.multiple('scripts.rust_semantic_ptrace.os', create=True,
                                       O_DIRECTORY=0x10000, O_CLOEXEC=0x80000,
                                       O_NOFOLLOW=0x20000, WNOHANG=1)]
        self.mocks = [item.start() for item in self.patches]
        for item in self.patches:
            self.addCleanup(item.stop)

    def read_at(self, directory, name, limit):
        return {'stat': b'stat', 'status': self.status, '30': self.info}[name]

    def test_bind_and_kill_exact_noninherited_handle_without_pid_signal(self):
        self.native.bind_stopped(10)
        self.assertEqual(self.native.handles, {10: {'fd': 30, 'starttime': 123}})
        self.native.kill_bound(10)
        self.mocks[3].assert_called_once_with(10, 0)
        self.mocks[5].assert_called_once_with(30, 9, None, 0)
        self.assertEqual(self.native.observations[-1]['bound_kill']['result'], 0)
        self.native.close_handles()
        self.mocks[1].assert_any_call(30)
        self.assertFalse(self.native.handles)

    def test_no_consumed_stop_observer_terminal_or_bool_never_opens(self):
        for pid in (9, 11, True, -1):
            with self.assertRaises(ValueError):
                self.native.bind_stopped(pid)
        self.native.wait_terminals[10] = 9
        with self.assertRaises(ValueError):
            self.native.bind_stopped(10)
        self.mocks[0].assert_not_called()
        self.mocks[3].assert_not_called()

    def test_foreign_tracer_uid_or_thread_never_opens_pidfd(self):
        for wrong in (self.status.replace(b'TracerPid:\t9', b'TracerPid:\t8'),
                      self.status.replace(b'1000 1000 1000 1000', b'1001 1001 1001 1001'),
                      self.status.replace(b'Tgid:\t10', b'Tgid:\t11'),
                      self.status + b'Pid:\t10\n'):
            self.status = wrong
            with self.assertRaises(ValueError):
                self.native.bind_stopped(10)
        self.mocks[3].assert_not_called()

    def test_pidfd_mismatch_inherited_or_changed_identity_closes_unpublished_fd(self):
        for bad_info in (b'Pid:\t-1\n', b'Pid:\t11\n', b'Pid:\t10\nPid:\t10\n'):
            self.info = bad_info
            with self.assertRaises(ValueError):
                self.native.bind_stopped(10)
            self.assertFalse(self.native.handles)
            self.mocks[1].assert_any_call(30)
        self.info = b'Pid:\t10\n'
        self.mocks[4].return_value = True
        with self.assertRaises(ValueError):
            self.native.bind_stopped(10)
        self.mocks[4].return_value = False
        identities = iter([self.identity.copy(), {**self.identity, 'starttime': 124}])
        self.native.resources.proc_identity = lambda raw: next(identities)
        with self.assertRaises(ValueError):
            self.native.bind_stopped(10)
        self.assertFalse(self.native.handles)

    def test_never_rebind_lifetime_or_kill_unbound_reaped_identity(self):
        self.native.handles[10] = {'fd': 30, 'starttime': 122}
        with self.assertRaises(ValueError):
            self.native.bind_stopped(10)
        with self.assertRaises(KeyError):
            self.native.kill_bound(11)
        self.native.wait_terminals[10] = 9
        with self.assertRaises(ValueError):
            self.native.kill_bound(10)
        self.mocks[3].assert_not_called()
        self.mocks[5].assert_not_called()

    def test_failed_send_records_errno_not_success(self):
        self.native.handles[10] = {'fd': 30, 'starttime': 123}
        self.mocks[5].side_effect = ProcessLookupError(errno.ESRCH, 'gone')
        with self.assertRaises(ProcessLookupError):
            self.native.kill_bound(10)
        row = self.native.observations[-1]['bound_kill']
        self.assertEqual(row['errno'], errno.ESRCH)
        self.assertNotIn('result', row)
        self.assertFalse(self.native.wait_terminals)

    def test_close_failure_drains_registry_and_never_retries_uncertain_fd(self):
        self.native.handles = {10: {'fd': 30, 'starttime': 123},
                               11: {'fd': 31, 'starttime': 124},
                               12: {'fd': 32, 'starttime': 125}}
        self.mocks[1].side_effect = [None, OSError(errno.EINTR, 'uncertain'), None]
        with self.assertRaises(OSError):
            self.native.close_handles()
        self.assertEqual([call.args for call in self.mocks[1].call_args_list],
                         [(30,), (31,), (32,)])
        self.assertFalse(self.native.handles)
        self.native.close_handles()
        self.assertEqual(self.mocks[1].call_count, 3)
        self.assertEqual(self.native.observations[1]['handle_close']['errno'], errno.EINTR)

    def test_wait_consumption_and_successful_resume_maintain_stop_authority(self):
        self.native.wait_stops.clear()
        with patch('scripts.rust_semantic_ptrace.os.waitpid', return_value=(10, 19 << 8 | 127)) as wait:
            self.native.wait()
            wait.assert_called_once_with(-1, 1 | 0x40000000)
        self.assertEqual(self.native.wait_stops, {10})
        self.native.ptrace = lambda *args: 0
        self.native.resume(10, 0)
        self.assertFalse(self.native.wait_stops)
        with patch('scripts.rust_semantic_ptrace.os.waitpid', return_value=(10, 9)):
            self.native.wait()
        self.assertEqual(self.native.wait_terminals, {10: 9})


if __name__ == '__main__':
    unittest.main()
