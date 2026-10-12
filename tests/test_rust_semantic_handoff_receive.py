"""Mock-only: never create a socket, receive a native FD, or signal a process."""
import errno
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.rust_semantic_control_budget import ControlBudget
from scripts.rust_semantic_handoff_receive import HandoffPair, HandoffReceiver, HandoffSender, FRAME, MAGIC, ACK_MAGIC, CREDS, FD
from scripts import rust_semantic_linux_resources as resources


class HandoffReceiveTests(unittest.TestCase):
    def receiver(self, peer_pid=11):
        self.now = 1.
        self.os = SimpleNamespace(O_NONBLOCK=2048, getpid=Mock(return_value=10),
            getuid=Mock(return_value=1000), get_inheritable=Mock(return_value=False),
            fstat=Mock(side_effect=lambda fd: SimpleNamespace(st_dev=1,
                st_ino=50 if fd == 20 else fd, st_uid=1000,
                st_mode=stat.S_IFSOCK if fd == 20 else stat.S_IFREG)), close=Mock())
        self.flags = SimpleNamespace(F_GETFL=3, fcntl=Mock(return_value=2048))
        self.packet = (FRAME.pack(MAGIC, 1, 12, 123),
            [(1, 2, CREDS.pack(peer_pid, 1000, 1000)), (1, 1, FD.pack(30))], 0, None)
        self.endpoint = SimpleNamespace(fileno=Mock(return_value=20),
            getsockopt=Mock(side_effect=lambda level, option: {39: 1, 3: 5, 16: 1}[option]),
            recvmsg=Mock(side_effect=lambda *args: self.packet))
        self.budget = ControlBudget(0.)
        with patch('scripts.rust_semantic_handoff_receive.sys.platform', 'linux'):
            return HandoffReceiver(self.endpoint, {'device': 1, 'inode': 50, 'uid': 1000},
                {'pid': peer_pid, 'uid': 1000, 'gid': 1000}, self.budget,
                os_api=self.os, fcntl_api=self.flags, clock=lambda: self.now)

    def test_received_handle_is_retained_but_never_qualified_or_acked(self):
        receiver = self.receiver()
        message = receiver.receive_once()
        self.assertEqual(message['received_fd'], 30)
        self.assertEqual(message['starttime'], 123)
        self.assertEqual(receiver.report()['held_fds'], [30])
        self.assertFalse(receiver.report()['lifetimes_verified'])
        message['pid'] = 99
        self.assertEqual(receiver.report()['messages'][0]['pid'], 12)
        receiver.close_received()
        receiver.close_received()
        self.os.close.assert_called_once_with(30)

    def test_all_extra_handles_owned_before_invalid_credentials_or_truncation(self):
        for failure in ('extra', 'credential', 'truncation', 'raw'):
            receiver = self.receiver()
            raw, ancillary, flags, address = self.packet
            ancillary = list(ancillary)
            if failure == 'extra': ancillary[1] = (1, 1, FD.pack(30) + FD.pack(31))
            if failure == 'credential': ancillary[0] = (1, 2, CREDS.pack(99, 1000, 1000))
            if failure == 'truncation': flags = 8
            if failure == 'raw': raw = b'bad'
            self.packet = raw, ancillary, flags, address
            with self.assertRaises(ValueError): receiver.receive_once()
            self.assertEqual([c.args[0] for c in self.os.close.call_args_list],
                             [30, 31] if failure == 'extra' else [30])
            self.assertTrue(receiver.report()['failed'])
            with self.assertRaises(RuntimeError): receiver.receive_once()

    def test_would_block_preserves_shared_deadline_and_late_receive_closes_fd(self):
        receiver = self.receiver()
        self.endpoint.recvmsg.side_effect = BlockingIOError()
        self.assertIsNone(receiver.receive_once())
        self.assertEqual(receiver.report()['messages'], [])
        def late(*args):
            self.now = 20.
            return self.packet
        self.endpoint.recvmsg.side_effect = late
        with self.assertRaises(TimeoutError): receiver.receive_once()
        self.os.close.assert_called_once_with(30)

    def test_close_uncertainty_preserves_primary_and_never_retries(self):
        receiver = self.receiver()
        self.packet = b'bad', self.packet[1], 0, None
        self.os.close.side_effect = OSError(errno.EINTR, 'uncertain')
        with self.assertRaises(ValueError): receiver.receive_once()
        self.assertEqual(receiver.report()['errors'][0]['operation'], 'receive')
        self.assertEqual(receiver.report()['errors'][1]['errno'], errno.EINTR)
        receiver.close_received()
        self.os.close.assert_called_once_with(30)

    def test_failed_next_packet_preserves_prior_recovery_handle(self):
        receiver = self.receiver()
        receiver.receive_once()
        self.packet = (FRAME.pack(MAGIC, 2, 13, 124),
            [(1, 2, CREDS.pack(99, 1000, 1000)), (1, 1, FD.pack(31))], 0, None)
        with self.assertRaises(ValueError): receiver.receive_once()
        self.os.close.assert_called_once_with(31)
        self.assertEqual(receiver.report()['held_fds'], [30])
        receiver.close_received()
        self.assertEqual([c.args[0] for c in self.os.close.call_args_list], [31, 30])

    def test_duplicate_handle_and_output_overflow_keep_raw_evidence(self):
        receiver = self.receiver()
        receiver.receive_once()
        self.packet = (FRAME.pack(MAGIC, 2, 13, 124), self.packet[1], 0, None)
        with self.assertRaises(ValueError): receiver.receive_once()
        self.os.close.assert_not_called()
        self.assertEqual(receiver.report()['held_fds'], [30])
        receiver = self.receiver()
        self.budget.total_seen = self.budget.LIMIT
        with self.assertRaises(OverflowError): receiver.receive_once()
        self.assertEqual(receiver.report()['messages'][0]['raw'], self.packet[0])
        self.os.close.assert_called_once_with(30)

    def root_samples(self, peer_pid=11):
        receiver = self.receiver(peer_pid)
        receiver.receive_once()
        self.os.O_RDONLY, self.os.O_CLOEXEC = 0, 524288
        self.os.O_NOFOLLOW, self.os.O_DIRECTORY = 131072, 65536
        previous = self.os.fstat.side_effect
        self.os.fstat.side_effect = lambda fd: (SimpleNamespace(st_dev=1, st_ino=40,
            st_uid=1000, st_mode=stat.S_IFDIR) if fd == 40 else previous(fd))
        self.os.open = Mock(side_effect=[40, 50, 51, 52, 53])
        fields = ['S', '11', '11', '11'] + ['0'] * 18
        fields[19] = '123'
        raw_stat = ('12 (python) ' + ' '.join(fields)).encode()
        self.raws = [raw_stat, b'Pid:\t12\nTgid:\t12\nUid:\t1000 1000 1000 1000\nTracerPid:\t0\n',
                     b'pos:\t0\nPid:\t12\n', raw_stat]
        self.os.read = Mock(side_effect=lambda fd, limit: self.raws.pop(0))
        return receiver

    def test_root_binding_samples_held_lifetime_without_ack_or_cleanup_authority(self):
        receiver = self.root_samples()
        binding = receiver.bind_root(resources)
        self.assertEqual((binding['pid'], binding['ppid'], binding['starttime']), (12, 11, 123))
        self.assertEqual(receiver.report()['held_proc_fds'], [40])
        self.assertFalse(receiver.report()['lifetimes_verified'])
        self.assertEqual(self.os.open.call_args_list[1].kwargs, {'dir_fd': 40})
        self.assertEqual([c.args[0] for c in self.os.close.call_args_list], [50, 51, 52, 53])
        with self.assertRaises(RuntimeError): receiver.bind_root(resources)
        receiver.close_proc()
        receiver.close_proc()
        receiver.close_received()
        self.assertEqual([c.args[0] for c in self.os.close.call_args_list], [50, 51, 52, 53, 40, 30])

    def test_controller_forwarded_root_handle_is_not_observer_parent_proof(self):
        # The packet really came from controller22, but root12 was created by
        # observer11 in session/group11. Valid transport is not parent binding.
        receiver = self.root_samples(peer_pid=22)
        self.assertEqual(receiver.report()['messages'][0]['kernel_credentials']['pid'], 22)
        with self.assertRaises(ValueError): receiver.bind_root(resources)
        self.assertTrue(receiver.report()['failed'])
        self.assertFalse(receiver.report()['lifetimes_verified'])
        self.assertEqual(receiver.report()['held_fds'], [30])
        self.assertEqual(receiver.report()['held_proc_fds'], [])
        with self.assertRaises(RuntimeError): receiver.bind_root(resources)

    def test_root_binding_rejects_false_pidfd_reuse_parent_credentials_or_lateness(self):
        for fault in ('ordinary', 'pid', 'parent', 'start', 'uid', 'tracer', 'late'):
            receiver = self.root_samples()
            if fault == 'ordinary': self.raws[2] = b'pos:\t0\nflags:\t0\n'
            if fault == 'pid': self.raws[2] = b'Pid:\t99\n'
            if fault == 'parent': self.raws[3] = self.raws[3].replace(b'S 11', b'S 99', 1)
            if fault == 'start': self.raws[3] = self.raws[3].replace(b'123', b'124')
            if fault == 'uid': self.raws[1] = self.raws[1].replace(b'1000', b'1001', 1)
            if fault == 'tracer': self.raws[1] = self.raws[1].replace(b'TracerPid:\t0', b'TracerPid:\t11')
            if fault == 'late':
                def delayed(fd, limit):
                    raw = self.raws.pop(0)
                    if not self.raws: self.now = 1.6
                    return raw
                self.os.read.side_effect = delayed
            with self.assertRaises(ValueError): receiver.bind_root(resources)
            self.assertTrue(receiver.report()['failed'])
            self.assertEqual(receiver.report()['held_fds'], [30])
            self.assertEqual(receiver.report()['held_proc_fds'], [])
            self.assertEqual(self.os.close.call_args_list[-1].args, (40,))
            with self.assertRaises(RuntimeError): receiver.bind_root(resources)

    def test_root_binding_retains_overflow_raw_and_primary_read_error(self):
        receiver = self.root_samples()
        self.budget.total_seen = self.budget.LIMIT
        with self.assertRaises(OverflowError): receiver.bind_root(resources)
        self.assertIn('stat_before', receiver.report()['messages'][0]['root_binding_attempt'])
        receiver = self.root_samples()
        primary = OSError(errno.EIO, 'read')
        self.os.read.side_effect = primary
        self.os.close.side_effect = OSError(errno.EINTR, 'close')
        with self.assertRaises(OSError) as raised: receiver.bind_root(resources)
        self.assertIs(raised.exception, primary)
        self.assertEqual([c.args[0] for c in self.os.close.call_args_list], [50, 40])
        self.assertEqual(receiver.report()['held_fds'], [30])


class HandoffSendTests(unittest.TestCase):
    receiver = HandoffReceiveTests.receiver  # reuse fixture, not duplicate inherited tests
    def sender(self):
        receiver = self.receiver()
        self.endpoint.sendmsg = Mock(return_value=FRAME.size)
        self.endpoint.recvmsg.side_effect = lambda *args: self.ack
        self.ack = FRAME.pack(ACK_MAGIC, 1, 12, 123), [(1, 2, CREDS.pack(11, 1000, 1000))], 0, None
        with patch('scripts.rust_semantic_handoff_receive.sys.platform', 'linux'):
            return HandoffSender(self.endpoint, receiver.expected, receiver.peer, self.budget,
                os_api=self.os, fcntl_api=self.flags, clock=lambda: self.now)

    def test_matching_ack_is_transport_only_not_gate_authority(self):
        sender = self.sender()
        sender.send_once(12, 30, 123)
        self.endpoint.sendmsg.assert_called_once_with([FRAME.pack(MAGIC, 1, 12, 123)],
            [(1, 1, FD.pack(30))], 0x40 | 0x4000)
        outcome = sender.receive_ack_once()
        self.assertTrue(outcome['transport_ack_matched'])
        self.assertFalse(outcome['harness_authenticated'])
        self.assertNotIn('recovery_held', outcome)
        self.os.close.assert_not_called()  # never close borrowed root descriptor
        with self.assertRaises(RuntimeError): sender.send_once(12, 30, 123)
        with self.assertRaises(RuntimeError): sender.receive_ack_once()

    def test_send_uncertain_short_or_late_fails_without_retry(self):
        for fault in ('short', 'uncertain', 'late'):
            sender = self.sender()
            if fault == 'short': self.endpoint.sendmsg.return_value = 7
            if fault == 'uncertain': self.endpoint.sendmsg.side_effect = OSError(errno.EINTR, 'send')
            if fault == 'late':
                def late(*args): self.now = 20.; return FRAME.size
                self.endpoint.sendmsg.side_effect = late
            with self.assertRaises((ValueError, OSError, TimeoutError)): sender.send_once(12, 30, 123)
            self.endpoint.sendmsg.assert_called_once()
            self.os.close.assert_not_called()
            with self.assertRaises(RuntimeError): sender.send_once(12, 30, 123)

    def test_wrong_extra_truncated_or_late_ack_retains_raw_and_retires_extra(self):
        for fault in ('credential', 'lifetime', 'extra', 'truncated', 'late', 'overflow'):
            sender = self.sender()
            sender.send_once(12, 30, 123)
            raw, ancillary, flags, address = self.ack
            if fault == 'credential': ancillary = [(1, 2, CREDS.pack(99, 1000, 1000))]
            if fault == 'lifetime': raw = FRAME.pack(ACK_MAGIC, 1, 12, 124)
            if fault == 'extra': ancillary = ancillary + [(1, 1, FD.pack(31) + FD.pack(32))]
            if fault == 'truncated': flags = 8
            self.ack = raw, ancillary, flags, address
            if fault == 'late':
                def late(*args): self.now = 20.; return self.ack
                self.endpoint.recvmsg.side_effect = late
            if fault == 'overflow': self.budget.total_seen = self.budget.LIMIT
            with self.assertRaises((ValueError, TimeoutError, OverflowError)): sender.receive_ack_once()
            self.assertEqual(sender.report()['records'][-1]['raw'], raw)
            self.assertEqual([c.args[0] for c in self.os.close.call_args_list],
                             [31, 32] if fault == 'extra' else [])
            self.assertTrue(sender.report()['failed'])

    def test_ack_would_block_preserves_pending_and_shared_budget(self):
        sender = self.sender()
        sender.send_once(12, 30, 123)
        self.endpoint.recvmsg.side_effect = BlockingIOError()
        self.assertIsNone(sender.receive_ack_once())
        self.assertEqual(sender.pending, (12, 30, 123))
        self.now = 20.
        with self.assertRaises(TimeoutError): sender.receive_ack_once()


class HandoffPairTests(unittest.TestCase):
    def owner(self):
        self.now = 1.
        self.os = SimpleNamespace(O_NONBLOCK=2048, getpid=Mock(return_value=10),
            getppid=Mock(return_value=10),
            getuid=Mock(return_value=1000), get_inheritable=Mock(return_value=False),
            fstat=Mock(side_effect=lambda fd: SimpleNamespace(st_dev=1, st_ino=fd,
                st_uid=1000, st_mode=stat.S_IFSOCK)))
        self.flags = SimpleNamespace(F_GETFL=3, fcntl=Mock(return_value=2048))
        self.pair = tuple(SimpleNamespace(fileno=Mock(return_value=fd), close=Mock(),
            detach=Mock(return_value=fd), setsockopt=Mock(), getsockopt=Mock(side_effect=lambda level, option:
                {39: 1, 3: 5, 16: 1}[option])) for fd in (20, 21))
        self.socket = SimpleNamespace(socketpair=Mock(return_value=self.pair))
        self.budget = ControlBudget(0.)
        with patch('scripts.rust_semantic_handoff_receive.sys.platform', 'linux'):
            return HandoffPair(self.budget, socket_api=self.socket, os_api=self.os,
                               fcntl_api=self.flags, clock=lambda: self.now)

    def test_created_pair_is_owned_and_receiver_uses_same_budget(self):
        owner = self.owner()
        self.assertEqual(owner.allocate(), self.pair)
        self.socket.socketpair.assert_called_once_with(1, 5 | 0x80000 | 0x800, 0)
        for endpoint in self.pair:
            endpoint.setsockopt.assert_called_once_with(1, 16, 1)
        owner.select_role('receiver')
        with patch('scripts.rust_semantic_handoff_receive.sys.platform', 'linux'):
            receiver = owner.receiver({'pid': 11, 'uid': 1000, 'gid': 1000})
        self.assertIs(receiver.budget, self.budget)
        self.assertIs(receiver.endpoint, self.pair[0])
        self.assertFalse(owner.report()['harness_authenticated'])
        owner.close_all()
        owner.close_all()
        for endpoint in self.pair: endpoint.close.assert_called_once()
        with self.assertRaises(RuntimeError): owner.allocate()

    def test_invalid_or_late_setup_retires_both_without_retry(self):
        for fault in ('credential', 'nonblocking', 'late'):
            owner = self.owner()
            if fault == 'credential': self.pair[0].setsockopt.side_effect = OSError(errno.EIO, 'setup')
            if fault == 'nonblocking': self.flags.fcntl.return_value = 0
            if fault == 'late':
                def late(*args): self.now = 20.
                self.pair[1].setsockopt.side_effect = late
            with self.assertRaises((ValueError, OSError, TimeoutError)): owner.allocate()
            self.assertFalse(owner.report()['ready'])
            self.assertEqual(owner.report()['owned_fds'], [])
            for endpoint in self.pair: endpoint.close.assert_called_once()

    def test_setup_primary_survives_close_uncertainty_and_foreign_fd_refused(self):
        owner = self.owner()
        primary = OSError(errno.EIO, 'setup')
        self.pair[0].setsockopt.side_effect = primary
        self.pair[0].close.side_effect = OSError(errno.EINTR, 'close')
        with self.assertRaises(OSError) as raised: owner.allocate()
        self.assertIs(raised.exception, primary)
        self.assertEqual(owner.report()['errors'][1]['errno'], errno.EINTR)
        owner.close_all()
        self.pair[0].close.assert_called_once()
        owner = self.owner()
        owner.allocate()
        self.pair[0].fileno.return_value = 99
        owner.close_all()
        self.pair[0].close.assert_not_called()
        self.pair[0].detach.assert_called_once()
        self.pair[1].close.assert_called_once()

    def test_replaced_inode_detaches_wrapper_without_foreign_close(self):
        owner = self.owner()
        owner.allocate()
        previous = self.os.fstat.side_effect
        self.os.fstat.side_effect = lambda fd: (SimpleNamespace(st_dev=1, st_ino=99,
            st_uid=1000, st_mode=stat.S_IFSOCK) if fd == 20 else previous(fd))
        owner.close_all()
        self.pair[0].detach.assert_called_once()
        self.pair[0].close.assert_not_called()
        self.pair[1].close.assert_called_once()
        self.assertEqual(owner.report()['errors'][0]['operation'], 'foreign-detach')

    def test_final_borrow_validation_still_inside_allocation_half_second(self):
        owner = self.owner()
        calls = 0
        previous = self.pair[1].getsockopt.side_effect
        def delayed(level, option):
            nonlocal calls
            calls += 1
            if calls == 6: self.now = 1.6  # second (borrow) validation's final sample
            return previous(level, option)
        self.pair[1].getsockopt.side_effect = delayed
        with self.assertRaises(ValueError): owner.allocate()
        self.assertFalse(owner.ready)
        for endpoint in self.pair: endpoint.close.assert_called_once()

    def test_unverifiable_endpoint_also_disarms_wrapper(self):
        owner = self.owner()
        owner.allocate()
        previous = self.os.fstat.side_effect
        def unreadable(fd):
            if fd == 20: raise OSError(errno.EBADF, 'closed or replaced')
            return previous(fd)
        self.os.fstat.side_effect = unreadable
        owner.close_all()
        self.pair[0].detach.assert_called_once()
        self.pair[0].close.assert_not_called()
        self.pair[1].close.assert_called_once()
        self.assertEqual(owner.report()['errors'][0]['operation'], 'unverified-detach')

    def test_fork_copy_selects_sender_once_and_retires_unused_receiver(self):
        owner = self.owner()
        owner.allocate()
        self.os.getpid.return_value = 11
        self.assertIs(owner.select_role('sender', expected_parent=10), self.pair[1])
        self.pair[0].close.assert_called_once()
        self.pair[1].close.assert_not_called()
        with patch('scripts.rust_semantic_handoff_receive.sys.platform', 'linux'):
            sender = owner.sender({'pid': 10, 'uid': 1000, 'gid': 1000})
        self.assertIs(sender.endpoint, self.pair[1])
        self.assertIs(sender.budget, self.budget)  # a fork copy still NOT global ledger proof
        with self.assertRaises(RuntimeError): owner.select_role('sender', expected_parent=10)
        with self.assertRaises(ValueError): owner.receiver({'pid': 12, 'uid': 1000, 'gid': 1000})
        owner.close_all()
        self.pair[1].close.assert_called_once()

    def test_wrong_parent_or_uncertain_role_retirement_cannot_borrow(self):
        owner = self.owner()
        owner.allocate()
        self.os.getpid.return_value = 11
        self.os.getppid.return_value = 99
        with self.assertRaises(ValueError): owner.select_role('sender', expected_parent=10)
        for endpoint in self.pair: endpoint.close.assert_not_called()
        self.os.getppid.return_value = 10
        with self.assertRaises(RuntimeError): owner.select_role('sender', expected_parent=10)
        owner.close_all()  # retire copied wrappers even though role admission failed
        for endpoint in self.pair: endpoint.close.assert_called_once()
        owner = self.owner()
        owner.allocate()
        self.os.getpid.return_value = 11
        self.pair[0].close.side_effect = OSError(errno.EINTR, 'close')
        with self.assertRaises(ValueError): owner.select_role('sender', expected_parent=10)
        with self.assertRaises(ValueError): owner.sender({'pid': 10, 'uid': 1000, 'gid': 1000})
        self.assertEqual(owner.report()['owned_fds'], [21])
        owner.close_all()
        self.pair[0].close.assert_called_once()
        self.pair[1].close.assert_called_once()

    def test_late_role_selection_latches_before_parent_check(self):
        owner = self.owner()
        owner.allocate()
        self.now = 20.
        with self.assertRaises(TimeoutError): owner.select_role('receiver')
        self.assertTrue(owner.report()['role_attempted'])
        self.assertFalse(owner.ready)
        with self.assertRaises(RuntimeError): owner.select_role('receiver')
        owner.close_all()
        for endpoint in self.pair: endpoint.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
