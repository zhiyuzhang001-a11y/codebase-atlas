"""Injected protocol tests; never spawn, wait, signal or inspect native FDs."""
import types
import unittest

from scripts.rust_semantic_control_observer import observe_fixed


class ObserverTests(unittest.TestCase):
    def harness(self, failure=None, terminal=True, bound=True, close_error=False,
                cleanup_error=False, clocks=(100, 101, 101), deadline=111):
        actions = []
        ops = types.SimpleNamespace(handles={10: {}} if bound else {},
                                    calls=[], observations=[])
        def close():
            actions.append('close')
            if close_error:
                raise OSError('uncertain close')
        ops.close_handles = close
        class Stops:
            def __init__(self, root, observer, operations):
                self.parents, self.admitted = {root: observer}, {}
                self.early, self.events, self.samples = {}, [], []
                self.terminals, self.pending_stops = {}, set()
            def run(self, limit):
                actions.append(('run', limit))
                self.events.append({'pid': 10, 'status': 0})
                if terminal:
                    self.terminals[10] = 0
                if failure:
                    raise failure
        class Cleanup:
            def __init__(self, loop, operations):
                actions.append(('drain-init', dict(loop.terminals)))
                self.known, self.parents = {10}, dict(loop.parents)
                self.terminals, self.events, self.errors = {}, [], []
                self.stopped = set()
            def run(self, limit):
                actions.append(('drain', limit))
                if cleanup_error:
                    self.known.add(11)
                    self.parents[11] = 10
                    self.events.append({'pid': 11, 'status': 9})
                    self.terminals[11] = 9
                    raise PermissionError('cannot drain')
                return {'qualified': False, 'outer_cleanup_complete': False}
        def transition():
            actions.append('cleanup-transition')
            return deadline
        times = iter(clocks)
        def bootstrap():
            actions.append('bootstrap-pidfd-kill')
            if getattr(failure, 'bootstrap_failure', False):
                raise PermissionError('failed pidfd send')
        result = observe_fixed(10, 9, ops, types.SimpleNamespace(
            FixedForkStops=Stops, FixedForkCleanup=Cleanup), clock=lambda: next(times),
            active_deadline=120, cleanup_deadline=transition,
            bootstrap_kill=bootstrap)
        return result, actions

    def test_normal_completion_still_drains_once_and_never_qualifies(self):
        result, actions = self.harness()
        self.assertTrue(result['control_complete'])
        self.assertEqual(result['errors'], [])
        self.assertEqual(actions.count('cleanup-transition'), 1)
        self.assertIn(('drain', 111), actions)
        self.assertEqual(actions[-1], 'close')
        self.assertFalse(result['qualified'])
        self.assertFalse(result['outer_cleanup_complete'])

    def test_before_first_stop_failure_uses_bootstrap_handle_before_drain(self):
        result, actions = self.harness(PermissionError('stop denied'), False, False,
                                      clocks=(100, 101))
        self.assertFalse(result['control_complete'])
        self.assertLess(actions.index('bootstrap-pidfd-kill'), actions.index(('drain', 111)))
        self.assertTrue(result['stops']['events'])
        self.assertEqual(result['errors'][0]['type'], 'PermissionError')
        # Already admitted root uses NativeStops' handle, not bootstrap authority.
        _, bound_actions = self.harness(ValueError('partial'), False, True,
                                        clocks=(100, 101))
        self.assertNotIn('bootstrap-pidfd-kill', bound_actions)

    def test_late_completion_and_cleanup_close_errors_preserve_evidence(self):
        result, actions = self.harness(clocks=(100, 120, 120), deadline=130,
                                      cleanup_error=True, close_error=True)
        self.assertFalse(result['control_complete'])
        self.assertEqual([row['operation'] for row in result['errors']],
                         ['active', 'cleanup', 'close-handles'])
        self.assertEqual(result['stops']['terminals'], {10: 0})
        self.assertTrue(result['cleanup']['partial'])
        self.assertEqual(result['cleanup']['parents'][11], 10)
        self.assertEqual(result['cleanup']['terminals'], {11: 9})
        self.assertEqual(result['cleanup']['events'], [{'pid': 11, 'status': 9}])
        self.assertFalse(result['cleanup']['all_terminal'])
        self.assertFalse(result['cleanup']['root_reaped'])
        self.assertEqual(actions.count('close'), 1)

    def test_invalid_cleanup_remaining_deadline_never_starts_drain(self):
        for deadline in (101, 112, float('nan'), True):
            result, actions = self.harness(deadline=deadline)
            self.assertIsNone(result['cleanup'])
            self.assertEqual(result['errors'][0]['operation'], 'cleanup')
            self.assertFalse(any(isinstance(a, tuple) and a[0] == 'drain' for a in actions))
            self.assertEqual(actions[-1], 'close')

    def test_nonfinite_backwards_or_boolean_completion_is_not_success(self):
        for finished in (float('nan'), float('inf'), 99, True):
            result, actions = self.harness(clocks=(100, finished, 101))
            self.assertFalse(result['control_complete'])
            self.assertEqual(result['errors'][0]['operation'], 'active')
            self.assertIn(('drain', 111), actions)

    def test_failed_bootstrap_signal_does_not_skip_terminal_drain(self):
        failure = ValueError('before initial stop')
        failure.bootstrap_failure = True
        result, actions = self.harness(failure, False, False, clocks=(100, 101))
        self.assertEqual([row['operation'] for row in result['errors']],
                         ['active', 'bootstrap-kill'])
        self.assertIn(('drain', 111), actions)
        self.assertFalse(result['outer_cleanup_complete'])


if __name__ == '__main__':
    unittest.main()
