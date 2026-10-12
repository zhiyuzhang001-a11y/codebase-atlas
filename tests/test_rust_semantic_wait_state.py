"""All native/proc APIs injected; no real libc loading or native signal call."""
import ctypes
import errno
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.rust_semantic_wait_state import NativeWaitState, SigAction, _linux_abi as actual_abi


class Entries:
    def __init__(self, names):
        self.names = names
    def __enter__(self):
        return iter(SimpleNamespace(name=name) for name in self.names)
    def __exit__(self, *args):
        pass


class WaitStateTests(unittest.TestCase):
    def setUp(self):
        abi = patch('scripts.rust_semantic_wait_state._linux_abi', return_value=True)
        abi.start()
        self.addCleanup(abi.stop)
        for target, value in (('sys.platform', 'linux'), ('platform.machine', None)):
            p = patch('scripts.rust_semantic_wait_state.' + target,
                      **({'return_value': 'x86_64'} if value is None else {'new': value}))
            p.start()
            self.addCleanup(p.stop)
        self.info = SimpleNamespace(st_dev=1, st_ino=2, st_uid=1000, st_mode=stat.S_IFDIR)
        self.os = SimpleNamespace(O_RDONLY=0, O_DIRECTORY=65536, O_CLOEXEC=524288,
            O_NOFOLLOW=131072, getpid=Mock(return_value=10), getuid=Mock(return_value=1000),
            open=Mock(return_value=40), fstat=Mock(side_effect=lambda fd: self.info),
            get_inheritable=Mock(return_value=False), close=Mock(),
            scandir=Mock(side_effect=lambda fd: Entries(['10'])))
        self.libc = SimpleNamespace(gnu_get_libc_version=Mock(return_value=b'2.39'),
                                   sigaction=Mock(return_value=0))
        self.clock = Mock(side_effect=[1, 1.1])
        self.owner = NativeWaitState(self.libc, self.os, self.clock)

    def test_native_readonly_double_sample_no_sole_waiter_claim(self):
        result = self.owner.measure()
        self.assertNotIn('sole_waiter', result)
        self.assertEqual(result['threads'], 1)
        self.assertEqual(self.libc.sigaction.call_count, 2)
        for call in self.libc.sigaction.call_args_list:
            self.assertEqual(call.args[:2], (17, None))
        self.os.close.assert_called_once_with(40)
        self.assertEqual(len(self.owner.report()['samples'][0]['samples']), 2)
        self.assertFalse(self.owner.report()['qualified'])
        with self.assertRaises(RuntimeError):
            self.owner.measure()
        self.os.open.assert_called_once()

    def test_multiple_threads_or_wrong_tid_rejected_and_bounded(self):
        for names in (['10', '11', '12'], ['11'], []):
            with self.subTest(names=names):
                self.setUp()
                self.os.scandir.side_effect = lambda fd: Entries(names)
                with self.assertRaises(ValueError):
                    self.owner.measure()
                recorded = self.owner.samples[0]['samples'][0]['threads']
                self.assertLessEqual(len(recorded), 2)
                self.os.close.assert_called_once_with(40)

    def test_ignored_custom_handler_or_no_cldwait_rejected(self):
        for handler, flags in ((1, 0), (1234, 0), (None, 2)):
            with self.subTest(handler=handler, flags=flags):
                self.setUp()
                def action(sig, new, old):
                    old._obj.handler, old._obj.flags = handler, flags
                    return 0
                self.libc.sigaction.side_effect = action
                with self.assertRaises(ValueError):
                    self.owner.measure()
                self.assertEqual(self.owner.samples[0]['samples'][0]['flags'], flags)

    def test_errno_is_preserved_without_fallback(self):
        def failure(*args):
            ctypes.set_errno(errno.EINVAL)
            return -1
        self.libc.sigaction.side_effect = failure
        with self.assertRaises(OSError):
            self.owner.measure()
        self.assertEqual(self.owner.errors[0]['errno'], errno.EINVAL)
        self.os.close.assert_called_once_with(40)

    def test_unreviewed_libc_or_platform_rejected(self):
        self.libc.gnu_get_libc_version.return_value = b'2.40'
        with self.assertRaises(ValueError):
            self.owner.measure()
        self.os.open.assert_not_called()
        with patch('scripts.rust_semantic_wait_state._linux_abi', return_value=False):
            with self.assertRaises(ValueError):
                NativeWaitState(self.libc, self.os, self.clock)

    def test_real_abi_guard_rejects_llp64_without_relaxing_native_layout(self):
        size = ctypes.sizeof
        self.assertEqual(size(SigAction), 152)
        with patch('scripts.rust_semantic_wait_state.ctypes.sizeof',
                   side_effect=lambda value: 4 if value is ctypes.c_ulong else size(value)):
            self.assertFalse(actual_abi())
        with patch('scripts.rust_semantic_wait_state.platform.machine', return_value='aarch64'):
            self.assertFalse(actual_abi())

    def test_identity_clock_and_uncertain_close_fail_closed(self):
        for cause in ('inode', 'late', 'nan', 'close'):
            with self.subTest(cause=cause):
                self.setUp()
                if cause == 'inode':
                    self.os.fstat.side_effect = [self.info, SimpleNamespace(
                        st_dev=1, st_ino=3, st_uid=1000, st_mode=stat.S_IFDIR)]
                elif cause == 'late':
                    self.clock.side_effect = [1, 1.51]
                elif cause == 'nan':
                    self.clock.side_effect = [1, float('nan')]
                else:
                    self.os.close.side_effect = OSError(errno.EINTR, 'uncertain')
                with self.assertRaises((ValueError, OSError)):
                    self.owner.measure()
                self.os.close.assert_called_once_with(40)
                self.assertFalse(self.owner.report()['qualified'])
                if cause == 'close':
                    self.assertEqual(self.owner.errors[0]['operation'], 'close')


if __name__ == '__main__':
    unittest.main()
