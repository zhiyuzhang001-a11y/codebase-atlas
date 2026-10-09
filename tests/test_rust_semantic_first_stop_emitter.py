"""Injected source/stop/byte-writer tests only; never native calls."""
import errno
import unittest

from scripts.rust_semantic_control_journal import FirstStopEmitter, ControlJournal, FRAME
from scripts.rust_semantic_ptrace import FixedForkStops, SIGSTOP
from tests.test_rust_semantic_ptrace import FakeStops


def packet(pid=101, parent=100):
    identity = dict(pid=pid, ppid=parent, session=100, pgrp=100,
                    starttime=99 + pid, state='t')
    return dict(before=dict(identity), after=dict(identity), rss_bytes=4096,
                start_seconds=.9, end_seconds=1.)


class EmitterTests(unittest.TestCase):
    def emitter(self, writer=None, clock=None):
        self.sent = []
        def write(raw):
            self.sent.append(raw)
            return len(raw)
        return FirstStopEmitter(90, 100, writer or write,
                                clock or (lambda: 1.), 20.)

    def test_three_exact_frames_before_resume_and_only_first_sample(self):
        emitter = self.emitter()
        ops = FakeStops()
        ops.sample = lambda pid, start, parent: packet(pid, parent)
        def emit(pid, parent, sample):
            self.assertNotIn(('resume', pid, 0), ops.actions)
            return emitter(pid, parent, sample)
        loop = FixedForkStops(101, 100, ops, journal_emit=emit)
        loop.event(101, SIGSTOP << 8 | 0x7f)
        loop._sample(101)
        self.assertEqual(len(self.sent), 1)
        emitter(102, 101, packet(102, 101))
        emitter(103, 101, packet(103, 101))
        journal = ControlJournal(90, 100)
        journal.feed(b''.join(self.sent))
        journal.eof()
        self.assertEqual(journal.lookup(102)['starttime'], 201)
        self.assertTrue(journal.report()['transport_complete'])
        report = emitter.report()
        report['records'][0]['packet']['before']['starttime'] = 0
        self.assertEqual(emitter.report()['records'][0]['packet']['before']['starttime'], 200)
        self.assertFalse(report['source_authenticated'])
        self.assertFalse(report['qualified'])
        with self.assertRaises(RuntimeError):
            emitter(104, 101, packet(104, 101))

    def test_failed_send_preserves_admission_never_resumes_or_retries(self):
        for outcome in (0, FRAME.size - 1, True, OSError(errno.EAGAIN, 'full'),
                        OSError(errno.EINTR, 'interrupted')):
            calls = []
            def write(raw):
                calls.append(raw)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            emitter = self.emitter(write)
            ops = FakeStops()
            ops.sample = lambda *args: packet()
            loop = FixedForkStops(101, 100, ops, journal_emit=emitter)
            with self.assertRaises((ValueError, OSError)):
                loop.event(101, SIGSTOP << 8 | 0x7f)
            self.assertEqual(loop.admitted[101], 200)
            self.assertIn(101, loop.pending_stops)
            self.assertNotIn(('resume', 101, 0), ops.actions)
            with self.assertRaises(RuntimeError):
                emitter(101, 100, packet())
            self.assertEqual(len(calls), 1)
            self.assertTrue(emitter.report()['records'][0]['write_attempted'])

    def test_native_packet_changes_refused_without_write(self):
        for where, key, value in (('before', 'ppid', 90), ('after', 'starttime', 5),
                                  ('after', 'state', 'R'), ('before', 'session', 101),
                                  ('before', 'pid', True), ('after', 'pgrp', 101)):
            sample = packet()
            sample[where][key] = value
            emitter = self.emitter()
            with self.assertRaises(ValueError):
                emitter(101, 100, sample)
            self.assertFalse(self.sent)
            self.assertFalse(emitter.report()['records'][0]['write_attempted'])

    def test_stale_future_and_late_packet_fail_closed(self):
        for start, end in ((0., .1), (1.1, 1.2), (.99, .98), (float('nan'), 1.)):
            sample = packet()
            sample.update(start_seconds=start, end_seconds=end)
            emitter = self.emitter()
            with self.assertRaises(ValueError):
                emitter(101, 100, sample)
            self.assertFalse(self.sent)
        times = iter((1., 1., 1., 1.5))
        emitter = self.emitter(clock=lambda: next(times))
        with self.assertRaises(ValueError):
            emitter(101, 100, packet())
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(emitter.report()['records'][0]['written'], FRAME.size)
        self.assertTrue(emitter.report()['failed'])

    def test_ordering_and_invalid_deadlines(self):
        emitter = self.emitter()
        with self.assertRaises(ValueError):
            emitter(102, 101, packet(102, 101))
        self.assertFalse(self.sent)
        for deadline in (True, float('inf'), 1., 22.):
            with self.assertRaises(ValueError):
                FirstStopEmitter(90, 100, lambda raw: len(raw), lambda: 1., deadline)

    def test_unconfirmed_callback_never_resumes(self):
        ops = FakeStops()
        loop = FixedForkStops(101, 100, ops, journal_emit=lambda *args: 1)
        with self.assertRaises(ValueError):
            loop.event(101, SIGSTOP << 8 | 0x7f)
        self.assertNotIn(('resume', 101, 0), ops.actions)


if __name__ == '__main__':
    unittest.main()
