"""Injected-only libc/OS calls; no process subreaper state is modified."""
import ctypes
import errno
import types
import unittest
from unittest.mock import patch

from scripts.rust_semantic_subreaper import Subreaper, _linux_abi


class Prctl:
    def __init__(self):
        self.calls = []
        self.values = [0, 1]
        self.fail_operation = None
    def __call__(self, operation, arg2, *unused):
        self.calls.append((operation, arg2, unused))
        if operation == self.fail_operation:
            ctypes.set_errno(errno.EINVAL)
            return -1
        if operation == 37:
            ctypes.c_int.from_address(arg2).value = self.values.pop(0)
        return 0


class SubreaperTests(unittest.TestCase):
    def setUp(self):
        guard = patch('scripts.rust_semantic_subreaper._linux_abi')
        guard.start()
        self.addCleanup(guard.stop)
        self.libc = types.SimpleNamespace(prctl=Prctl())
        self.os = types.SimpleNamespace(getpid=lambda: 100)
        self.time = 1
        self.contract = dict(observer=100, threads=1, sigchld_default=True,
                             sa_no_cldwait=False, sole_waiter=True)
    def adapter(self):
        return Subreaper(self.libc, 100, os_api=self.os, clock=lambda: self.time)
    def verify(self): return dict(self.contract)

    def test_single_set_verified_by_paired_get_and_contracts(self):
        adapter = self.adapter()
        self.assertTrue(adapter.arm(self.verify, 20))
        self.assertEqual([row[0] for row in self.libc.prctl.calls], [37, 36, 37])
        self.assertTrue(all(row[2] == (0, 0, 0) for row in self.libc.prctl.calls))
        self.assertEqual([row['output'] for row in adapter.report()['records'] if 'output' in row], [0, 1])
        self.assertEqual(len(adapter.report()['contracts']), 2)
        self.assertFalse(adapter.report()['qualified'])
        with self.assertRaises(RuntimeError): adapter.arm(self.verify, 20)

    def test_missing_or_invalid_wait_contract_prevents_set(self):
        for key, value in [('threads', 2), ('sole_waiter', False), ('sa_no_cldwait', True),
                           ('observer', True), ('sigchld_default', False)]:
            adapter = self.adapter()
            contract = dict(self.contract, **{key: value})
            with self.assertRaises(ValueError): adapter.arm(lambda: contract, 20)
            self.assertFalse(adapter.set_attempted)
        self.assertEqual(self.libc.prctl.calls, [])

    def test_preexisting_or_unconfirmed_subreaper_rejected(self):
        for values in ([1], [0, 0], [0, -1]):
            self.libc.prctl = Prctl()
            self.libc.prctl.values = list(values)
            adapter = self.adapter()
            with self.assertRaises(ValueError): adapter.arm(self.verify, 20)
            self.assertFalse(adapter.prepared)
            self.assertNotIn((36, 0, (0, 0, 0)), self.libc.prctl.calls)

    def test_uncertain_set_errno_preserved_never_retry_or_unset(self):
        self.libc.prctl.fail_operation = 36
        adapter = self.adapter()
        with self.assertRaises(OSError): adapter.arm(self.verify, 20)
        self.assertTrue(adapter.set_attempted)
        self.assertEqual(adapter.report()['records'][-1]['errno'], errno.EINVAL)
        with self.assertRaises(RuntimeError): adapter.arm(self.verify, 20)
        self.assertEqual([row[0] for row in self.libc.prctl.calls], [37, 36])

    def test_late_return_and_changed_post_contract_not_prepared(self):
        original = self.libc.prctl
        def late(*args):
            result = original(*args)
            self.time = 3
            return result
        late.argtypes = late.restype = None
        self.libc.prctl = late
        adapter = self.adapter()
        with self.assertRaises(ValueError): adapter.arm(self.verify, 20)
        self.assertEqual(len(adapter.report()['records']), 1)
        self.time = 1
        self.libc.prctl = Prctl()
        adapter = self.adapter()
        calls = []
        def changing():
            calls.append(True)
            return dict(self.contract, threads=1 if len(calls) == 1 else 2)
        with self.assertRaises(ValueError): adapter.arm(changing, 20)
        self.assertTrue(adapter.set_attempted)
        self.assertFalse(adapter.prepared)

    def test_real_abi_guard_rejects_other_platform_or_llp64(self):
        with patch('scripts.rust_semantic_subreaper.sys.platform', 'darwin'):
            with self.assertRaises(ValueError): _linux_abi()
        real_sizeof = ctypes.sizeof
        with patch('scripts.rust_semantic_subreaper.sys.platform', 'linux'), \
             patch('scripts.rust_semantic_subreaper.platform.machine', return_value='x86_64'), \
             patch('scripts.rust_semantic_subreaper.ctypes.sizeof',
                   side_effect=lambda kind: 4 if kind is ctypes.c_ulong else real_sizeof(kind)):
            with self.assertRaises(ValueError): _linux_abi()

    def test_cleanup_get_measures_current_state_without_setting(self):
        adapter = self.adapter()
        adapter.arm(self.verify, 20)
        self.libc.prctl.values = [1, 1]
        self.assertEqual(adapter.measure(11), {'controller': 100, 'subreaper': True})
        self.assertEqual(adapter.measure(11), {'controller': 100, 'subreaper': True})
        self.assertEqual([c[0] for c in self.libc.prctl.calls], [37, 36, 37, 37, 37])
        self.assertEqual(adapter.records[-1]['phase'], 'cleanup-readonly')

    def test_cleanup_missing_arm_bad_bounds_or_state_fail_closed(self):
        with self.assertRaises(RuntimeError):
            self.adapter().measure(11)
        for deadline, state in ((12, 1), (11, 0), (float('nan'), 1), (True, 1)):
            self.libc.prctl = Prctl()
            adapter = self.adapter()
            adapter.arm(self.verify, 20)
            self.libc.prctl.values = [state]
            with self.assertRaises(ValueError):
                adapter.measure(deadline)
            calls = len(self.libc.prctl.calls)
            with self.assertRaises(RuntimeError):
                adapter.measure(11)
            self.assertEqual(len(self.libc.prctl.calls), calls)

    def test_cleanup_errno_and_late_get_keep_raw_evidence(self):
        adapter = self.adapter()
        adapter.arm(self.verify, 20)
        self.libc.prctl.fail_operation = 37
        with self.assertRaises(OSError):
            adapter.measure(11)
        self.assertEqual(adapter.records[-1]['errno'], errno.EINVAL)
        self.libc.prctl = Prctl()
        adapter = self.adapter()
        adapter.arm(self.verify, 20)
        self.libc.prctl.values = [1]
        original = self.libc.prctl
        def late(*args):
            result = original(*args)
            self.time = 1.5
            return result
        self.libc.prctl = late
        with self.assertRaises(ValueError):
            adapter.measure(11)
        self.assertEqual(adapter.records[-1]['output'], 1)
        self.assertTrue(adapter.measure_failed)


if __name__ == '__main__': unittest.main()
