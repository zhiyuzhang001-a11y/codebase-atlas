"""Mock-only native identity reads: no actual proc/FD access or processes."""
import errno
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.rust_semantic_adopted_identity import AdoptedIdentity
from scripts import rust_semantic_linux_resources as resources


class FakeOS:
    O_RDONLY, O_CLOEXEC, O_NOFOLLOW = 0, 2, 4
    def __init__(self):
        self.files, self.opened, self.closed = {}, [], []
        self.next_fd = 40
        self.inodes = {20: 8, 30: 9}
        self.inherit = set()
        self.data = {'stat': proc_stat(), 'status':
                     b'Pid: 300\nTgid: 300\nUid: 501 501 501 501\nTracerPid: 0\n',
                     '/proc/self/fdinfo/30': b'Pid: 300\n'}
        self.read_error = self.close_error = False
    def getpid(self): return 100
    def getuid(self): return 501
    def get_inheritable(self, fd): return fd in self.inherit
    def fstat(self, fd):
        return SimpleNamespace(st_dev=1, st_ino=self.inodes.get(fd, fd), st_uid=501,
                               st_mode=stat.S_IFDIR if fd == 20 else stat.S_IFREG)
    def open(self, path, flags, *, dir_fd=None):
        self.next_fd += 1
        self.files[self.next_fd] = path
        self.opened.append((path, flags, dir_fd))
        return self.next_fd
    def read(self, fd, size):
        if self.read_error: raise OSError(errno.EIO, 'fake read')
        return self.data[self.files[fd]]
    def close(self, fd):
        self.closed.append(fd)
        if self.close_error: raise OSError(errno.EINTR, 'fake close')


def proc_stat(**changes):
    identity = dict(pid=300, state='Z', ppid=100, pgrp=200, session=200, starttime=77)
    identity.update(changes)
    fields = ['0'] * 22
    fields[0] = identity['state']
    for index, key in ((1, 'ppid'), (2, 'pgrp'), (3, 'session'), (19, 'starttime')):
        fields[index] = str(identity[key])
    return (str(identity['pid']) + ' (fixed) ' + ' '.join(fields)).encode()


class AdoptedIdentityTests(unittest.TestCase):
    def setUp(self):
        platform = patch('scripts.rust_semantic_adopted_identity.sys.platform', 'linux')
        platform.start()
        self.addCleanup(platform.stop)
        self.os = FakeOS()
        self.observer = SimpleNamespace(pid=200, terminal={'si_pid': 200},
                                        reap_attempted=True, reaped=True)
        self.binding = dict(pid=300, pidfd=30, uid=501, ppid=100, pgrp=200, session=200,
                            tracer=0, starttime=77, proc_dev=1, proc_ino=8,
                            pidfd_dev=1, pidfd_ino=9)
        self.now = 2
    def make(self, clock=None):
        return AdoptedIdentity(self.observer, self.binding, 20, resources, 12,
                               os_api=self.os, clock=clock or (lambda: self.now))

    def test_zombie_and_live_same_lifetime_raw_packets_not_qualification(self):
        adapter = self.make()
        self.assertEqual(adapter.verify(), self.binding)
        self.os.data['stat'] = proc_stat(state='S')
        self.assertEqual(adapter.verify(), self.binding)
        packet = adapter.report()['packets'][0]
        self.assertTrue(packet['verified'])
        self.assertEqual(packet['stat_before_hex'], proc_stat().hex())
        packet['stat_before']['pid'] = 9
        self.assertEqual(adapter.packets[0]['stat_before']['pid'], 300)
        self.assertEqual(self.os.opened[0], ('stat', 6, 20))
        self.assertNotIn(20, self.os.closed)
        self.assertNotIn(30, self.os.closed)
        self.assertFalse(adapter.report()['outer_cleanup_complete'])

    def test_wrong_parent_lifetime_group_or_tracer_latches(self):
        for changes in ({'ppid': 99}, {'starttime': 78}, {'session': 201},
                        {'pgrp': 201}, {'pid': 301}, {'state': 't'}):
            self.os = FakeOS()
            self.os.data['stat'] = proc_stat(**changes)
            adapter = self.make()
            with self.assertRaises(ValueError): adapter.verify()
            self.assertTrue(adapter.failed)
            calls = len(self.os.opened)
            with self.assertRaises(RuntimeError): adapter.verify()
            self.assertEqual(len(self.os.opened), calls)
        self.os = FakeOS()
        self.os.data['status'] = self.os.data['status'].replace(b'TracerPid: 0', b'TracerPid: 200')
        with self.assertRaises(ValueError): self.make().verify()

    def test_fd_identity_inheritable_changed_and_ambiguous_fdinfo(self):
        for change in ('proc', 'pidfd', 'inherit', 'info', 'status', 'oversize'):
            self.os = FakeOS()
            if change == 'proc': self.os.inodes[20] = 99
            if change == 'pidfd': self.os.inodes[30] = 99
            if change == 'inherit': self.os.inherit.add(30)
            if change == 'info': self.os.data['/proc/self/fdinfo/30'] += b'Pid: 300\n'
            if change == 'status': self.os.data['status'] += b'Pid: 300\n'
            if change == 'oversize': self.os.data['stat'] = b'x' * 8193
            with self.assertRaises(ValueError): self.make().verify()
            self.assertNotIn(20, self.os.closed)
            self.assertNotIn(30, self.os.closed)

    def test_read_and_uncertain_close_errors_preserved_separately(self):
        self.os.read_error = self.os.close_error = True
        adapter = self.make()
        with self.assertRaises(OSError) as caught: adapter.verify()
        self.assertEqual(caught.exception.errno, errno.EIO)
        packet = adapter.packets[0]
        self.assertEqual(packet['stat_before_errno'], errno.EIO)
        self.assertEqual(packet['stat_before_close_errno'], errno.EINTR)
        self.assertEqual(len(self.os.closed), 1)
        with self.assertRaises(RuntimeError): adapter.verify()
        self.assertEqual(len(self.os.closed), 1)

    def test_observer_reap_and_clock_failure_no_identity_acceptance(self):
        for field, value in (('terminal', None), ('reap_attempted', False),
                             ('reaped', False), ('reaped', 1)):
            original = getattr(self.observer, field)
            setattr(self.observer, field, value)
            with self.assertRaises(ValueError): self.make().verify()
            setattr(self.observer, field, original)
        self.assertEqual(self.os.opened, [])
        for end in (1, 3, 12, float('nan')):
            ticks = iter([2, 2, end])
            adapter = self.make(clock=lambda: next(ticks))
            with self.assertRaises(ValueError): adapter.verify()
            self.assertFalse(adapter.packets[0].get('verified', False))

    def test_identity_changes_during_read_and_remaining_bound(self):
        original = self.os.read
        def read(fd, size):
            raw = original(fd, size)
            if self.os.files[fd] == 'status': self.os.data['stat'] = proc_stat(starttime=78)
            return raw
        self.os.read = read
        with self.assertRaises(ValueError): self.make().verify()
        self.now = 1
        with self.assertRaises(ValueError): self.make()
