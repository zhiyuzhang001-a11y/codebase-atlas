"""Mock-only fixed handshake; no real pipes, process, or OS IO."""
import errno
import stat
import struct
import types
import unittest
from unittest.mock import patch

from scripts.rust_semantic_cleanup_ipc import CleanupIPC, GRANT, REQUEST


class FakeOS:
    O_RDONLY, O_WRONLY, O_ACCMODE, O_NONBLOCK = 0, 1, 3, 2048
    def __init__(self):
        self.input, self.output = [], []
        self.changed = self.blocking = self.inheritable = False
        self.write_result = None
        self.write_error = None
    def getuid(self): return 501
    def get_inheritable(self, fd): return self.inheritable
    def fstat(self, fd):
        return types.SimpleNamespace(st_dev=1, st_ino=fd + int(self.changed),
                                     st_mode=stat.S_IFIFO, st_uid=501)
    def read(self, fd, size):
        raw = self.input.pop(0) if self.input else OSError(errno.EAGAIN, 'pending')
        if isinstance(raw, Exception): raise raw
        return raw
    def write(self, fd, raw):
        self.output.append(raw)
        if self.write_error: raise OSError(self.write_error, 'write failed')
        return len(raw) if self.write_result is None else self.write_result


class Flags:
    F_GETFL = 3
    def __init__(self, os_api): self.os = os_api
    def fcntl(self, fd, operation):
        return (0 if fd == 8 else 1) | (0 if self.os.blocking else 2048)


class CleanupIPCTests(unittest.TestCase):
    def setUp(self):
        platform = patch('scripts.rust_semantic_cleanup_ipc.sys.platform', 'linux')
        platform.start()
        self.addCleanup(platform.stop)
        self.os = FakeOS()
        self.time = 1
    def endpoint(self, role):
        return CleanupIPC(role, 8, 9, 20, os_api=self.os,
                          fcntl_api=Flags(self.os), clock=lambda: self.time)

    def test_same_outer_absolute_deadline_no_inner_new_grace(self):
        inner = self.endpoint('inner')
        inner.request_cleanup()
        self.assertEqual(self.os.output, [REQUEST])
        outer = self.endpoint('outer')
        self.os.input = [REQUEST]
        self.assertTrue(outer.poll_cleanup_request())
        self.time = 2
        outer.deliver_cleanup_deadline(12)
        self.time = 3
        self.os.input = [self.os.output[-1]]
        self.assertEqual(inner.poll_cleanup_deadline(), 12)  # not 13
        self.assertFalse(inner.report()['qualified'])
        with self.assertRaises(RuntimeError): inner.request_cleanup()
        with self.assertRaises(RuntimeError): outer.deliver_cleanup_deadline(13)
        with self.assertRaises(RuntimeError): inner.poll_cleanup_deadline()

    def test_pending_and_fragmented_request_one_read_per_tick(self):
        outer = self.endpoint('outer')
        self.os.input = [OSError(errno.EINTR, 'interrupted'), b'CLE', b'ANUP1']
        self.assertFalse(outer.poll_cleanup_request())
        self.assertFalse(outer.poll_cleanup_request())
        self.assertTrue(outer.poll_cleanup_request())
        self.assertEqual(len(outer.report()['records']), 3)

    def test_malformed_extra_eof_or_unknown_errno_latches_failure(self):
        for raw in (b'INVALID1', REQUEST + b'x', b'', OSError(errno.EIO, 'io')):
            outer = self.endpoint('outer')
            self.os.input = [raw]
            with self.assertRaises((ValueError, EOFError, OSError)):
                outer.poll_cleanup_request()
            self.assertTrue(outer.failed)
            with self.assertRaises((TimeoutError, RuntimeError)):
                outer.poll_cleanup_request()

    def test_short_uncertain_write_never_retried(self):
        for result, error in ((3, None), (None, errno.EINTR), (None, errno.EAGAIN)):
            self.os = FakeOS()
            inner = self.endpoint('inner')
            self.os.write_result, self.os.write_error = result, error
            with self.assertRaises((ValueError, OSError)): inner.request_cleanup()
            with self.assertRaises(RuntimeError): inner.request_cleanup()
            self.assertEqual(len(self.os.output), 1)
            self.assertFalse(inner.requested)

    def test_bad_expired_or_renewed_grants_refused(self):
        for deadline in (float('nan'), float('inf'), 1, 31, 15):
            self.os = FakeOS()
            inner = self.endpoint('inner')
            inner.request_cleanup()
            self.os.input = [GRANT + struct.pack('!d', deadline)]
            self.time = 3
            with self.assertRaises(ValueError): inner.poll_cleanup_deadline()
            self.time = 1
        outer = self.endpoint('outer')
        with self.assertRaises(RuntimeError): outer.deliver_cleanup_deadline(11)

    def test_flags_identity_and_postread_clock_refuse(self):
        outer = self.endpoint('outer')
        self.os.changed = True
        with self.assertRaises(ValueError): outer.poll_cleanup_request()
        for option in ('blocking', 'inheritable'):
            self.os = FakeOS()
            setattr(self.os, option, True)
            with self.assertRaises(ValueError): self.endpoint('outer')
        self.os = FakeOS()
        outer = self.endpoint('outer')
        self.os.input = [REQUEST]
        original_read = self.os.read
        def late_read(fd, size):
            raw = original_read(fd, size)
            self.time = 20
            return raw
        self.os.read = late_read
        with self.assertRaises(TimeoutError): outer.poll_cleanup_request()
        self.assertEqual(outer.report()['records'][-1]['raw_hex'], REQUEST.hex())

    def test_inner_wait_for_grant_has_frozen_no_renewal_bound(self):
        inner = self.endpoint('inner')
        with self.assertRaises(RuntimeError): inner.poll_cleanup_deadline()
        inner.request_cleanup()
        self.time = 29
        self.assertIsNone(inner.poll_cleanup_deadline())
        self.time = 30
        with self.assertRaises(TimeoutError): inner.poll_cleanup_deadline()
        self.time = 28
        with self.assertRaises(TimeoutError): inner.poll_cleanup_deadline()

    def test_fragmented_grant_and_wrong_magic(self):
        inner = self.endpoint('inner')
        inner.request_cleanup()
        frame = GRANT + struct.pack('!d', 11)
        self.os.input = [frame[:5], frame[5:]]
        self.assertIsNone(inner.poll_cleanup_deadline())
        self.assertEqual(inner.poll_cleanup_deadline(), 11)
        inner = self.endpoint('inner')
        inner.request_cleanup()
        self.os.input = [b'INVALID1' + struct.pack('!d', 11)]
        with self.assertRaises(ValueError): inner.poll_cleanup_deadline()
        self.assertTrue(inner.failed)

    def test_postwrite_deadline_retains_bytes_without_retry(self):
        outer = self.endpoint('outer')
        self.os.input = [REQUEST]
        outer.poll_cleanup_request()
        original_write = self.os.write
        def late_write(fd, raw):
            count = original_write(fd, raw)
            self.time = 11
            return count
        self.os.write = late_write
        with self.assertRaises(TimeoutError): outer.deliver_cleanup_deadline(11)
        row = outer.report()['records'][-1]
        self.assertEqual(row['written'], 16)
        self.assertEqual(row['raw_hex'], (GRANT + struct.pack('!d', 11)).hex())
        with self.assertRaises(RuntimeError): outer.deliver_cleanup_deadline(12)
        self.assertIsNone(outer.deadline)

    def test_pending_read_errno_survives_postcheck_failure(self):
        for error in (errno.EAGAIN, errno.EINTR):
            self.os = FakeOS()
            self.time = 1
            outer = self.endpoint('outer')
            def late_pending(fd, size):
                self.time = 20
                raise OSError(error, 'pending but late')
            self.os.read = late_pending
            with self.assertRaises(TimeoutError): outer.poll_cleanup_request()
            row = outer.report()['records'][-1]
            self.assertEqual(row['read_errno'], error)
            self.assertEqual(row['error'], 'TimeoutError')
            self.assertTrue(outer.failed)

    def test_independent_grant_clock_failure_latches_with_evidence(self):
        outer = self.endpoint('outer')
        self.os.input = [REQUEST]
        outer.poll_cleanup_request()
        self.time = float('nan')
        with self.assertRaises(ValueError): outer.deliver_cleanup_deadline(11)
        self.assertTrue(outer.failed)
        self.assertEqual(outer.report()['records'][-1]['operation'], 'outgoing-grant-validation')
        self.time = 1
        inner = self.endpoint('inner')
        inner.request_cleanup()
        self.os.input = [GRANT + struct.pack('!d', 11)]
        ticks = iter([1, 1, float('nan')])
        inner.clock = lambda: next(ticks)
        with self.assertRaises(ValueError): inner.poll_cleanup_deadline()
        self.assertTrue(inner.failed)
        self.assertEqual(inner.report()['records'][-1]['operation'], 'incoming-grant-validation')


if __name__ == '__main__': unittest.main()
