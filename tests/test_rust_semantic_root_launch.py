"""Preparation-only tests: all process/signal APIs mocked; never real fork."""
import ast
import ctypes
import errno
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.rust_semantic_control_code import ROOT_SOURCE
from scripts.rust_semantic_control_budget import ControlBudget
from scripts.rust_semantic_handoff_receive import HandoffSender, FRAME, ACK_MAGIC, CREDS
from scripts.rust_semantic_root_launch import (PYTHON, RootLauncher,
                                              ROOT_PERMIT, RootLaunchGate,
                                              arm_parent_death, prepare_root_argv)


class RootLaunchTests(unittest.TestCase):
    def setUp(self):
        platform = patch('scripts.rust_semantic_root_launch.sys.platform', 'linux')
        platform.start()
        self.addCleanup(platform.stop)
        self.os = SimpleNamespace(getpid=Mock(return_value=10), getppid=Mock(return_value=10), getsid=Mock(return_value=10),
            getpgrp=Mock(return_value=10), getcwd=Mock(return_value='/owned/control'),
            fork=Mock(return_value=11), pidfd_open=Mock(return_value=30),
            get_inheritable=Mock(return_value=False), set_inheritable=Mock(),
            execve=Mock(), _exit=Mock(side_effect=SystemExit))
        self.signal = SimpleNamespace(SIGCHLD=17, SIG_DFL=0, signal=Mock())
        self.contract = {'observer': 10, 'threads': 1, 'sigchld_default': True,
                         'sa_no_cldwait': False, 'sole_waiter': True}
        self.verify = Mock(side_effect=lambda: dict(self.contract))
        self.env = {'PATH': '/usr/bin:/bin', 'HOME': '/owned/home', 'LC_ALL': 'C'}
        def prctl(option, arg, *unused):
            if option == 2:
                ctypes.cast(arg, ctypes.POINTER(ctypes.c_int))[0] = 9
            return 0
        self.libc = SimpleNamespace(prctl=Mock(side_effect=prctl))
        self.launcher = RootLauncher(self.os, self.signal, self.libc)
        self.source = ROOT_SOURCE.encode()
        self.gate = Mock(spec=RootLaunchGate)
        self.gate.creator = 10
        self.gate.os = self.os
        self.gate.owner = Mock()
        self.gate.budget = ControlBudget(0.)
        self.gate.clock = Mock(return_value=1.)
        self.handoff = Mock()

    def launch(self):
        return self.launcher.launch(self.source, 40, self.env, self.verify,
                                   gate=self.gate, handoff=self.handoff)

    def test_fixed_isolated_entry_syntax_and_receipt_without_evaluation(self):
        argv, receipt = prepare_root_argv(self.source, 40, 10)
        self.assertEqual(argv[:4], [PYTHON, '-I', '-S', '-c'])
        parsed = ast.parse(argv[4])
        self.assertEqual(parsed.body[-1].value.func.id, 'fixed_root')
        self.assertEqual(receipt['true_fd'], 40)
        self.assertEqual(receipt['expected_parent'], 10)
        self.assertEqual(receipt['entry_bytes'], len(argv[4].encode()))
        self.assertEqual(len(receipt['root_source_sha256']), 64)
        for source, fd in ((b'', 40), (b'x'*16385, 40), (b'\xff', 40),
                           (self.source, True), (self.source, 2)):
            with self.subTest(fd=fd), self.assertRaises((ValueError, UnicodeError)):
                prepare_root_argv(source, fd, 10)
        for parent in (True, 0, 1, 2**31, None):
            with self.subTest(parent=parent), self.assertRaises(ValueError):
                prepare_root_argv(self.source, 40, parent)

    def test_parent_death_guard_failures_prevent_child_exec(self):
        for failure in ('set', 'get', 'value', 'parent'):
            with self.subTest(failure=failure):
                self.setUp()
                self.os.fork.return_value = 0
                if failure == 'parent':
                    self.os.getppid.return_value = 12
                else:
                    def broken(option, arg, *unused):
                        if option == 2 and failure != 'value':
                            ctypes.cast(arg, ctypes.POINTER(ctypes.c_int))[0] = 9
                        return -1 if option == (1 if failure == 'set' else 2) and failure != 'value' else 0
                    self.libc.prctl.side_effect = broken
                with self.assertRaises(SystemExit):
                    self.launch()
                self.os.execve.assert_not_called()
                self.os.set_inheritable.assert_not_called()
                self.os._exit.assert_called_once_with(125)

    def test_guard_uses_explicit_native_widths_and_readback_before_parent_check(self):
        arm_parent_death(self.libc, self.os, 10)
        self.assertEqual(self.libc.prctl.argtypes, [ctypes.c_int] + [ctypes.c_ulong] * 4)
        self.assertEqual([c.args[0] for c in self.libc.prctl.call_args_list], [1, 2])
        self.os.getppid.assert_called_once_with()
        self.os.execve.assert_not_called()

    def test_owned_fresh_child_pidfd_and_one_attempt(self):
        self.assertEqual(self.launch(), (11, 30))
        self.os.pidfd_open.assert_called_once_with(11, 0)
        self.gate.prepare.assert_called_once_with()
        self.gate.release_parent.assert_called_once_with(11, 30, self.handoff)
        self.signal.signal.assert_called_once_with(17, 0)
        self.verify.assert_called_once_with()
        self.os.execve.assert_not_called()
        self.assertEqual(self.launcher.report()['receipt']['wait_contract'], self.contract)
        self.assertFalse(self.launcher.report()['qualified'])
        with self.assertRaises(RuntimeError):
            self.launch()
        self.os.fork.assert_called_once_with()

    def test_missing_gate_or_handoff_fails_before_fork(self):
        with self.assertRaises(ValueError):
            self.launcher.launch(self.source, 40, self.env, self.verify)
        self.os.fork.assert_not_called()

    def test_handoff_failure_keeps_exact_root_and_closes_permit(self):
        self.gate.release_parent.side_effect = ValueError('mock lost ACK')
        with self.assertRaises(ValueError):
            self.launch()
        self.assertEqual(self.launcher.root, 11)
        self.assertEqual(self.launcher.pidfd, 30)
        self.gate.owner.close_all.assert_called_once_with()
        self.os.execve.assert_not_called()

    def test_child_gate_timeout_never_enters_exec(self):
        self.os.fork.return_value = 0
        self.gate.await_child.side_effect = TimeoutError('mock late close')
        with self.assertRaises(SystemExit): self.launch()
        self.os.set_inheritable.assert_not_called()
        self.os.execve.assert_not_called()
        self.os._exit.assert_called_once_with(125)

    def test_missing_or_invalid_wait_proof_fails_before_fork(self):
        with self.assertRaises(ValueError):
            self.launcher.launch(self.source, 40, self.env)
        self.os.fork.assert_not_called()
        for key, value in (('threads', 2), ('threads', True), ('observer', 12),
                           ('sigchld_default', False), ('sa_no_cldwait', True),
                           ('sole_waiter', False), ('sole_waiter', 1)):
            with self.subTest(key=key, value=value):
                self.setUp()
                self.contract[key] = value
                with self.assertRaises(ValueError):
                    self.launch()
                self.os.fork.assert_not_called()

    def test_bad_environment_session_or_capability_fails_before_fork(self):
        for failure in ('env', 'session', 'api'):
            with self.subTest(failure=failure):
                self.setUp()
                if failure == 'env':
                    self.env['RUSTFLAGS'] = 'foreign'
                elif failure == 'session':
                    self.os.getsid.return_value = 9
                else:
                    self.os.pidfd_open = None
                with self.assertRaises(ValueError):
                    self.launch()
                self.os.fork.assert_not_called()

    def test_child_exec_failure_cannot_continue_observer_logic(self):
        self.os.fork.return_value = 0
        self.os.execve.side_effect = OSError(errno.ENOENT, 'mock')
        with self.assertRaises(SystemExit):
            self.launch()
        self.os.set_inheritable.assert_called_once_with(40, True)
        argv = self.os.execve.call_args.args
        self.assertEqual(argv[0], PYTHON)
        self.assertEqual(argv[1][:4], [PYTHON, '-I', '-S', '-c'])
        self.assertEqual(argv[2], self.env)
        self.os._exit.assert_called_once_with(125)
        self.os.pidfd_open.assert_not_called()

    def test_postfork_failures_preserve_owned_identity_for_outer_cleanup(self):
        for failure in ('open', 'flags'):
            with self.subTest(failure=failure):
                self.setUp()
                if failure == 'open':
                    self.os.pidfd_open.side_effect = OSError(errno.ESRCH, 'mock')
                else:
                    self.os.get_inheritable.return_value = True
                with self.assertRaises((OSError, ValueError)):
                    self.launch()
                report = self.launcher.report()
                self.assertEqual(report['root'], 11)
                self.assertEqual(report['owned_bootstrap_pidfd'], None if failure == 'open' else 30)
                self.assertFalse(report['outer_cleanup_complete'])
                self.assertEqual(len(report['errors']), 1)
                self.os.fork.assert_called_once_with()


class RootGateTests(unittest.TestCase):
    def gate(self):
        self.now = 1.
        self.info = {fd: SimpleNamespace(st_dev=1, st_ino=50, st_uid=1000,
                                        st_mode=stat.S_IFIFO) for fd in (50, 51)}
        self.os = SimpleNamespace(O_RDONLY=0, O_WRONLY=1, O_ACCMODE=3,
            O_NONBLOCK=2048, O_CLOEXEC=524288, getpid=Mock(return_value=10),
            getppid=Mock(return_value=10), getuid=Mock(return_value=1000),
            pipe2=Mock(return_value=(50, 51)), close=Mock(),
            fstat=Mock(side_effect=lambda fd: self.info[fd]),
            get_inheritable=Mock(return_value=False), write=Mock(return_value=8),
            read=Mock(side_effect=[ROOT_PERMIT, b'']))
        flags = SimpleNamespace(F_GETFL=3,
            fcntl=Mock(side_effect=lambda fd, op: 2048 + (fd == 51)))
        self.poller = Mock()
        self.budget = ControlBudget(0.)
        with patch('scripts.rust_semantic_owned_journal.sys.platform', 'linux'), \
             patch('scripts.rust_semantic_root_launch.sys.platform', 'linux'):
            gate = RootLaunchGate(self.budget, os_api=self.os, fcntl_api=flags,
                clock=lambda: self.now, poll_factory=lambda: self.poller)
        gate.prepare()
        return gate

    def test_parent_waits_for_exact_ack_before_atomic_permit(self):
        gate = self.gate()
        def ack(root, pidfd):
            self.os.write.assert_not_called()
            self.assertEqual([c.args[0] for c in self.os.close.call_args_list], [50])
            return {'root': root, 'pidfd': pidfd, 'recovery_held': True}
        gate.release_parent(11, 30, ack)
        self.os.write.assert_called_once_with(51, ROOT_PERMIT)
        self.assertEqual(self.os.close.call_count, 2)
        self.assertTrue(gate.report()['permit_released'])
        self.assertFalse(gate.report()['harness_authenticated'])
        with self.assertRaises(RuntimeError): gate.release_parent(11, 30, ack)
        self.os.write.assert_called_once()

    def test_missing_wrong_or_late_ack_never_writes(self):
        for result in (None, True, {'root': 12, 'pidfd': 30, 'recovery_held': True},
                       {'root': 11, 'pidfd': 30, 'recovery_held': 1}, 'late'):
            with self.subTest(result=result):
                gate = self.gate()
                def ack(root, pidfd):
                    if result == 'late':
                        self.now = 20.
                        return {'root': root, 'pidfd': pidfd, 'recovery_held': True}
                    return result
                with self.assertRaises((ValueError, TimeoutError)):
                    gate.release_parent(11, 30, ack)
                self.os.write.assert_not_called()
                self.assertFalse(gate.released)
                self.assertEqual(self.os.close.call_count, 2)

    def test_transport_ack_cannot_be_used_as_root_gate_recovery_authority(self):
        gate = self.gate()
        self.info[20] = SimpleNamespace(st_dev=1, st_ino=20, st_uid=1000, st_mode=stat.S_IFSOCK)
        self.info[30] = SimpleNamespace(st_dev=1, st_ino=30, st_uid=1000, st_mode=stat.S_IFREG)
        endpoint = SimpleNamespace(fileno=Mock(return_value=20),
            getsockopt=Mock(side_effect=lambda level, option: {39: 1, 3: 5, 16: 1}[option]),
            sendmsg=Mock(return_value=FRAME.size), recvmsg=Mock(return_value=(
                FRAME.pack(ACK_MAGIC, 1, 11, 123), [(1, 2, CREDS.pack(9, 1000, 1000))], 0, None)))
        flags = SimpleNamespace(F_GETFL=3, fcntl=Mock(return_value=2048))
        with patch('scripts.rust_semantic_handoff_receive.sys.platform', 'linux'):
            sender = HandoffSender(endpoint, {'device': 1, 'inode': 20, 'uid': 1000},
                {'pid': 9, 'uid': 1000, 'gid': 1000}, self.budget,
                os_api=self.os, fcntl_api=flags, clock=lambda: self.now)
        def transport(root, pidfd):
            sender.send_once(root, pidfd, 123)
            return sender.receive_ack_once()
        with self.assertRaises(ValueError): gate.release_parent(11, 30, transport)
        self.assertTrue(sender.report()['records'][-1]['transport_ack_matched'])
        self.os.write.assert_not_called()
        self.assertFalse(gate.released)
        self.assertEqual([c.args[0] for c in self.os.close.call_args_list], [50, 51])
        endpoint.sendmsg.assert_called_once()

    def test_child_needs_complete_permit_and_eof_before_return(self):
        gate = self.gate()
        self.os.getpid.return_value = 11
        self.os.read.side_effect = [BlockingIOError(), ROOT_PERMIT[:3],
                                    ROOT_PERMIT[3:], b'']
        gate.await_child()
        self.assertTrue(gate.released)
        self.poller.poll.assert_called_once_with(10)
        self.assertEqual([c.args[1] for c in self.os.read.call_args_list], [9, 9, 6, 1])
        self.assertEqual([c.args[0] for c in self.os.close.call_args_list], [51, 50])
        self.assertEqual(self.budget.report()['seen'], 8)

    def test_failed_child_role_never_reselects_and_retires_fork_copy(self):
        for fault in ('parent', 'clock'):
            with self.subTest(fault=fault):
                gate = self.gate()
                self.os.getpid.return_value = 11
                if fault == 'parent': self.os.getppid.return_value = 99
                else: self.now = 20.
                with self.assertRaises((ValueError, TimeoutError)):
                    gate.await_child()
                self.assertTrue(gate.report()['role_attempted'])
                self.assertTrue(gate.report()['await_attempted'])
                self.assertEqual(gate.owner.report()['owned_fds'], [])
                self.assertEqual([c.args[0] for c in self.os.close.call_args_list], [50, 51])
                self.os.getppid.return_value, self.now = 10, 1.
                with self.assertRaises(RuntimeError): gate.await_child()
                with self.assertRaises(RuntimeError): gate._select_role('child')
                self.os.read.assert_not_called()
                self.os.write.assert_not_called()
                self.assertEqual(self.os.close.call_count, 2)

    def test_successful_child_wait_cannot_consume_another_permit(self):
        gate = self.gate()
        self.os.getpid.return_value = 11
        gate.await_child()
        before = self.os.read.call_count
        with self.assertRaises(RuntimeError): gate.await_child()
        self.assertEqual(self.os.read.call_count, before)
        self.assertEqual(self.os.close.call_count, 2)

    def test_child_eof_truncation_extra_bytes_parent_loss_and_timeout_refused(self):
        for failure in ('eof', 'short', 'extra', 'parent', 'timeout', 'poll'):
            with self.subTest(failure=failure):
                gate = self.gate()
                self.os.getpid.return_value = 11
                if failure == 'eof': self.os.read.side_effect = [b'']
                if failure == 'short': self.os.read.side_effect = [b'ATROOT', b'']
                if failure == 'extra': self.os.read.side_effect = [ROOT_PERMIT + b'x']
                if failure == 'parent': self.os.getppid.side_effect = [10, 12]
                if failure == 'timeout': self.now = 20.
                if failure == 'poll': self.poller.register.side_effect = OSError('mock')
                with self.assertRaises((ValueError, TimeoutError, OSError)):
                    gate.await_child()
                self.assertFalse(gate.released)
                self.assertEqual(self.os.close.call_count, 2)

    def test_short_write_no_retry_and_close_failure_remains_incomplete(self):
        for failure in ('short', 'close'):
            gate = self.gate()
            if failure == 'short': self.os.write.return_value = 7
            else: self.os.close.side_effect = [None, OSError(errno.EINTR, 'uncertain')]
            ack = lambda r, f: {'root': r, 'pidfd': f, 'recovery_held': True}
            with self.assertRaises(ValueError): gate.release_parent(11, 30, ack)
            self.os.write.assert_called_once()
            self.assertEqual(self.budget.report()['seen'], 7 if failure == 'short' else 8)
            self.assertFalse(gate.report()['outer_cleanup_complete'])
            gate.owner.close_all()
            self.assertEqual(self.os.close.call_count, 2)

    def test_primary_errno_retained_separately_from_close_uncertainty(self):
        for failure in ('ack', 'write', 'read'):
            with self.subTest(failure=failure):
                gate = self.gate()
                error = OSError(errno.EIO, 'primary')
                self.os.close.side_effect = [None, OSError(errno.EINTR, 'close')]
                ack = Mock(return_value={'root': 11, 'pidfd': 30, 'recovery_held': True})
                if failure == 'ack': ack.side_effect = error
                if failure == 'write': self.os.write.side_effect = error
                if failure == 'read':
                    self.os.getpid.return_value = 11
                    self.os.read.side_effect = error
                with self.assertRaises(OSError) as caught:
                    if failure == 'read': gate.await_child()
                    else: gate.release_parent(11, 30, ack)
                self.assertIs(caught.exception, error)
                report = gate.report()
                self.assertEqual(report['errors'][0]['errno'], errno.EIO)
                self.assertEqual(report['pipe']['errors'][0]['errno'], errno.EINTR)
                self.assertEqual(report['write_attempted'], failure == 'write')
                gate.owner.close_all()
                self.assertEqual(self.os.close.call_count, 2)

    def test_late_close_cannot_return_success_in_either_role(self):
        for role in ('parent', 'child'):
            with self.subTest(role=role):
                gate = self.gate()
                def close(fd):
                    if self.os.close.call_count == 2: self.now = 20.
                self.os.close.side_effect = close
                with self.assertRaises(TimeoutError):
                    if role == 'child':
                        self.os.getpid.return_value = 11
                        gate.await_child()
                    else:
                        gate.release_parent(11, 30, lambda r, f:
                            {'root': r, 'pidfd': f, 'recovery_held': True})
                self.assertEqual(gate.report()['errors'][-1]['operation'],
                                 'post-retirement-clock')
                self.assertEqual(self.os.close.call_count, 2)


if __name__ == '__main__':
    unittest.main()
