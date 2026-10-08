"""Prepared cancellation for an explicitly owned, unreaped observer group.

Not a launcher, adoption/drain implementation, or executable control entry.
Caller must supply the sole wait owner's borrowed pidfd from a reviewed spawn,
and bind while the fixed observer is stopped before it can create tracees.
The caller must not concurrently reap, close/reuse the pidfd, or mutate handles.
"""
import errno
import math
import os
import stat
import sys
import time


class OwnedGroup:
    def __init__(self, observer_wait, resources, os_api=None, clock=None, evidence=None):
        if evidence is not None and (type(evidence) is not dict or evidence):
            raise ValueError('caller-owned empty evidence sink required')
        self.os = os if os_api is None else os_api
        self.clock = time.monotonic if clock is None else clock
        self.wait, self.resources = observer_wait, resources
        self.fd = None
        self.cancel_attempted = False
        self.records = []
        self.verifications = []
        if evidence is not None:
            # Caller retains partial constructor failure and separate close errors.
            evidence.update(records=self.records, verifications=self.verifications)
        self.pid, self.pidfd = observer_wait.pid, observer_wait.pidfd
        if (sys.platform != 'linux' or type(self.pid) is not int
                or not 1 < self.pid <= 2**31-1 or type(self.pidfd) is not int
                or not 3 <= self.pidfd <= 65535
                or self.pid in {self.os.getpid(), self.os.getpgrp(), self.os.getsid(0)}):
            raise ValueError('distinct owned Linux observer session leader required')
        self.uid = self.os.getuid()
        try:
            self.fd = self.os.open(f'/proc/{self.pid}', self.os.O_RDONLY |
                                   self.os.O_DIRECTORY | self.os.O_CLOEXEC |
                                   self.os.O_NOFOLLOW)
            self.directory = self._directory_identity()
            self.identity = self._verify(initial=True)
        except BaseException:
            try:
                self.close()
            except OSError:
                pass  # close() retains the separate error, preserving binding failure.
            raise

    def _directory_identity(self):
        info = self.os.fstat(self.fd)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != self.uid
                or self.os.get_inheritable(self.fd)):
            raise ValueError('owned CLOEXEC proc directory required')
        return info.st_dev, info.st_ino

    def _pidfd_identity(self, packet):
        info = self.os.fstat(self.pidfd)
        packet['pidfd_before'] = (info.st_dev, info.st_ino)
        if self.os.get_inheritable(self.pidfd):
            raise ValueError('borrowed pidfd must remain CLOEXEC')
        fd = self.os.open(f'/proc/self/fdinfo/{self.pidfd}', self.os.O_RDONLY |
                          self.os.O_CLOEXEC | self.os.O_NOFOLLOW)
        packet['fdinfo_fd'] = fd
        read_failed = False
        try:
            raw = self.os.read(fd, 8193)
            packet['fdinfo_hex'] = raw.hex()
        except BaseException:
            read_failed = True
            raise
        finally:
            try:
                self.os.close(fd)
            except OSError as exc:
                packet['fdinfo_close_errno'] = exc.errno
                if not read_failed:
                    raise
        matches = [line.split(b':', 1)[1].strip() for line in raw.splitlines()
                   if line.startswith(b'Pid:')]
        if not raw or len(raw) > 8192 or matches != [str(self.pid).encode()]:
            raise ValueError('exact unreaped pidfd lifetime required')
        after = self.os.fstat(self.pidfd)
        packet['pidfd_after'] = (after.st_dev, after.st_ino)
        if (info.st_dev, info.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError('borrowed pidfd changed during verification')
        return info.st_dev, info.st_ino

    def _now(self):
        value = self.clock()
        if type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 2**40:
            raise ValueError('finite bounded monotonic time required')
        return value

    def _verify(self, initial=False):
        if len(self.verifications) >= 16:
            raise ValueError('verification evidence bound exceeded')
        packet = {'initial': initial, 'pid': self.pid, 'pidfd': self.pidfd}
        self.verifications.append(packet)
        try:
            return self._verify_packet(initial, packet)
        except BaseException as exc:
            packet['error'] = type(exc).__name__
            if isinstance(exc, OSError):
                packet['errno'] = exc.errno
            raise

    def _verify_packet(self, initial, packet):
        packet['reap_attempted'] = self.wait.reap_attempted
        packet['reaped'] = self.wait.reaped
        if self.fd is None or self.wait.reap_attempted or self.wait.reaped:
            raise ValueError('unreaped sole owner required before group cancellation')
        started = self._now()
        packet['started'] = started
        packet['directory_before'] = self._directory_identity()
        if packet['directory_before'] != self.directory:
            raise ValueError('held proc directory changed')
        pidfd_identity = self._pidfd_identity(packet)
        if initial:
            self.pidfd_identity = pidfd_identity
        elif pidfd_identity != self.pidfd_identity:
            raise ValueError('borrowed pidfd identity changed')
        raw_before = self.resources._read_at(self.fd, 'stat', 8192)
        packet['stat_before_hex'] = raw_before.hex()
        before = self.resources.proc_identity(raw_before)
        packet['stat_before'] = dict(before)
        raw = self.resources._read_at(self.fd, 'status', 8192)
        packet['status_hex'] = raw.hex()
        fields = {}
        for line in raw.splitlines():
            key, separator, value = line.partition(b':')
            if key in {b'Pid', b'Tgid', b'Uid', b'TracerPid'}:
                if not separator or key in fields:
                    raise ValueError('ambiguous observer status')
                fields[key] = value.split()
        expected = {b'Pid': [str(self.pid).encode()], b'Tgid': [str(self.pid).encode()],
                    b'Uid': [str(self.uid).encode()] * 4, b'TracerPid': [b'0']}
        raw_after = self.resources._read_at(self.fd, 'stat', 8192)
        packet['stat_after_hex'] = raw_after.hex()
        after = self.resources.proc_identity(raw_after)
        packet['stat_after'] = dict(after)
        for identity in (before, after):
            if (identity['pid'] != self.pid or identity['ppid'] != self.os.getpid()
                    or identity['session'] != self.pid or identity['pgrp'] != self.pid
                    or identity['starttime'] <= 0 or identity['state'] in {'X', 'x'}
                    or (initial and identity['state'] != 'T')
                    or (not initial and identity['starttime'] != self.identity['starttime'])):
                raise ValueError('observer lifetime or owned session changed')
        ended = self._now()
        packet['ended'] = ended
        packet['directory_after'] = self._directory_identity()
        if (fields != expected or before['starttime'] != after['starttime']
                or packet['directory_after'] != self.directory
                or not started <= ended <= started + .5
                or self.wait.reap_attempted or self.wait.reaped):
            raise ValueError('observer binding evidence incomplete or late')
        packet['verified'] = True
        return after

    def cancel_owned_group(self):
        if self.cancel_attempted:
            raise RuntimeError('group cancellation never retried')
        self.cancel_attempted = True
        row = {'operation': 'cancel', 'group': self.pid, 'signal': 9}
        self.records.append(row)
        self._verify()
        try:
            self.os.killpg(self.pid, 9)
        except OSError as exc:
            row['errno'] = exc.errno
            raise
        row['sent'] = True  # Not proof of terminal, tracee drain or reap.
        return True

    def group_absent_after_reap(self):
        if not self.wait.reaped:
            raise ValueError('verified observer reap required for absence probe')
        if len(self.records) >= 4096:
            raise ValueError('group evidence record bound exceeded')
        row = {'operation': 'absence', 'group': self.pid, 'signal': 0}
        self.records.append(row)
        try:
            self.os.killpg(self.pid, 0)  # Read-only; never kill a potentially reused PGID.
        except OSError as exc:
            row['errno'] = exc.errno
            if exc.errno == errno.ESRCH:
                return True
            raise
        return False

    def close(self):
        if self.fd is not None:
            fd, self.fd = self.fd, None
            try:
                self.os.close(fd)  # Owned directory only; uncertain close is not retried.
            except OSError as exc:
                self.records.append({'operation': 'close', 'fd': fd, 'errno': exc.errno})
                raise

    def report(self):
        return {'qualified': False, 'outer_cleanup_complete': False,
                'cancel_attempted': self.cancel_attempted,
                'verifications': [dict(packet,
                    **{key: dict(value) for key, value in packet.items() if isinstance(value, dict)})
                    for packet in self.verifications],
                'records': [dict(row) for row in self.records]}
