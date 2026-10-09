"""Whole-entry ledger/AST tests only; no native adapter/run/IPC construction."""
import ast
import copy
import hashlib
import json
import struct
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.rust_semantic_control_budget import ControlBudget
from scripts.rust_semantic_supervisor_entry import TreeLedger
from scripts import rust_semantic_supervisor_entry as entry


class WholeEntryTests(unittest.TestCase):
    def test_harness_original_clock_includes_preparation_before_native_identity(self):
        native = SimpleNamespace(getpid=Mock(return_value=90), getuid=Mock(return_value=1000),
                                 getgid=Mock(return_value=1000))
        with patch.object(entry, 'os', native), patch.object(entry.time, 'monotonic', return_value=15.):
            harness = entry.NativeHarness(original_started=1.)
            self.assertEqual(harness.budget.active_deadline, 21.)
        native.getpid.reset_mock()
        for started in (-1, True, 16., -float('inf'), float('nan')):
            with patch.object(entry, 'os', native), patch.object(entry.time, 'monotonic', return_value=15.):
                with self.assertRaises(ValueError): entry.NativeHarness(original_started=started)
        with patch.object(entry, 'os', native), patch.object(entry.time, 'monotonic', return_value=21.):
            with self.assertRaises(TimeoutError): entry.NativeHarness(original_started=1.)
        native.getpid.assert_not_called()

    def setUp(self):
        self.budget = ControlBudget(0)
        self.tree = TreeLedger(10, self.budget)
        self.identities = {}
        for role, pid, parent, session in (
                ('watchdog', 16, 10, 10),
                ('controller', 11, 10, 10), ('observer', 12, 11, 10),
                ('root', 13, 12, 12), ('true-path', 14, 13, 12), ('true-fd', 15, 13, 12)):
            identity = dict(pid=pid, ppid=parent, session=session, pgrp=session,
                            starttime=pid+100, state='S')
            self.identities[role] = identity

    def register(self, role):
        self.tree.register(role, self.identities[role], self.identities[role]['pid']+20)

    def bootstrap(self):
        self.register('watchdog'); self.register('controller'); self.register('observer')
        identity = dict(self.identities['observer'], session=12, pgrp=12, state='T')
        self.tree.confirm_session(identity)

    def all_roles(self):
        self.bootstrap()
        for role in ('root', 'true-path', 'true-fd'):
            self.register(role)
            self.tree.stop(role, self.identities[role]['starttime'], 4096)

    def consume(self, role, consumer, status=0):
        row = self.identities[role]
        self.tree.terminal(role, row['pid'], row['starttime'], status, consumer)

    def test_normal_whole_tree_barriers_retire_before_role_consumption(self):
        self.all_roles()
        for role in ('true-path', 'true-fd', 'root'): self.consume(role, 'observer')
        with self.assertRaises(ValueError): self.consume('observer', 'controller')
        self.tree.begin_cancel(); self.tree.retire(sent=True)
        self.consume('observer', 'controller'); self.consume('controller', 'harness')
        self.consume('watchdog', 'harness')
        report = self.tree.report()
        self.assertEqual(len(report['terminals']), 6)
        self.assertEqual(report['group_state'], 'retired-normal')
        self.assertFalse(report['qualified']); self.assertFalse(report['outer_cleanup_complete'])

    def test_controller_death_is_distinct_failure_recovery(self):
        self.bootstrap(); self.register('root')
        self.tree.begin_cancel()
        with self.assertRaises(ValueError): self.tree.retire(sent=True)
        self.tree.retire(sent=True, failed_recovery=True)
        with self.assertRaises(ValueError): self.consume('observer', 'harness', 9)
        self.consume('controller', 'harness', 9)
        self.consume('observer', 'harness', 9); self.consume('root', 'harness', 9)
        self.assertEqual(self.tree.group_state, 'abandoned-failed')
        self.assertTrue(self.tree.failed)
        with self.assertRaises(ValueError): self.tree.begin_cancel()
        with self.assertRaises(ValueError): self.register('true-path')

    def test_root_cannot_create_before_post_session_confirmation(self):
        self.register('controller'); self.register('observer')
        with self.assertRaises(ValueError): self.register('root')

    def test_same_observer_lifetime_and_true_parent_must_match(self):
        self.register('controller'); self.register('observer')
        identity = dict(self.identities['observer'], session=12, pgrp=12, state='T', starttime=999)
        with self.assertRaises(ValueError): self.tree.confirm_session(identity)
        self.tree.confirm_session(dict(identity, starttime=112))
        self.register('root')
        self.identities['true-path']['ppid'] = 11
        with self.assertRaises(ValueError): self.register('true-path')

    def test_zero_rss_old_starttime_duplicate_stop_all_refused(self):
        self.bootstrap(); self.register('root')
        for start, rss in ((113, 0), (999, 4096), (113, True)):
            with self.assertRaises(ValueError): self.tree.stop('root', start, rss)
        self.tree.stop('root', 113, 4096)
        with self.assertRaises(ValueError): self.tree.stop('root', 113, 4096)

    def test_uncertain_or_failed_cancel_never_retries_or_retires(self):
        self.all_roles(); self.tree.begin_cancel()
        with self.assertRaises(ValueError): self.tree.retire(sent=False, failed_recovery=True)
        with self.assertRaises(ValueError): self.tree.begin_cancel()
        self.assertEqual(self.tree.group_state, 'held')

    def test_pre_session_failure_never_invents_group_signal_authority(self):
        self.register('controller')
        self.tree.abandon_unacquired_group()
        self.consume('controller', 'harness', 9)
        self.assertEqual(self.tree.group_state, 'not-acquired-failed')
        with self.assertRaises(ValueError): self.tree.begin_cancel()
        self.setUp(); self.bootstrap()
        with self.assertRaises(ValueError): self.tree.abandon_unacquired_group()

    def test_watchdog_bootstrap_failure_is_direct_child_not_tracee_adoption(self):
        self.register('watchdog')
        with self.assertRaises(ValueError): self.consume('watchdog', 'harness', 9)
        self.tree.abandon_unacquired_group()
        self.consume('watchdog', 'harness', 9)
        self.assertFalse(self.tree.controller_consumed)
        self.assertFalse(self.tree.observer_consumed)
        with self.assertRaises(ValueError): self.consume('watchdog', 'harness', 9)

    def test_watchdog_uncertain_consumption_never_retries_or_cancels(self):
        self.register('watchdog'); self.tree.abandon_unacquired_group()
        harness = object.__new__(entry.NativeHarness)
        harness.ledger = self.tree
        harness.consume_attempts = {16: dict(attempted=True, result=None)}
        harness.wait_terminal = Mock()
        cancel = Mock()
        with patch.object(entry, 'signal', SimpleNamespace(pidfd_send_signal=cancel)):
            with self.assertRaises(RuntimeError): harness.recover_watchdog()
        cancel.assert_not_called(); harness.wait_terminal.assert_not_called()

    def test_watchdog_cancel_exception_latches_before_send_and_never_retries(self):
        self.register('watchdog'); self.tree.abandon_unacquired_group()
        harness = object.__new__(entry.NativeHarness)
        harness.ledger, harness.consume_attempts = self.tree, {}
        harness.watch_cancel_attempted, harness.watch_stopping = False, False
        harness.watch_channel, harness.watch_retire_attempted = None, False
        harness.check = Mock()
        harness.errors = []
        harness.wait_terminal = Mock(side_effect=TimeoutError('held terminal missing'))
        cancel = Mock(side_effect=OSError(5, 'uncertain'))
        with patch.object(entry, 'signal', SimpleNamespace(pidfd_send_signal=cancel)):
            with self.assertRaises(TimeoutError): harness.recover_watchdog()
            with self.assertRaises(TimeoutError): harness.recover_watchdog()
        cancel.assert_called_once_with(36, 9, None, 0)
        self.assertTrue(harness.watch_cancel_attempted)
        self.assertEqual(harness.errors, [dict(phase='watchdog-force-cancel', error='OSError', errno=5)])
        self.assertNotIn('watchdog', self.tree.terminals)

    def test_dual_hold_ack_is_required_before_returning_release_permission(self):
        harness = object.__new__(entry.NativeHarness)
        harness.watch_channel, harness.watchdog, harness.watch_stopping = object(), 16, False
        harness.watch_handoffs, harness.watch_held = {}, {}
        harness.send = Mock()
        harness.await_ack = Mock(side_effect=TimeoutError('no watchdog holder'))
        with self.assertRaises(TimeoutError):
            harness.dual_hold('controller', 31, self.identities['controller'])
        harness.await_ack.assert_called_once_with(harness.watch_channel, 16, 1)
        self.assertEqual(harness.watch_handoffs['controller']['identity'], self.identities['controller'])
        self.assertEqual(harness.watch_held, {})  # lost ACK never grants release

    def test_cleanup_notice_cannot_renew_first_deadline(self):
        harness = object.__new__(entry.NativeHarness)
        harness.budget, harness.cleanup_deadline = ControlBudget(0), None
        harness.watch_deadline_records = []
        harness.watch_channel, harness.watch_stopping, harness.send = object(), False, Mock()
        with patch.object(entry.time, 'monotonic', side_effect=[5, 5, 8, 8]):
            self.assertEqual(harness.enter_cleanup(), 15)
            self.assertEqual(harness.enter_cleanup(), 15)
        self.assertEqual(harness.budget.cleanup_started, 5)
        self.assertEqual(harness.send.call_args.args[1], dict(op='watch-cleanup', deadline=15))

    def test_dead_watchdog_refuses_active_operation_but_not_cleanup_clock(self):
        self.register('watchdog')
        harness = object.__new__(entry.NativeHarness)
        harness.ledger, harness.harness_pid, harness.watchdog = self.tree, 10, 16
        harness.budget, harness.cleanup_deadline = ControlBudget(0), None
        harness.watch_stopping, harness.watch_health_checking = False, False
        harness.output_failed, harness.errors = False, []
        harness.drain_outputs = Mock()
        harness.terminal = Mock(return_value=dict(si_pid=16, si_code=2, si_status=9))
        with patch.object(entry, 'os', SimpleNamespace(getpid=lambda: 10)), \
                patch.object(entry.time, 'monotonic', return_value=2):
            with self.assertRaises(RuntimeError): harness.check()
            self.assertTrue(harness.watch_stopping)
            with self.assertRaises(RuntimeError): harness.check()
            harness.cleanup_deadline = harness.budget.begin_cleanup(2)
            harness.check()
        harness.terminal.assert_called_once_with(16, 36)

    def test_last_group_cancel_failure_uses_adopted_parent_before_consume(self):
        self.bootstrap()
        harness = object.__new__(entry.NativeHarness)
        harness.ledger, harness.controller, harness.pid = self.tree, 11, 10
        harness.identity = Mock()
        kill = Mock()
        with patch.object(entry, 'os', SimpleNamespace(killpg=kill)), \
                patch.object(entry, 'signal', SimpleNamespace(SIGKILL=9)):
            harness.last_group_cancel(True)
        kill.assert_called_once_with(12, 9)
        harness.identity.assert_called_once_with(12, 32, parent=10, session=12)
        self.assertFalse(self.tree.controller_consumed)
        self.assertEqual(self.tree.group_state, 'abandoned-failed')

    def test_last_group_cancel_normal_uses_live_controller_parent(self):
        self.all_roles()
        harness = object.__new__(entry.NativeHarness)
        harness.ledger, harness.controller, harness.pid = self.tree, 11, 10
        harness.identity = Mock()
        kill = Mock()
        with patch.object(entry, 'os', SimpleNamespace(killpg=kill)), \
                patch.object(entry, 'signal', SimpleNamespace(SIGKILL=9)):
            harness.last_group_cancel(False)
        kill.assert_called_once_with(12, 9)
        harness.identity.assert_called_once_with(12, 32, parent=11, session=12)
        self.assertEqual(self.tree.group_state, 'retired-normal')

    def test_received_earlier_first_tightens_and_later_notice_cannot_extend(self):
        harness = object.__new__(entry.NativeHarness)
        harness.budget, harness.cleanup_deadline = ControlBudget(0), None
        harness.watch_deadline_records = []
        with patch.object(entry.time, 'monotonic', return_value=5):
            self.assertEqual(harness.adopt_cleanup(12), 12)
            self.assertEqual(harness.adopt_cleanup(18), 12)
            self.assertEqual(harness.adopt_cleanup(11), 11)
            for value in (float('nan'), float('inf'), True, '11'):
                with self.assertRaises(ValueError): harness.adopt_cleanup(value)
        with patch.object(entry.time, 'monotonic', return_value=12):
            with self.assertRaises(TimeoutError): harness.adopt_cleanup(20)
        self.assertEqual(harness.budget.cleanup_deadline, 11)

    def test_pump_saves_ack_once_and_reentry_cannot_read_again(self):
        harness = object.__new__(entry.NativeHarness)
        harness.watch_channel, harness.watchdog = object(), 16
        harness.watch_pumping, harness.watch_stopping, harness.watch_pending = False, False, []
        item = (dict(op='ack', sequence=1), [], 16)
        def receive(*args):
            harness.pump_watchdog()  # deliberate recursive callback under mock
            return item
        harness.receive = Mock(side_effect=receive)
        harness.pump_watchdog()
        harness.receive.assert_called_once_with(harness.watch_channel, 16)
        self.assertEqual(harness.watch_pending, [item])
        self.assertFalse(harness.watch_pumping)
        harness.watch_pending = [item]*8
        with self.assertRaises(ValueError): harness.pump_watchdog()
        self.assertEqual(len(harness.watch_pending), 8)

    def test_uncertain_fault_publication_does_not_retry_or_claim_complete(self):
        harness = object.__new__(entry.NativeHarness)
        harness.watch_publish_failures, harness.watch_evidence_complete = [], False
        endpoint = Mock()
        endpoint.sendmsg.side_effect = BlockingIOError(11, 'full')
        # Inject Linux's constant namespace even on Windows; no real socket or
        # platform signal operation is performed by this mock-only test.
        with patch.object(entry, 'socket', SimpleNamespace(MSG_DONTWAIT=64, MSG_NOSIGNAL=16384)):
            harness.publish_watch(endpoint, dict(op='watch-fault', deadline=12, error='fault'))
        endpoint.sendmsg.assert_called_once()
        self.assertEqual(endpoint.sendmsg.call_args.args[2], 64 | 16384)
        self.assertFalse(harness.watch_evidence_complete)
        self.assertEqual(harness.watch_publish_failures, [dict(op='watch-fault', error='BlockingIOError')])

    def cancel_stream(self):
        harness = object.__new__(entry.NativeHarness)
        harness.watch_held = {'controller': dict(identity=self.identities['controller'])}
        harness.watch_handoffs = {}
        harness.watch_raw, harness.watch_cancelled = [], set()
        harness.watch_cancel_end, harness.watch_evidence_complete = None, False
        record = dict(role='controller', pid=11,
                      starttime=self.identities['controller']['starttime'], signal=9, sent=True)
        packet = dict(op='watch-cancel', sequence=0, record=record)
        end = dict(op='watch-cancel-end', count=1, publish_failures=0,
            lifetimes=[dict(role='controller', pid=11, starttime=record['starttime'])],
            sha256=hashlib.sha256(json.dumps([record], sort_keys=True,
                separators=(',', ':')).encode('ascii')).hexdigest())
        return harness, packet, end

    def wire_watch_harness(self):
        harness = object.__new__(entry.NativeHarness)
        harness.budget, harness.cleanup_deadline = ControlBudget(0), None
        harness.harness_pid, harness.uid, harness.gid = 10, 1000, 1001
        harness.watch_channel = Mock(); harness.watch_channel.fileno.return_value = 50
        harness.watch_pumping, harness.watch_pending, harness.eof = False, [], set()
        harness.receive_raw, harness.raw, harness.watch_deadline_records = [], [], []
        harness.watch_fault_seen, harness.watch_stopping = False, False
        harness.watch_cleanup_confirmed = False
        harness.check = Mock()
        return harness

    def wire_packet(self, harness, packet):
        raw = json.dumps(packet).encode('ascii')
        # Entire Linux-only constant namespace injected, not patched onto the
        # host socket module; no socket creation/native credential operation.
        constants = SimpleNamespace(SOL_SOCKET=1, SCM_RIGHTS=1, SCM_CREDENTIALS=2,
            MSG_CMSG_CLOEXEC=0x40000000, MSG_DONTWAIT=64, MSG_TRUNC=32, MSG_CTRUNC=8)
        harness.watch_channel.recvmsg.return_value = (raw, [(1, 2,
            struct.pack('=iii', 16, 1000, 1001))], 0, '')
        with patch.object(entry, 'socket', constants):
            return harness.receive(harness.watch_channel, 16)

    def test_concurrent_first_notices_in_both_orders_require_actual_earliest_ack(self):
        for first_local in (True, False):
            with self.subTest(first_local=first_local):
                harness = self.wire_watch_harness()
                with patch.object(entry.os, 'getpid', return_value=10), \
                        patch.object(entry.time, 'monotonic', return_value=5):
                    if first_local: harness.adopt_cleanup(12)
                    with self.assertRaises(RuntimeError):
                        self.wire_packet(harness, dict(op='watch-fault', deadline=14, error='fault'))
                    if not first_local: harness.adopt_cleanup(12)
                    self.assertEqual(harness.cleanup_deadline, 12)
                    self.assertFalse(harness.watch_cleanup_confirmed)
                    self.wire_packet(harness, dict(op='watch-cleanup-applied', deadline=14))
                    self.assertEqual(harness.cleanup_deadline, 12)
                    self.assertFalse(harness.watch_cleanup_confirmed)  # stale ACK does not prove shared FIRST
                    self.wire_packet(harness, dict(op='watch-cleanup-applied', deadline=12))
                    self.assertTrue(harness.watch_cleanup_confirmed)
                    harness.adopt_cleanup(11)
                    self.assertFalse(harness.watch_cleanup_confirmed)  # earlier FIRST invalidates prior ACK
                    self.wire_packet(harness, dict(op='watch-cleanup-applied', deadline=11))
                    self.assertTrue(harness.watch_cleanup_confirmed)
                    self.assertTrue(harness.watch_stopping)
                    self.assertEqual(harness.budget.failure, 'watchdog-fault')

    def test_expired_or_invalid_fault_first_never_leaves_active_permission_unlocked(self):
        for deadline in (4, float('nan'), True):
            with self.subTest(deadline=deadline):
                harness = self.wire_watch_harness()
                with patch.object(entry.os, 'getpid', return_value=10), \
                        patch.object(entry.time, 'monotonic', return_value=5):
                    with self.assertRaises((ValueError, TimeoutError)):
                        self.wire_packet(harness, dict(op='watch-fault', deadline=deadline, error='fault'))
                    self.assertTrue(harness.watch_fault_seen)
                    self.assertTrue(harness.watch_stopping)
                    self.assertEqual(harness.budget.failure, 'watchdog-fault')

    def test_end_lifetime_float_or_bool_is_not_exact_integer_identity(self):
        for key, value in (('pid', 11.0), ('starttime', 111.0), ('pid', True)):
            harness, packet, end = self.cancel_stream()
            harness.accept_watch_cancel(packet)
            end['lifetimes'][0][key] = value
            with self.assertRaises(ValueError): harness.accept_watch_cancel(end)

    def test_watchdog_fault_then_earlier_parent_first_cancels_once_and_never_renews(self):
        class SimulatedExit(BaseException): pass
        harness = object.__new__(entry.NativeHarness)
        harness.libc, harness.budget, harness.cleanup_deadline = None, ControlBudget(0), None
        harness.output_reads, harness.output_writes = {}, {}
        harness.watch_held = {'controller': dict(fd=31, identity=self.identities['controller'])}
        harness.watch_cancelled, harness.watch_raw, harness.watch_publish_failures = set(), [], []
        harness._close_channel, harness.retire_fd = Mock(), Mock()
        harness.await_ack, harness.wait_policy, harness.send = Mock(), Mock(), Mock()
        harness.receive = Mock(side_effect=[ValueError('fault at4'),
            (dict(op='watch-cleanup', deadline=12), [], 10),
            (dict(op='watch-failure-retire', deadline=19), [], 10),  # invalid later deadline at6
            (dict(op='watch-failure-retire', deadline=12), [], 10)])
        pair = [Mock(), Mock()]
        packets = []
        def publish(raw, ancillary, flags):
            packets.append(json.loads(raw[0])); return len(raw[0])
        pair[1].sendmsg.side_effect = publish
        cancel, exit_call = Mock(), Mock(side_effect=SimulatedExit)
        with patch.object(entry, 'os', SimpleNamespace(getpid=lambda: 16, _exit=exit_call)), \
                patch.object(entry, 'signal', SimpleNamespace(pidfd_send_signal=cancel)), \
                patch.object(entry, 'socket', SimpleNamespace(MSG_DONTWAIT=64, MSG_NOSIGNAL=16384)), \
                patch.object(entry, 'arm_parent_death'), \
                patch.object(entry.time, 'monotonic', side_effect=[4, 5, 6, 7, 8]):
            with self.assertRaises(SimulatedExit): harness.watchdog_role(pair, 10)
        self.assertEqual(harness.cleanup_deadline, 12)
        cancel.assert_called_once_with(31, 9, None, 0)
        exit_call.assert_called_once_with(124)
        self.assertEqual([p['deadline'] for p in packets if p['op']=='watch-cleanup-applied'], [12, 12])
        self.assertEqual(len([p for p in packets if p['op']=='watch-fault']), 1)
        self.assertEqual(len([p for p in packets if p['op']=='watch-cancel']), 1)
        self.assertEqual(len([p for p in packets if p['op']=='watch-cancel-end']), 1)

    def test_failure_boundary_does_not_prove_eof_terminal_or_cleanup(self):
        harness, packet, end = self.cancel_stream()
        harness.accept_watch_cancel(packet)
        harness.accept_watch_cancel(end)
        self.assertEqual(harness.watch_cancel_end, end)
        self.assertFalse(harness.watch_evidence_complete)
        for late in (packet, end):
            with self.assertRaises(ValueError): harness.accept_watch_cancel(late)

    def test_cancel_record_missing_repeated_reordered_or_wrong_lifetime_rejected(self):
        for key, value in [('sequence', 1), ('sequence', True), ('pid', True),
                           ('starttime', 999), ('signal', True), ('sent', 1)]:
            with self.subTest(key=key, value=value):
                harness, packet, _ = self.cancel_stream()
                if key == 'sequence': packet[key] = value
                else: packet['record'][key] = value
                with self.assertRaises(ValueError): harness.accept_watch_cancel(packet)
                self.assertEqual(harness.watch_raw, [])
        harness, packet, end = self.cancel_stream()
        with self.assertRaises(ValueError): harness.accept_watch_cancel(end)
        harness.accept_watch_cancel(packet)
        with self.assertRaises(ValueError): harness.accept_watch_cancel(packet)

    def test_final_boundary_digest_inventory_count_or_send_failure_stays_incomplete(self):
        for key, value in [('sha256', '0'*64), ('count', True), ('count', 0),
                           ('lifetimes', []), ('publish_failures', 1),
                           ('publish_failures', False)]:
            with self.subTest(key=key):
                harness, packet, end = self.cancel_stream()
                harness.accept_watch_cancel(packet)
                end[key] = value
                with self.assertRaises(ValueError): harness.accept_watch_cancel(end)
                self.assertIsNone(harness.watch_cancel_end)
                self.assertFalse(harness.watch_evidence_complete)

    def test_failed_signal_record_requires_exact_error_fields_and_is_retained(self):
        harness, packet, _ = self.cancel_stream()
        packet['record'].update(sent=False, error='OSError', errno=5)
        harness.accept_watch_cancel(packet)
        self.assertEqual(harness.watch_raw, [packet['record']])
        harness, packet, _ = self.cancel_stream()
        packet['record']['sent'] = False
        with self.assertRaises(ValueError): harness.accept_watch_cancel(packet)

    def test_pending_handoff_can_reconcile_log_but_cannot_confirm_release(self):
        harness, packet, end = self.cancel_stream()
        harness.watch_handoffs, harness.watch_held = harness.watch_held, {}
        harness.accept_watch_cancel(packet)
        harness.accept_watch_cancel(end)
        self.assertEqual(harness.watch_held, {})
        self.assertFalse(harness.watch_evidence_complete)
        harness, packet, end = self.cancel_stream()
        harness.watch_handoffs = {'observer': dict(identity=self.identities['observer'])}
        harness.accept_watch_cancel(packet)
        harness.accept_watch_cancel(end)  # unreceived pending handoff not invented held
        self.assertNotIn('observer', harness.watch_cancelled)

    def failed_watch_harness(self, live_control=False):
        self.tree = TreeLedger(10, ControlBudget(0))
        self.register('watchdog')
        if live_control: self.register('controller')
        self.tree.abandon_unacquired_group()
        harness, packet, end = self.cancel_stream()
        harness.ledger, harness.consume_attempts = self.tree, {}
        harness.watch_channel, harness.watchdog = Mock(), 16
        harness.watch_channel.fileno.return_value = 50
        harness.watch_retire_attempted, harness.watch_stopping = False, False
        harness.watch_stream_invalid, harness.errors = False, []
        harness.cleanup_deadline, harness.watch_cleanup_confirmed = 12, True
        harness.check, harness.send = Mock(), Mock()
        harness.eof = set()
        harness.terminal = Mock()
        return harness, packet, end

    def test_failed_retirement_holds_terminal_drains_eof_then_consumes_once(self):
        harness, packet, end = self.failed_watch_harness()
        event = dict(si_pid=16, si_code=1, si_status=124)
        timeline = []
        packets = iter([packet, end, None])
        def receive(*args):
            item = next(packets)
            if item is None: harness.eof.add(50); timeline.append('EOF')
            else: harness.accept_watch_cancel(item)
        def terminal(pid, fd, consume=False):
            timeline.append('consume' if consume else 'held')
            return event
        harness.receive, harness.terminal = Mock(side_effect=receive), Mock(side_effect=terminal)
        with patch.object(entry.time, 'sleep'):
            harness.recover_watchdog()
        self.assertEqual(timeline, ['held', 'EOF', 'consume'])
        harness.send.assert_called_once_with(harness.watch_channel,
                                            dict(op='watch-failure-retire', deadline=12))
        self.assertTrue(harness.watch_evidence_complete)
        self.assertFalse(harness.ledger.report()['qualified'])
        harness.recover_watchdog()  # retired exact role has no second send/wait
        self.assertEqual(harness.terminal.call_count, 2)

    def test_failed_retirement_missing_end_or_first_or_wrong_exit_never_complete(self):
        for fault in ('end', 'first', 'exit', 'invalid'):
            with self.subTest(fault=fault):
                harness, packet, end = self.failed_watch_harness()
                if fault != 'end':
                    harness.accept_watch_cancel(packet); harness.accept_watch_cancel(end)
                if fault == 'first': harness.watch_cleanup_confirmed = False
                if fault == 'invalid': harness.watch_stream_invalid = True
                event = dict(si_pid=16, si_code=1, si_status=125 if fault == 'exit' else 124)
                harness.terminal.return_value = event
                def receive(*args): harness.eof.add(50)
                harness.receive = Mock(side_effect=receive)
                harness.recover_watchdog()
                self.assertFalse(harness.watch_evidence_complete)

    def test_failure_retirement_refuses_known_live_controls(self):
        harness, _, _ = self.failed_watch_harness(live_control=True)
        with self.assertRaises(ValueError): harness.recover_watchdog()
        harness.send.assert_not_called(); harness.terminal.assert_not_called()

    def test_failure_retirement_send_uncertainty_never_resends_and_falls_back(self):
        harness, _, _ = self.failed_watch_harness()
        harness.send.side_effect = BlockingIOError(11, 'full')
        harness.watch_cancel_attempted = False
        harness.wait_terminal = Mock(side_effect=TimeoutError('terminal absent'))
        cancel = Mock()
        with patch.object(entry, 'signal', SimpleNamespace(pidfd_send_signal=cancel)):
            with self.assertRaises(TimeoutError): harness.recover_watchdog()
            with self.assertRaises(TimeoutError): harness.recover_watchdog()
        harness.send.assert_called_once(); cancel.assert_called_once()
        self.assertTrue(harness.watch_stream_invalid)
        self.assertFalse(harness.watch_evidence_complete)

    def test_terminal_without_eof_never_consumes_before_original_timeout(self):
        harness, _, _ = self.failed_watch_harness()
        harness.check.side_effect = [None, None, None, TimeoutError('FIRST')]
        harness.receive = Mock(return_value=None)
        harness.terminal.return_value = dict(si_pid=16, si_code=1, si_status=124)
        with patch.object(entry.time, 'sleep'):
            with self.assertRaises(TimeoutError): harness.finish_failed_watchdog(self.identities['watchdog'])
        self.assertTrue(harness.terminal.call_count >= 1)
        self.assertTrue(all(not call.kwargs.get('consume') and len(call.args) == 2
                            for call in harness.terminal.call_args_list))
        self.assertFalse(harness.watch_evidence_complete)

    def pending_harness(self):
        harness = object.__new__(entry.NativeHarness)
        harness.ledger = self.tree
        harness.harness_pid, harness.uid = 10, 100
        harness.direct_births, harness.pending_cancelled = {'watchdog': 16}, set()
        harness.consume_attempts, harness.wait_raw = {}, []
        harness.errors = []
        harness.check, harness.account = Mock(), Mock()
        row = SimpleNamespace(si_pid=16, si_uid=100, si_signo=17, si_code=2, si_status=9)
        fake_os = SimpleNamespace(kill=Mock(), P_PID=1, WEXITED=4, WNOHANG=1,
                                  WNOWAIT=0x1000000, waitid=Mock(return_value=row))
        return harness, fake_os

    def test_pending_fork_rollback_consumes_once_without_normal_admission(self):
        harness, fake_os = self.pending_harness()
        with patch.object(entry, 'os', fake_os), \
                patch.object(entry, 'signal', SimpleNamespace(SIGKILL=9)):
            harness.recover_pending_direct('watchdog')
            with self.assertRaises(RuntimeError): harness.recover_pending_direct('watchdog')
        fake_os.kill.assert_called_once_with(16, 9)
        self.assertEqual(fake_os.waitid.call_count, 2)
        self.assertEqual(harness.consume_attempts[16]['result']['si_pid'], 16)
        self.assertNotIn('watchdog', self.tree.created)
        self.assertFalse(harness.account.call_args.args[0]['normal_admission'])

    def test_pending_fork_consume_none_locks_retry_before_return(self):
        harness, fake_os = self.pending_harness()
        fake_os.waitid.side_effect = [fake_os.waitid.return_value, None]
        with patch.object(entry, 'os', fake_os), \
                patch.object(entry, 'signal', SimpleNamespace(SIGKILL=9)):
            with self.assertRaises(ValueError): harness.recover_pending_direct('watchdog')
            with self.assertRaises(RuntimeError): harness.recover_pending_direct('watchdog')
        self.assertEqual(fake_os.waitid.call_count, 2)
        self.assertIsNone(harness.consume_attempts[16]['result'])
        self.assertEqual(harness.wait_raw[-1]['wait'], None)

    def test_pending_source_never_grants_rights_over_received_or_adopted_pid(self):
        harness, fake_os = self.pending_harness()
        with patch.object(entry, 'os', fake_os):
            for role in ('observer', 'root', 'true-path', 'controller'):
                with self.assertRaises(ValueError): harness.recover_pending_direct(role)
            self.register('watchdog')
            with self.assertRaises(ValueError): harness.recover_pending_direct('watchdog')
        fake_os.kill.assert_not_called(); fake_os.waitid.assert_not_called()

    def test_pending_wrong_uid_terminal_cannot_grant_consuming_authority(self):
        harness, fake_os = self.pending_harness()
        fake_os.waitid.return_value.si_uid = 999
        with patch.object(entry, 'os', fake_os), \
                patch.object(entry, 'signal', SimpleNamespace(SIGKILL=9)):
            with self.assertRaises(ValueError): harness.recover_pending_direct('watchdog')
        fake_os.waitid.assert_called_once()
        self.assertNotIn(16, harness.consume_attempts)

    def test_pending_signal_error_still_observes_exact_terminal_without_retry(self):
        harness, fake_os = self.pending_harness()
        fake_os.kill.side_effect = OSError(5, 'uncertain send')
        with patch.object(entry, 'os', fake_os), \
                patch.object(entry, 'signal', SimpleNamespace(SIGKILL=9)):
            harness.recover_pending_direct('watchdog')
            with self.assertRaises(RuntimeError): harness.recover_pending_direct('watchdog')
        fake_os.kill.assert_called_once_with(16, 9)
        self.assertEqual(fake_os.waitid.call_count, 2)
        self.assertFalse(harness.errors[0]['sent'])
        self.assertEqual(harness.errors[0]['errno'], 5)

    def test_pending_expired_clock_cannot_send_signal(self):
        harness, fake_os = self.pending_harness()
        harness.check.side_effect = TimeoutError('FIRST already exhausted')
        with patch.object(entry, 'os', fake_os):
            with self.assertRaises(TimeoutError): harness.recover_pending_direct('watchdog')
        fake_os.kill.assert_not_called(); fake_os.waitid.assert_not_called()
        self.assertNotIn('watchdog', harness.pending_cancelled)

    def test_birth_capture_precedes_pidfd_acquisition_in_both_fixed_forks(self):
        source = Path(entry.__file__).read_text()
        module = ast.parse(source)
        cls = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == 'NativeHarness')
        methods = {n.name: ast.unparse(n) for n in cls.body if isinstance(n, ast.FunctionDef)}
        for method, role in (('start_watchdog', 'watchdog'), ('_run_unreviewed_draft', 'controller')):
            text = methods[method]
            self.assertLess(text.index(f"self.direct_births['{role}'] ="),
                            text.index(f'os.pidfd_open(self.{role}, 0)'))

    def test_failed_cleanup_notice_cannot_block_safe_cleanup_clock(self):
        harness = object.__new__(entry.NativeHarness)
        harness.budget, harness.cleanup_deadline = ControlBudget(0), None
        harness.watch_deadline_records, harness.errors = [], []
        harness.watch_channel, harness.watch_stopping = object(), False
        harness.send = Mock(side_effect=BlockingIOError(11, 'no delivery'))
        with patch.object(entry.time, 'monotonic', return_value=5):
            self.assertEqual(harness.enter_cleanup(), 15)
        self.assertEqual(harness.cleanup_deadline, 15)
        self.assertFalse(harness.watch_cleanup_confirmed)
        self.assertEqual(harness.errors[0]['phase'], 'watch-cleanup-notice')

    def test_creation_and_consumption_barriers_are_present_in_whole_source(self):
        path = Path(__file__).resolve().parents[1]/'scripts/rust_semantic_supervisor_entry.py'
        module = ast.parse(path.read_text())
        cls = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == 'NativeHarness')
        methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
        controller = ast.unparse(methods['controller_role'])
        self.assertLess(controller.index('self.await_ack(ctrl[1], harness, 0)'), controller.index('os.fork()'))
        observer = ast.unparse(methods['observer_role'])
        self.assertLess(observer.index('arm_parent_death'), observer.index('self._close_channel'))
        wait = ast.unparse(methods['terminal'])
        self.assertLess(wait.index('self.consume_attempts[pid] ='), wait.index('os.waitid'))
        self.assertLess(wait.index('self.wait_raw.append'), wait.index('self.account'))
        run = ast.unparse(methods['_run_unreviewed_draft'])
        self.assertIn('self.wait_terminal(self.controller, controller_pidfd, True)', run)
        self.assertIn('for output_fd in self.output_writes.values()', run)

    def test_duplicate_wrong_consumer_and_mismatched_terminal_refused(self):
        self.all_roles()
        with self.assertRaises(ValueError): self.consume('true-path', 'root')
        with self.assertRaises(ValueError): self.tree.terminal('root', 13, 999, 0, 'observer')
        self.consume('true-path', 'observer')
        with self.assertRaises(ValueError): self.consume('true-path', 'observer')

    def test_outputs_share_one_budget_including_cleanup(self):
        self.budget.feed('stdout', b'x'*65536)
        self.budget.begin_cleanup(1)
        for _ in range(15): self.budget.feed('trace', b'y'*65536)
        with self.assertRaises(OverflowError): self.budget.feed('stderr', b'z')
        self.assertEqual(self.budget.total_seen, 1048577)
        self.assertEqual(self.budget.total_retained, 1048576)

    def test_native_entry_is_unconditionally_closed_and_unregistered(self):
        path = Path(__file__).resolve().parents[1]/'scripts/rust_semantic_supervisor_entry.py'
        module = ast.parse(path.read_text())
        cls = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == 'NativeHarness')
        entry = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'run_control')
        self.assertEqual(len(entry.body), 1)
        self.assertIsInstance(entry.body[0], ast.Raise)
        self.assertNotIn('__main__', path.read_text())

    def fake_harness(self):
        harness = object.__new__(entry.NativeHarness)  # no native constructor
        harness.check, harness.account = Mock(), Mock()
        harness.uid = 100
        harness.consume_attempts, harness.wait_raw, harness.fds = {}, [], {}
        fake_os = SimpleNamespace(P_PIDFD=3, WEXITED=4, WNOHANG=1, WNOWAIT=0x1000000,
                                  waitid=Mock(), fstat=Mock(), get_inheritable=Mock(return_value=False))
        return harness, fake_os

    def test_late_consume_retains_result_and_cannot_repeat_kernel_call(self):
        harness, fake_os = self.fake_harness()
        fake_os.waitid.return_value = SimpleNamespace(si_pid=11, si_uid=100, si_signo=17,
                                                      si_code=1, si_status=0)
        harness.check.side_effect = [None, TimeoutError('late')]
        with patch.object(entry, 'os', fake_os):
            with self.assertRaises(TimeoutError): harness.terminal(11, 30, True)
            self.assertEqual(harness.consume_attempts[11]['result']['si_pid'], 11)
            self.assertEqual(harness.wait_raw[0]['wait']['si_status'], 0)
            harness.check.side_effect = None
            with self.assertRaises(ValueError): harness.terminal(11, 30, True)
        fake_os.waitid.assert_called_once()

    def test_uncertain_consume_exception_also_retires_authority(self):
        harness, fake_os = self.fake_harness()
        fake_os.waitid.side_effect = OSError(4, 'ambiguous')
        with patch.object(entry, 'os', fake_os):
            with self.assertRaises(OSError): harness.terminal(11, 30, True)
            with self.assertRaises(ValueError): harness.terminal(11, 30, False)
        fake_os.waitid.assert_called_once()
        self.assertEqual(harness.consume_attempts[11]['error'], 'InterruptedError')

    def test_fstat_failure_preserves_new_fd_ownership_before_validation(self):
        harness, fake_os = self.fake_harness()
        fake_os.fstat.side_effect = OSError(5, 'unmeasurable')
        with patch.object(entry, 'os', fake_os):
            with self.assertRaises(OSError): harness.hold(30)
        self.assertIn(30, harness.fds)
        self.assertIsNone(harness.fds[30])

    def test_consuming_none_is_uncertain_not_an_invitation_to_retry(self):
        harness, fake_os = self.fake_harness()
        fake_os.waitid.return_value = None
        with patch.object(entry, 'os', fake_os):
            with self.assertRaises(ValueError): harness.terminal(11, 30, True)
            with self.assertRaises(ValueError): harness.terminal(11, 30, True)
        fake_os.waitid.assert_called_once()

    def test_output_overflow_cannot_block_cleanup_clock_or_retry_failed_stream(self):
        harness = object.__new__(entry.NativeHarness)
        harness.harness_pid = 10
        harness.budget = ControlBudget(0)
        harness.cleanup_deadline = harness.budget.begin_cleanup(1)
        harness.output_failed, harness.errors = False, []
        harness.drain_outputs = Mock(side_effect=OverflowError('raw cap'))
        with patch.object(entry, 'os', SimpleNamespace(getpid=lambda: 10)), \
                patch.object(entry.time, 'monotonic', return_value=2):
            harness.check(); harness.check()
        harness.drain_outputs.assert_called_once()
        self.assertTrue(harness.output_failed)
        self.assertEqual(harness.budget.cleanup_deadline, 11)

    def test_active_output_failure_refuses_control_but_cleanup_can_proceed(self):
        harness = object.__new__(entry.NativeHarness)
        harness.harness_pid = 10
        harness.budget, harness.cleanup_deadline = ControlBudget(0), None
        harness.output_failed, harness.errors = False, []
        harness.drain_outputs = Mock(side_effect=OSError(5, 'pipe'))
        with patch.object(entry, 'os', SimpleNamespace(getpid=lambda: 10)), \
                patch.object(entry.time, 'monotonic', return_value=2):
            with self.assertRaises(OSError): harness.check()
            harness.cleanup_deadline = harness.budget.begin_cleanup(2)
            harness.check()
        harness.drain_outputs.assert_called_once()
        self.assertEqual(harness.budget.cleanup_deadline, 12)


if __name__ == '__main__': unittest.main()
