"""Pure byte transport tests; no native observer or execution."""
import unittest

from scripts.rust_semantic_control_journal import ControlJournal, FRAME, MAGIC


def frame(seq=1, pid=101, parent=100, start=99, session=100, group=100, magic=MAGIC):
    return FRAME.pack(magic, seq, pid, parent, start, session, group)


class ControlJournalTests(unittest.TestCase):
    def test_fragmented_exact_shape_copies_and_not_authority(self):
        journal = ControlJournal(90, 100)
        raw = frame() + frame(2, 102, 101) + frame(3, 103, 101)
        for byte in raw:
            journal.feed(bytes([byte]))
        journal.eof()
        report = journal.report()
        self.assertTrue(report['transport_complete'])
        self.assertFalse(report['source_authenticated'])
        self.assertFalse(report['qualified'])
        self.assertFalse(report['outer_cleanup_complete'])
        self.assertEqual(report['raw_hex'], raw.hex())
        report['identities'][0]['starttime'] = 0
        identity = journal.lookup(101)
        self.assertEqual(identity['starttime'], 99)
        identity['starttime'] = 0
        self.assertEqual(journal.lookup(101)['starttime'], 99)
        with self.assertRaises(RuntimeError):
            journal.feed(frame())

    def test_invalid_identity_and_sequence_latch_preserve_raw(self):
        for raw in (frame(magic=b'BADMAGIC'), frame(seq=2), frame(pid=90),
                    frame(pid=100), frame(pid=1), frame(parent=90), frame(start=0),
                    frame(session=101), frame(group=101)):
            journal = ControlJournal(90, 100)
            with self.assertRaises(ValueError):
                journal.feed(raw)
            self.assertEqual(journal.report()['raw_hex'], raw.hex())
            with self.assertRaises(RuntimeError):
                journal.feed(frame())
            with self.assertRaises(ValueError):
                journal.lookup(101)

    def test_duplicate_lifetime_and_wrong_child_parent_refused(self):
        for second in (frame(2, 101, 101), frame(2, 102, 100), frame(3, 102, 101)):
            journal = ControlJournal(90, 100)
            journal.feed(frame())
            with self.assertRaises(ValueError):
                journal.feed(second)
            self.assertEqual(len(journal.report()['identities']), 1)

    def test_truncated_unknown_partial_and_empty_never_complete(self):
        for count in (0, 1, 2):
            journal = ControlJournal(90, 100)
            if count:
                journal.feed(frame())
            if count == 2:
                journal.feed(frame(2, 102, 101))
            journal.eof()
            self.assertFalse(journal.report()['transport_complete'])
            with self.assertRaises(ValueError):
                journal.lookup(999)
        journal = ControlJournal(90, 100)
        journal.feed(frame()[:-1])
        with self.assertRaises(ValueError):
            journal.eof()
        self.assertTrue(journal.report()['failed'])
        self.assertEqual(journal.report()['pending_bytes'], FRAME.size - 1)

    def test_aggregate_bound_and_invalid_types(self):
        journal = ControlJournal(90, 100)
        raw = frame() + frame(2, 102, 101) + frame(3, 103, 101)
        journal.feed(raw)
        with self.assertRaises(ValueError):
            journal.feed(b'x')
        self.assertEqual(journal.report()['raw_hex'], raw.hex())
        self.assertEqual(journal.report()['omitted_bytes'], 1)
        for value in (b'', bytearray(b'x'), None, b'x' * (FRAME.size * 3 + 1)):
            with self.assertRaises(ValueError):
                ControlJournal(90, 100).feed(value)
        for values in ((True, 100), (90, 90), (0, 100), (90, 2**31)):
            with self.assertRaises(ValueError):
                ControlJournal(*values)

    def test_oversized_chunks_retain_prefix_count_without_decoding(self):
        for prefix in (b'', frame()):
            journal = ControlJournal(90, 100)
            if prefix:
                journal.feed(prefix)
            oversized = b'x' * (FRAME.size * 3 + 1)
            with self.assertRaises(ValueError):
                journal.feed(oversized)
            report = journal.report()
            self.assertEqual(report['seen_bytes'], len(prefix) + len(oversized))
            self.assertEqual(report['omitted_bytes'], len(prefix) + 1)
            self.assertEqual(report['raw_hex'],
                             (prefix + oversized[:FRAME.size * 3 - len(prefix)]).hex())
            self.assertEqual(len(report['identities']), bool(prefix))
            self.assertTrue(report['failed'])
            with self.assertRaises(RuntimeError):
                journal.feed(frame())


if __name__ == '__main__':
    unittest.main()
