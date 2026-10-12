"""Injected OS/FD mocks only, no actual pipe/read/write/close calls."""
import errno
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.rust_semantic_journal_pipe import JournalPipe
from scripts.rust_semantic_control_journal import FirstStopEmitter, FRAME
from tests.test_rust_semantic_control_journal import frame
from tests.test_rust_semantic_first_stop_emitter import packet


class JournalPipeTests(unittest.TestCase):
    def adapter(self, role='reader', clock=None):
        self.info = SimpleNamespace(st_dev=1, st_ino=10, st_uid=1000, st_mode=stat.S_IFIFO)
        self.os = SimpleNamespace(O_RDONLY=0, O_WRONLY=1, O_ACCMODE=3, O_NONBLOCK=2048,
                                  fstat=Mock(side_effect=lambda fd: self.info),
                                  getuid=Mock(return_value=1000),
                                  get_inheritable=Mock(return_value=False),
                                  read=Mock(return_value=b''), write=Mock(return_value=FRAME.size))
        self.flags = SimpleNamespace(F_GETFL=3, fcntl=Mock(return_value=2048 + (role == 'writer')))
        with patch('scripts.rust_semantic_journal_pipe.sys.platform', 'linux'):
            return JournalPipe(role, 10, dict(device=1, inode=10, uid=1000), 90, 100, 20.,
                               os_api=self.os, fcntl_api=self.flags, clock=clock or (lambda: 1.))

    def test_first_stop_writer_composition_and_copied_receipts(self):
        writer = self.adapter('writer')
        emitter = FirstStopEmitter(90, 100, writer.write_frame, lambda: 1., 20.)
        self.assertTrue(emitter(101, 100, packet()))
        self.os.write.assert_called_once_with(10, frame(start=200))
        row = writer.report()['records'][0]
        row['before']['inode'] = 99
        self.assertEqual(writer.report()['records'][0]['before']['inode'], 10)
        self.assertFalse(writer.report()['source_authenticated'])
        self.assertFalse(writer.report()['qualified'])

    def test_fragmented_reader_and_cleanup_eof_not_process_exit(self):
        reader = self.adapter()
        raw = frame() + frame(2, 102, 101) + frame(3, 103, 101)
        self.os.read.side_effect = [raw[:5], OSError(errno.EAGAIN, 'pending'), raw[5:], b'']
        reader.tick()
        self.assertIsNone(reader.tick())
        reader.begin_cleanup(11.)
        reader.tick()
        reader.tick()
        self.assertTrue(reader.report()['journal']['transport_complete'])
        self.assertFalse(reader.report()['outer_cleanup_complete'])
        self.assertEqual(self.os.read.call_count, 4)
        with self.assertRaises(RuntimeError):
            reader.tick()
        with self.assertRaises(RuntimeError):
            reader.begin_cleanup(11.)

    def test_wrong_mode_flags_receipt_or_object_no_io(self):
        for bad in ('mode', 'blocking', 'inheritable', 'inode', 'owner', 'file'):
            reader = self.adapter()
            if bad == 'mode': self.flags.fcntl.return_value = 2049
            if bad == 'blocking': self.flags.fcntl.return_value = 0
            if bad == 'inheritable': self.os.get_inheritable.return_value = True
            if bad == 'inode': self.info.st_ino = 99
            if bad == 'owner': self.info.st_uid = 1001
            if bad == 'file': self.info.st_mode = stat.S_IFREG
            with self.assertRaises(ValueError): reader.tick()
            self.os.read.assert_not_called()
            self.assertTrue(reader.report()['failed'])

    def test_read_before_postcheck_failure_preserves_raw(self):
        reader = self.adapter()
        def read(fd, size):
            self.info.st_ino = 99
            return frame()
        self.os.read.side_effect = read
        with self.assertRaises(ValueError): reader.tick()
        self.assertEqual(reader.report()['journal']['raw_hex'], frame().hex())
        self.assertEqual(reader.report()['records'][0]['raw_hex'], frame().hex())
        with self.assertRaises(RuntimeError): reader.tick()
        self.os.read.assert_called_once()

    def test_short_error_late_writes_never_retry(self):
        for outcome in (0, True, FRAME.size - 1, OSError(errno.EINTR, 'uncertain'),
                        OSError(errno.EAGAIN, 'full')):
            writer = self.adapter('writer')
            if isinstance(outcome, Exception): self.os.write.side_effect = outcome
            else: self.os.write.return_value = outcome
            with self.assertRaises((ValueError, OSError)): writer.write_frame(frame())
            with self.assertRaises(RuntimeError): writer.write_frame(frame())
            self.os.write.assert_called_once()
        times = iter((1., 1., 1.6))
        writer = self.adapter('writer', lambda: next(times))
        with self.assertRaises(ValueError): writer.write_frame(frame())
        self.assertEqual(writer.report()['records'][0]['written'], FRAME.size)

    def test_overflow_truncation_and_fatal_read_lock(self):
        for raw in (b'x' * 133, frame()[:-1]):
            reader = self.adapter()
            self.os.read.side_effect = [raw, b'']
            with self.assertRaises(ValueError):
                reader.tick()
                reader.tick()
            self.assertTrue(reader.report()['failed'])
            self.assertEqual(reader.report()['records'][0]['raw_hex'], raw.hex())
        reader = self.adapter()
        self.os.read.side_effect = OSError(errno.EIO, 'broken')
        with self.assertRaises(OSError): reader.tick()
        with self.assertRaises(RuntimeError): reader.tick()
        self.os.read.assert_called_once()

    def test_clock_record_bound_and_exact_borrowed_fd(self):
        for clock in (iter((1., 0.)), iter((1., 20.))):
            reader = self.adapter(clock=lambda: next(clock))
            with self.assertRaises(ValueError): reader.tick()
            self.os.read.assert_not_called()
        reader = self.adapter()
        reader.records = [{}] * 4096
        with self.assertRaises(RuntimeError): reader.tick()
        self.os.read.assert_not_called()
        for deadline in (True, 1., 12., float('nan')):
            reader = self.adapter()
            with self.assertRaises(ValueError): reader.begin_cleanup(deadline)
            with self.assertRaises(RuntimeError): reader.begin_cleanup(11.)
            self.assertTrue(reader.report()['failed'])
            self.os.read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
