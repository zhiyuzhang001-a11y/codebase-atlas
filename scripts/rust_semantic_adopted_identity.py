"""Read-only prepared verification of a journal-admitted adopted lifetime.

Not discovery or admission: borrowed proc/pidfd FDs and their frozen identities
must come from a separately reviewed sole owner's lifetime journal. No spawn,
signal, wait, pidfd_open, borrowed-FD close or execution entry. The native wait
state and fixed-source sole-waiter policy remain separate mandatory checks.
"""
import copy
import math
import os
import stat
import sys
import time


class AdoptedIdentity:
    def __init__(self, observer, binding, proc_fd, resources, cleanup_deadline, *,
                 os_api=None, clock=None):
        self.os = os if os_api is None else os_api
        self.clock = time.monotonic if clock is None else clock
        self.observer, self.resources = observer, resources
        self.controller, self.uid = self.os.getpid(), self.os.getuid()
        keys = {'pid', 'pidfd', 'uid', 'ppid', 'session', 'pgrp', 'tracer',
                'starttime', 'proc_dev', 'proc_ino', 'pidfd_dev', 'pidfd_ino'}
        if (sys.platform != 'linux' or type(binding) is not dict or set(binding) != keys
                or any(type(value) is not int or not 0 <= value <= 2**64-1
                       for value in binding.values())
                or not 1 < binding['pid'] <= 2**31-1
                or binding['pid'] in {self.controller, observer.pid}
                or not 3 <= binding['pidfd'] <= 65535
                or type(proc_fd) is not int or not 3 <= proc_fd <= 65535
                or proc_fd == binding['pidfd'] or self.controller <= 1
                or binding['uid'] != self.uid or binding['ppid'] != self.controller
                or binding['session'] != observer.pid or binding['pgrp'] != observer.pid
                or binding['tracer'] != 0 or binding['starttime'] == 0
                or binding['proc_ino'] == 0 or binding['pidfd_ino'] == 0
                or type(cleanup_deadline) not in {int, float}
                or not math.isfinite(cleanup_deadline)
                or not 0 < cleanup_deadline <= 2**40):
            raise ValueError('frozen admitted adopted lifetime and borrowed handles required')
        self.binding, self.fd = dict(binding), proc_fd
        self.deadline, self.last = cleanup_deadline, None
        self.failed, self.packets = False, []
        if self.deadline - self._now() > 10:
            raise ValueError('remaining shared cleanup budget must be at most ten seconds')

    def _now(self):
        now = self.clock()
        if (type(now) not in {int, float} or not math.isfinite(now)
                or not 0 <= now < self.deadline
                or (self.last is not None and now < self.last)
                or self.os.getpid() != self.controller):
            raise ValueError('same controller and remaining monotonic cleanup deadline required')
        self.last = now
        return now

    def _held(self, packet, suffix):
        directory, handle = self.os.fstat(self.fd), self.os.fstat(self.binding['pidfd'])
        packet['directory_' + suffix] = (directory.st_dev, directory.st_ino)
        packet['pidfd_' + suffix] = (handle.st_dev, handle.st_ino)
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != self.uid
                or self.os.get_inheritable(self.fd)
                or self.os.get_inheritable(self.binding['pidfd'])
                or (directory.st_dev, directory.st_ino) !=
                   (self.binding['proc_dev'], self.binding['proc_ino'])
                or (handle.st_dev, handle.st_ino) !=
                   (self.binding['pidfd_dev'], self.binding['pidfd_ino'])):
            raise ValueError('held borrowed proc/pidfd identity changed')

    def _read(self, path, packet, label, *, directory=None):
        flags = self.os.O_RDONLY | self.os.O_CLOEXEC | self.os.O_NOFOLLOW
        fd = self.os.open(path, flags) if directory is None else self.os.open(
            path, flags, dir_fd=directory)
        packet[label + '_fd'] = fd
        primary_failed = False
        try:
            info = self.os.fstat(fd)
            packet[label + '_identity'] = {
                'dev': info.st_dev, 'ino': info.st_ino,
                'uid': info.st_uid, 'mode': info.st_mode}
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.uid
                    or self.os.get_inheritable(fd)):
                raise ValueError('owned CLOEXEC proc sample FD required')
            raw = self.os.read(fd, 8193)
            packet[label + '_hex'] = raw.hex()
            if type(raw) is not bytes or not 0 < len(raw) <= 8192:
                raise ValueError('bounded nonempty proc sample required')
            return raw
        except BaseException as exc:
            primary_failed = True
            packet[label + '_error'] = type(exc).__name__
            packet[label + '_errno'] = getattr(exc, 'errno', None)
            raise
        finally:
            try:
                self.os.close(fd)  # only the newly opened sample; one attempt
            except OSError as exc:
                packet[label + '_close_errno'] = exc.errno
                if not primary_failed:
                    raise

    def verify(self):
        if self.failed:
            raise RuntimeError('failed lifetime verifier cannot reuse authority')
        if len(self.packets) >= 16:
            self.failed = True
            raise ValueError('adopted identity packet bound exceeded')
        packet = {'pid': self.binding['pid'], 'pidfd': self.binding['pidfd'],
                  'borrowed_proc_fd': self.fd}
        self.packets.append(packet)
        try:
            begin = packet['started'] = self._now()
            if (self.observer.terminal is None or self.observer.reap_attempted is not True
                    or self.observer.reaped is not True):
                raise ValueError('exactly consumed observer required before adopted drain')
            self._held(packet, 'before')
            raw = self._read('stat', packet, 'stat_before', directory=self.fd)
            before = packet['stat_before'] = self.resources.proc_identity(raw)
            status = self._read('status', packet, 'status', directory=self.fd)
            fields = {}
            for line in status.splitlines():
                key, sep, value = line.partition(b':')
                if key in {b'Pid', b'Tgid', b'Uid', b'TracerPid'}:
                    if not sep or key in fields:
                        raise ValueError('ambiguous adopted process status')
                    fields[key] = value.split()
            expected = {b'Pid': [str(self.binding['pid']).encode()],
                        b'Tgid': [str(self.binding['pid']).encode()],
                        b'Uid': [str(self.uid).encode()] * 4, b'TracerPid': [b'0']}
            info = self._read(f"/proc/self/fdinfo/{self.binding['pidfd']}",
                              packet, 'fdinfo')
            matches = [line.partition(b':')[2].split() for line in info.splitlines()
                       if line.partition(b':')[0] == b'Pid']
            raw = self._read('stat', packet, 'stat_after', directory=self.fd)
            after = packet['stat_after'] = self.resources.proc_identity(raw)
            for identity in (before, after):
                if (any(type(identity[key]) is not int or identity[key] != self.binding[key]
                        for key in ('pid', 'ppid', 'session', 'pgrp', 'starttime'))
                        or identity['state'] in {'X', 'x', 't'}):
                    raise ValueError('same adopted parent/session/lifetime required')
            self._held(packet, 'after')
            end = packet['finished'] = self._now()
            if (fields != expected or matches != [expected[b'Pid']]
                    or not begin <= end <= begin + .5
                    or self.observer.terminal is None
                    or self.observer.reap_attempted is not True or self.observer.reaped is not True):
                raise ValueError('adopted identity evidence incomplete or late')
            packet['verified'] = True
            return dict(self.binding)  # NOT thread/wait policy or journal proof
        except BaseException as exc:
            self.failed = True
            packet.update(error=type(exc).__name__, errno=getattr(exc, 'errno', None))
            raise

    def report(self):
        return {'qualified': False, 'outer_cleanup_complete': False,
                'failed': self.failed, 'binding': dict(self.binding),
                'packets': copy.deepcopy(self.packets)}
