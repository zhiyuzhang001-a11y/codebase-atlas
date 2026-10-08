"""Outer protocol only: fake operations, no process/native/pipe execution."""
import errno
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from scripts.rust_semantic_control_budget import ControlBudget
from scripts.rust_semantic_outer_control import OuterControl


class OuterControlTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.budget = ControlBudget(0)
        self.terminal = {'si_pid': 10, 'si_code': 1, 'si_status': 0}
        self.wait = SimpleNamespace(poll=Mock(return_value=self.terminal),
                                    reap=Mock(return_value=dict(self.terminal)),
                                    report=Mock(return_value={'observer_reaped': False}))
        self.owner = SimpleNamespace(cancel_owned_group=Mock(),
            poll_cleanup_request=Mock(return_value=False), deliver_cleanup_deadline=Mock(),
            drain_owned_tracees=Mock(return_value=True),
            no_owned_children=Mock(return_value=True),
            group_absent_after_reap=Mock(return_value=True), report=Mock(return_value={}))
        def tick(clock, cleanup):
            (self.budget.check_cleanup if cleanup else self.budget.check_active)(clock())
            if cleanup:
                for stream in self.budget.STREAMS:
                    if not self.budget.records[stream]['eof']:
                        self.budget.eof(stream)
        self.pipes = SimpleNamespace(tick=Mock(side_effect=tick), report=Mock(return_value={}))
        self.control = OuterControl(self.budget, self.pipes, self.wait, self.owner,
                                    lambda: self.now, self.sleep)

    def sleep(self, delay):
        self.now += delay

    def test_normal_terminal_then_common_cleanup_reap_drain_census_absence(self):
        order = []
        self.owner.cancel_owned_group.side_effect = lambda: order.append('cancel')
        self.owner.drain_owned_tracees.side_effect = lambda: order.append('drain') or True
        self.wait.reap.side_effect = lambda: order.append('reap') or dict(self.terminal)
        self.owner.no_owned_children.side_effect = lambda: order.append('census') or True
        self.owner.group_absent_after_reap.side_effect = lambda: order.append('absence') or True
        result = self.control.run()
        self.assertEqual(order, ['cancel', 'reap', 'drain', 'census', 'absence'])
        self.assertTrue(result['cleanup_protocol_complete'])
        self.assertFalse(result['outer_cleanup_complete'])
        self.assertFalse(result['qualified'])
        with self.assertRaises(RuntimeError):
            self.control.run()
        self.owner.cancel_owned_group.assert_called_once()

    def test_stalled_tracer_terminal_does_not_renew_outer_active_budget(self):
        self.wait.poll.return_value = None
        def cancel():
            self.wait.poll.return_value = self.terminal
        self.owner.cancel_owned_group.side_effect = cancel
        result = self.control.run()
        self.assertGreaterEqual(self.now, 20)
        self.assertLess(self.now, 20.02)
        self.assertEqual(self.budget.cleanup_deadline, self.budget.cleanup_started + 10)
        self.assertFalse(result['active_terminal_observed'])
        self.assertTrue(result['cleanup_protocol_complete'])

    def test_eof_and_send_failure_not_terminal_or_reap_proof(self):
        self.wait.poll.return_value = None
        self.owner.cancel_owned_group.side_effect = OSError(errno.EPERM, 'mock')
        result = self.control.run()
        self.assertFalse(result['cleanup_protocol_complete'])
        self.assertTrue(result['budget']['all_eof'])
        self.wait.reap.assert_not_called()
        self.owner.cancel_owned_group.assert_called_once()
        self.assertTrue(any(row['errno'] == errno.EPERM for row in result['errors']))
        self.assertLessEqual(self.now, 30.02)

    def test_observer_consume_precedes_pending_drain_no_completion_or_later_kill(self):
        self.owner.drain_owned_tracees.return_value = False
        result = self.control.run()
        self.wait.reap.assert_called_once()
        self.assertTrue(result['observer_consumed'])
        self.owner.no_owned_children.assert_not_called()
        self.owner.cancel_owned_group.assert_called_once()
        self.owner.group_absent_after_reap.assert_not_called()
        self.assertFalse(result['cleanup_protocol_complete'])
        self.assertGreaterEqual(self.now, 10)

    def test_ambiguous_reap_or_missing_group_absence_is_not_retried(self):
        for cause in ('reap', 'absence'):
            with self.subTest(cause=cause):
                self.setUp()
                if cause == 'reap':
                    self.wait.reap.side_effect = OSError(errno.ECHILD, 'mock')
                else:
                    self.owner.group_absent_after_reap.return_value = False
                result = self.control.run()
                self.assertFalse(result['cleanup_protocol_complete'])
                self.wait.reap.assert_called_once()
                self.owner.cancel_owned_group.assert_called_once()
                if cause == 'reap':
                    self.owner.drain_owned_tracees.assert_not_called()
                    self.owner.no_owned_children.assert_not_called()

    def test_late_completion_and_bad_cleanup_clock_fail_closed(self):
        def late():
            self.now = 20
            return self.terminal
        self.wait.poll.side_effect = late
        result = self.control.run()
        self.assertFalse(result['active_terminal_observed'])
        self.assertTrue(any(row['phase'] == 'active' for row in result['errors']))
        self.setUp()
        self.now = float('nan')
        result = self.control.run()
        self.assertFalse(result['cleanup_protocol_complete'])
        self.wait.reap.assert_not_called()
        self.owner.cancel_owned_group.assert_called_once()

    def test_inner_cleanup_receives_shared_deadline_before_normal_tracer_terminal(self):
        order = []
        self.now = 2
        self.owner.poll_cleanup_request.return_value = True
        self.wait.poll.return_value = None
        def notify(deadline):
            order.append('notify')
            self.assertEqual(deadline, 12)
            self.assertEqual(self.owner.cancel_owned_group.call_count, 0)
        self.owner.deliver_cleanup_deadline.side_effect = notify
        # The trusted observer completes on its own; census cannot cause it.
        self.wait.poll.side_effect = [None, self.terminal]
        self.owner.cancel_owned_group.side_effect = lambda: order.append('cancel')
        result = self.control.run()
        self.assertEqual(order, ['notify', 'cancel'])
        self.assertTrue(result['inner_cleanup_requested'])
        self.assertTrue(result['cleanup_protocol_complete'])
        self.assertEqual(result['budget']['cleanup_deadline'], 12)

    def test_normal_cleanup_timeout_cancels_without_new_grace(self):
        self.now = 2
        self.owner.poll_cleanup_request.return_value = True
        self.wait.poll.return_value = None
        result = self.control.run()
        self.owner.cancel_owned_group.assert_called_once()
        self.assertFalse(result['cleanup_protocol_complete'])
        self.assertEqual(result['budget']['cleanup_deadline'], 12)
        self.assertLessEqual(self.now, 12.02)

    def test_eof_is_final_only_and_observer_is_not_polled_after_consume(self):
        cleanup_ticks = [0]
        def tick(clock, cleanup):
            if cleanup:
                cleanup_ticks[0] += 1
                if cleanup_ticks[0] == 2:
                    for stream in self.budget.STREAMS:
                        self.budget.eof(stream)
        self.pipes.tick.side_effect = tick
        def reap():
            self.assertFalse(self.budget.report()['all_eof'])
            self.wait.poll.side_effect = RuntimeError('no polling consumed observer')
            return dict(self.terminal)
        self.wait.reap.side_effect = reap
        result = self.control.run()
        self.assertTrue(result['cleanup_protocol_complete'])
        self.wait.reap.assert_called_once()
        self.owner.cancel_owned_group.assert_called_once()
        self.assertEqual(cleanup_ticks[0], 2)

    def test_census_pending_unknown_type_or_error_never_completes(self):
        for state in (False, None, 1, ChildProcessError(errno.ECHILD, 'unqualified census')):
            with self.subTest(state=state):
                self.setUp()
                if isinstance(state, Exception):
                    self.owner.no_owned_children.side_effect = state
                else:
                    self.owner.no_owned_children.return_value = state
                result = self.control.run()
                self.assertFalse(result['cleanup_protocol_complete'])
                self.assertTrue(result['observer_consumed'])
                self.owner.group_absent_after_reap.assert_not_called()
                self.wait.reap.assert_called_once()
                self.owner.cancel_owned_group.assert_called_once()

    def test_ambiguous_consumed_result_no_census_and_no_post_reap_group_signal(self):
        for raw in (None, {**self.terminal, 'si_status': 1}):
            self.setUp()
            self.wait.reap.return_value = raw
            result = self.control.run()
            self.assertFalse(result['observer_consumed'])
            self.assertFalse(result['cleanup_protocol_complete'])
            self.owner.drain_owned_tracees.assert_not_called()
            self.owner.no_owned_children.assert_not_called()
            self.owner.cancel_owned_group.assert_called_once()

    def test_deadline_after_consume_retains_consumed_but_does_not_enter_census(self):
        def late_reap():
            self.now = 10
            return dict(self.terminal)
        self.wait.reap.side_effect = late_reap
        result = self.control.run()
        self.assertTrue(result['observer_consumed'])
        self.assertFalse(result['cleanup_protocol_complete'])
        self.owner.drain_owned_tracees.assert_not_called()
        self.owner.cancel_owned_group.assert_called_once()

    def test_retained_terminal_and_returned_report_cannot_be_mutated_into_success(self):
        def changed_reap():
            self.terminal['si_status'] = 1
            return dict(self.terminal)
        self.wait.reap.side_effect = changed_reap
        result = self.control.run()
        self.assertFalse(result['observer_consumed'])
        self.owner.no_owned_children.assert_not_called()
        result['events'][-1]['result']['si_status'] = 9
        self.assertEqual(self.control.events[-1]['result']['si_status'], 1)

    def test_pipe_error_after_consume_does_not_reactivate_group_signal(self):
        count = [0]
        def tick(clock, cleanup):
            if cleanup:
                count[0] += 1
                if count[0] == 2:
                    raise OSError(errno.EIO, 'pipe failed after observer consumption')
        self.pipes.tick.side_effect = tick
        self.owner.no_owned_children.side_effect = [False, True]
        result = self.control.run()
        self.assertFalse(result['cleanup_protocol_complete'])
        self.assertTrue(result['observer_consumed'])
        self.owner.cancel_owned_group.assert_called_once()
        self.wait.reap.assert_called_once()
        self.assertTrue(any(row['phase'] == 'cleanup-pipes' for row in result['errors']))


if __name__ == '__main__':
    unittest.main()
