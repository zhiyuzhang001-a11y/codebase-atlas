"""Pure fixed-stop replay only; never constructs NativeStops or reads proc."""
import unittest
from unittest.mock import patch

from scripts.rust_semantic_ptrace import FixedForkStops, NativeStops, FORK, EXEC, EXIT, SIGSTOP, SIGTRAP, SIGCHLD


def stopped(sig=SIGSTOP, event=0):
    return event << 16 | sig << 8 | 0x7f


class FakeStops:
    def __init__(self):
        self.actions = []
        self.messages = {}
        self.starts = {}

    def configure(self, pid):
        self.actions.append(('configure', pid))

    def resume(self, pid, sig):
        self.actions.append(('resume', pid, sig))

    def message(self, pid):
        return self.messages[pid]

    def signal_delivery(self, pid, sig):
        self.actions.append(('delivery', pid, sig))

    def sample(self, pid, start, parent):
        self.actions.append(('sample', pid, start, parent))
        return {'before': {'starttime': self.starts.get(pid, pid+100)},
                'after': {'starttime': self.starts.get(pid, pid+100)},
                'rss_bytes': 4096, 'start_seconds': 0, 'end_seconds': .01}


class FixedForkTests(unittest.TestCase):
    def setUp(self):
        self.ops = FakeStops()
        self.loop = FixedForkStops(10, 9, self.ops)

    def event(self, pid, kind, message):
        self.ops.messages[pid] = message
        self.loop.event(pid, stopped(SIGTRAP, kind))

    def child(self, pid, early=False):
        if early:
            self.loop.event(pid, stopped())
            self.assertNotIn(pid, self.loop.admitted)
            self.assertNotIn(('resume', pid, 0), self.ops.actions)
        self.event(10, FORK, pid)
        if not early:
            self.loop.event(pid, stopped())
        self.event(pid, EXEC, pid)
        self.event(pid, EXIT, 0)
        self.loop.event(pid, 0)
        self.loop.event(10, stopped(SIGCHLD))

    def test_complete_serial_children_both_notification_orders(self):
        self.loop.event(10, stopped())
        self.child(11, early=True)
        self.child(12)
        self.event(10, EXIT, 0)
        self.loop.event(10, 0)
        self.assertEqual(set(self.loop.admitted), {10, 11, 12})
        self.assertEqual(set(self.loop.terminals), {10, 11, 12})
        for child in (11, 12):
            configured = self.ops.actions.index(('configure', child))
            sampled = next(i for i, row in enumerate(self.ops.actions)
                           if row[0:2] == ('sample', child))
            resumed = self.ops.actions.index(('resume', child, 0))
            self.assertLess(configured, sampled)
            self.assertLess(sampled, resumed)
        self.assertIn(('delivery', 10, SIGCHLD), self.ops.actions)
        self.assertIn(('resume', 10, SIGCHLD), self.ops.actions)

    def test_no_unknown_initial_or_reused_pid(self):
        for pid, status in ((11, stopped()), (10, 0), (10, stopped(SIGTRAP))):
            with self.assertRaises(ValueError):
                FixedForkStops(10, 9, FakeStops()).event(pid, status)
        self.loop.event(10, stopped())
        self.child(11)
        with self.assertRaises(ValueError):
            self.loop.event(11, 0)

    def test_rejects_extra_stop_unknown_event_signal_and_child_fork(self):
        for sig, kind in ((SIGSTOP, 0), (SIGTRAP, 0),
                          (15, 0), (SIGTRAP, 3)):
            ops = FakeStops()
            loop = FixedForkStops(10, 9, ops)
            loop.event(10, stopped())
            with self.assertRaises(ValueError):
                loop.event(10, stopped(sig, kind))
        self.loop.event(10, stopped())
        self.event(10, FORK, 11)
        self.loop.event(11, stopped())
        with self.assertRaises(ValueError):
            self.event(11, FORK, 12)

    def test_rejects_missing_exec_exit_or_incomplete_root(self):
        self.loop.event(10, stopped())
        self.event(10, FORK, 11)
        self.loop.event(11, stopped())
        with self.assertRaises(ValueError):
            self.event(11, EXIT, 0)
        with self.assertRaises(ValueError):
            self.loop.event(11, 0)
        self.event(10, EXIT, 0)
        with self.assertRaises(ValueError):
            self.loop.event(10, 0)

    def test_positive_sample_required_and_bounds(self):
        self.ops.sample = lambda *args: {'before': {'starttime': 0}, 'rss_bytes': 0}
        with self.assertRaises(ValueError):
            self.loop.event(10, stopped())
        self.assertFalse(any(row[0] == 'resume' for row in self.ops.actions))
        for root, observer in ((True, 9), (10, 10), (0, 9)):
            with self.assertRaises(ValueError):
                FixedForkStops(root, observer, FakeStops())
        self.loop.events = [{}] * 4096
        with self.assertRaises(ValueError):
            self.loop.event(10, stopped())

    def test_invalid_deadline_never_waits(self):
        with patch('scripts.rust_semantic_ptrace.time.monotonic', return_value=100):
            for deadline in (True, float('nan'), float('inf'), 99, 121):
                with self.assertRaises(ValueError):
                    self.loop.run(deadline)

    def test_proc_failure_does_not_resume(self):
        self.ops.sample = lambda *args: (_ for _ in ()).throw(PermissionError('denied'))
        with self.assertRaises(PermissionError):
            self.loop.event(10, stopped())
        self.assertFalse(any(row[0] == 'resume' for row in self.ops.actions))


class NativeAdapterMocks(unittest.TestCase):
    def test_real_sigchld_info_validation_without_kernel_calls(self):
        native = object.__new__(NativeStops)
        native.observations = []
        def supply(request, pid, data):
            data._obj[0], data._obj[1], data._obj[2] = SIGCHLD, 0, 1
        native.ptrace = supply
        native.signal_delivery(10, SIGCHLD)
        self.assertEqual(native.observations[0]['signal_info']['code'], 1)
        def bad(request, pid, data):
            data._obj[0], data._obj[1], data._obj[2] = SIGTRAP, 0, 0
        native.ptrace = bad
        with self.assertRaises(ValueError):
            native.signal_delivery(10, SIGCHLD)

    def test_native_construction_rejects_other_platform_before_loading_libc(self):
        with patch('scripts.rust_semantic_ptrace.sys.platform', 'darwin'), \
                patch('scripts.rust_semantic_ptrace.ctypes.CDLL') as load:
            with self.assertRaises(ValueError):
                NativeStops(10)
            load.assert_not_called()


if __name__ == '__main__':
    unittest.main()
