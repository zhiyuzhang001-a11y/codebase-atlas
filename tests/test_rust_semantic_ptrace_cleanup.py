"""Mock-only abort drain; no native ptrace, signals, handles or spawning."""
import errno
import unittest
from unittest.mock import patch

from scripts.rust_semantic_ptrace import FixedForkCleanup, FixedForkStops, SIGSTOP
from tests.test_rust_semantic_ptrace import FakeStops, stopped


class CleanupOps(FakeStops):
    def __init__(self, rows=()):
        super().__init__()
        self.rows = iter(rows)

    def kill_bound(self, pid):
        self.actions.append(('bound-kill', pid))

    def bind_stopped(self, pid):
        self.actions.append(('bind-stop', pid))

    def wait(self):
        try:
            return next(self.rows)
        except StopIteration:
            raise ChildProcessError(errno.ECHILD, 'owned children drained')


class CleanupTests(unittest.TestCase):
    def run_drain(self, loop, ops):
        with patch('scripts.rust_semantic_ptrace.time.monotonic', return_value=100):
            return FixedForkCleanup(loop, ops).run(110)

    def test_pending_failed_sample_is_aborted_and_terminal_reaped(self):
        ops = CleanupOps([(10, 9)])
        loop = FixedForkStops(10, 9, ops)
        ops.sample = lambda *args: (_ for _ in ()).throw(PermissionError('denied'))
        with self.assertRaises(PermissionError):
            loop.event(10, stopped())
        self.assertEqual(loop.pending_stops, {10})
        result = self.run_drain(loop, ops)
        self.assertIn(('resume', 10, 0), ops.actions)
        self.assertLess(ops.actions.index(('bind-stop', 10)),
                        ops.actions.index(('bound-kill', 10)))
        self.assertLess(ops.actions.index(('bound-kill', 10)),
                        ops.actions.index(('resume', 10, 0)))
        self.assertTrue(result['all_terminal'])
        self.assertTrue(result['root_reaped'])
        self.assertTrue(result['no_waitable_children'])
        self.assertFalse(result['outer_cleanup_complete'])
        self.assertFalse(result['qualified'])

    def test_resumed_root_uses_bound_handle_not_spurious_continue(self):
        ops = CleanupOps([(10, 9)])
        loop = FixedForkStops(10, 9, ops)
        loop.event(10, stopped())
        self.assertFalse(loop.pending_stops)
        self.run_drain(loop, ops)
        self.assertIn(('bound-kill', 10), ops.actions)
        self.assertNotIn(('resume', 10, 9), ops.actions)

    def test_early_child_and_creation_notification_order_are_drained(self):
        ops = CleanupOps([(10, stopped(5, 1)), (11, 9), (10, 9)])
        loop = FixedForkStops(10, 9, ops)
        loop.event(10, stopped())
        loop.event(11, stopped())
        ops.messages[10] = 11
        result = self.run_drain(loop, ops)
        self.assertEqual(result['known'], [10, 11])
        self.assertTrue(result['all_terminal'])
        self.assertIn(('resume', 11, 0), ops.actions)

    def test_creation_before_initial_stop_discovers_child(self):
        ops = CleanupOps([(10, stopped(5, 1)), (11, stopped(SIGSTOP)),
                          (11, 9), (10, 9)])
        loop = FixedForkStops(10, 9, ops)
        ops.messages[10] = 11
        result = self.run_drain(loop, ops)
        self.assertTrue(result['all_terminal'])
        self.assertLess(ops.actions.index(('bind-stop', 11)),
                        ops.actions.index(('bound-kill', 11)))
        self.assertLess(ops.actions.index(('bound-kill', 11)),
                        ops.actions.index(('resume', 11, 0)))

    def test_no_resume_after_missing_handle_or_failed_bound_kill(self):
        for failure in (KeyError(11), PermissionError(errno.EPERM, 'denied')):
            ops = CleanupOps()
            loop = FixedForkStops(10, 9, ops)
            # Model a newly observed initial child stop during cleanup.
            ops.rows = iter([(11, stopped())])
            ops.kill_bound = lambda pid: (_ for _ in ()).throw(failure)
            result = self.run_drain(loop, ops)
            self.assertNotIn(('resume', 11, 0), ops.actions)
            self.assertNotIn(('resume', 11, 9), ops.actions)
            self.assertFalse(result['all_terminal'])
            self.assertTrue(result['errors'])

    def test_no_kill_or_resume_after_stopped_handle_binding_fails(self):
        ops = CleanupOps([(11, stopped())])
        loop = FixedForkStops(10, 9, ops)
        ops.bind_stopped = lambda pid: (_ for _ in ()).throw(PermissionError(errno.EPERM, 'denied'))
        result = self.run_drain(loop, ops)
        self.assertNotIn(('bound-kill', 11), ops.actions)
        self.assertNotIn(('resume', 11, 0), ops.actions)
        self.assertFalse(result['all_terminal'])

    def test_esrch_is_not_terminal_or_reap_evidence(self):
        ops = CleanupOps()
        loop = FixedForkStops(10, 9, ops)
        ops.kill_bound = lambda pid: (_ for _ in ()).throw(ProcessLookupError(errno.ESRCH, 'gone'))
        result = self.run_drain(loop, ops)
        self.assertFalse(result['all_terminal'])
        self.assertFalse(result['root_reaped'])
        self.assertEqual(result['errors'][0]['errno'], errno.ESRCH)

    def test_duplicate_terminal_and_creation_reuse_rejected(self):
        for rows in ([(10, 9), (10, 9)], [(10, stopped(5, 1))]):
            ops = CleanupOps(rows)
            loop = FixedForkStops(10, 9, ops)
            ops.messages[10] = 10
            with self.assertRaises(ValueError):
                self.run_drain(loop, ops)

    def test_invalid_terminal_words_and_observer_identity_rejected(self):
        for row in ((10, 0x10000), (10, 0xffff), (10, 65), (9, 9)):
            with self.assertRaises(ValueError):
                self.run_drain(FixedForkStops(10, 9, FakeStops()), CleanupOps([row]))

    def test_shared_cleanup_deadline_invalid_or_expired(self):
        ops = CleanupOps()
        loop = FixedForkStops(10, 9, ops)
        with patch('scripts.rust_semantic_ptrace.time.monotonic', return_value=100):
            for deadline in (True, float('nan'), float('inf'), 100, 111):
                with self.assertRaises(ValueError):
                    FixedForkCleanup(loop, ops).run(deadline)
        self.assertEqual(ops.actions, [])
        with patch('scripts.rust_semantic_ptrace.time.monotonic', side_effect=[100, 110]):
            result = FixedForkCleanup(loop, ops).run(110)
        self.assertFalse(result['no_waitable_children'])
        self.assertFalse(result['all_terminal'])


if __name__ == '__main__':
    unittest.main()
