"""Prepared fixed cleanup handshake over two borrowed nonblocking Linux pipes.

No allocation/spawn/close/CLI. Reviewed bootstrap must close unused endpoints,
bind peer/FD provenance and keep sole ownership without FD reuse. Observer may
not begin cleanup until it receives the outer owner's absolute deadline.
"""
import errno
import math
import os
import stat
import struct
import sys
import time

REQUEST = b'CLEANUP1'
GRANT = b'DEADLIN1'


class CleanupIPC:
    def __init__(self, role, read_fd, write_fd, active_deadline, *,
                 os_api=None, fcntl_api=None, clock=None):
        if (sys.platform != 'linux' or role not in {'outer', 'inner'}
                or any(type(fd) is not int or not 3 <= fd <= 65535
                       for fd in (read_fd, write_fd)) or read_fd == write_fd):
            raise ValueError('fixed role and distinct borrowed Linux pipe endpoints required')
        if fcntl_api is None:
            import fcntl
            fcntl_api = fcntl
        self.os = os if os_api is None else os_api
        self.fcntl = fcntl_api
        self.clock = time.monotonic if clock is None else clock
        self.role, self.read_fd, self.write_fd = role, read_fd, write_fd
        self.last_time = None
        now = self._now()
        if (type(active_deadline) not in {int, float} or not math.isfinite(active_deadline)
                or not now < active_deadline <= min(now + 20, 2**40 - 10)):
            raise ValueError('frozen outer active deadline required')
        self.active_deadline = active_deadline
        self.identities = {read_fd: self._identity(read_fd, self.os.O_RDONLY),
                           write_fd: self._identity(write_fd, self.os.O_WRONLY)}
        if self.identities[read_fd] == self.identities[write_fd]:
            raise ValueError('request and grant must use distinct pipe objects')
        self.buffer = b''
        self.requested = self.send_attempted = self.failed = False
        self.deadline = None
        self.records = []

    def _now(self):
        now = self.clock()
        if (type(now) not in {int, float} or not math.isfinite(now) or not 0 <= now <= 2**40
                or (self.last_time is not None and now < self.last_time)):
            raise ValueError('finite nondecreasing clock required')
        self.last_time = now
        return now

    def _identity(self, fd, mode):
        info = self.os.fstat(fd)
        flags = self.fcntl.fcntl(fd, self.fcntl.F_GETFL)
        if (not stat.S_ISFIFO(info.st_mode) or info.st_uid != self.os.getuid()
                or flags & self.os.O_ACCMODE != mode or not flags & self.os.O_NONBLOCK
                or self.os.get_inheritable(fd)):
            raise ValueError('owned CLOEXEC nonblocking pipe mode required')
        return info.st_dev, info.st_ino, info.st_uid

    def _check(self, fd, mode, deadline):
        if self.failed or not self._now() < deadline:
            raise TimeoutError('IPC failed or fixed deadline exhausted')
        if self._identity(fd, mode) != self.identities[fd]:
            raise ValueError('borrowed IPC pipe identity changed')

    def _operation(self, direction, size, deadline, payload=None):
        if len(self.records) >= 4096:
            self.failed = True
            raise ValueError('IPC record bound exhausted')
        fd = self.read_fd if direction == 'read' else self.write_fd
        mode = self.os.O_RDONLY if direction == 'read' else self.os.O_WRONLY
        row = {'operation': direction, 'fd': fd, 'deadline': deadline}
        self.records.append(row)
        try:
            self._check(fd, mode, deadline)
            if direction == 'read':
                try:
                    raw = self.os.read(fd, size)
                except OSError as exc:
                    row['read_errno'] = exc.errno
                    if exc.errno not in {errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR}:
                        raise
                    raw = None  # next outer tick only; no internal retry or new grace
                if raw is not None:
                    if type(raw) is not bytes or len(raw) > size:
                        raise ValueError('bounded raw IPC bytes required')
                    row['raw_hex'] = raw.hex()  # preserve before post-read checks
                    if not raw:
                        raise EOFError('IPC peer closed before complete handshake')
            else:
                row['raw_hex'] = payload.hex()
                raw = self.os.write(fd, payload)
                row['written'] = raw
                if type(raw) is not int or raw != len(payload):
                    raise ValueError('single atomic control write incomplete')
            self._check(fd, mode, deadline)
            return raw
        except Exception as exc:
            self.failed = True
            row['error'], row['errno'] = type(exc).__name__, getattr(exc, 'errno', None)
            raise

    def _receive(self, size, deadline):
        raw = self._operation('read', size + 1, deadline)
        if raw is None:
            return None
        self.buffer += raw
        if len(self.buffer) > size:
            self.failed = True
            raise ValueError('extra/duplicate IPC frame bytes')
        return self.buffer if len(self.buffer) == size else None

    def request_cleanup(self):
        if self.role != 'inner' or self.send_attempted:
            raise RuntimeError('one inner cleanup request only')
        self.send_attempted = True
        self._operation('write', 8, self.active_deadline + 10, REQUEST)
        self.requested = True

    def poll_cleanup_request(self):
        if self.role != 'outer' or self.requested:
            raise RuntimeError('outer request may be consumed only once')
        raw = self._receive(8, self.active_deadline)
        if raw is None:
            return False
        if raw != REQUEST:
            self.failed = True
            raise ValueError('fixed cleanup request required')
        self.requested = True
        return True

    def deliver_cleanup_deadline(self, deadline):
        if self.role != 'outer' or not self.requested or self.send_attempted:
            raise RuntimeError('one outer grant after verified request only')
        self.send_attempted = True
        try:
            now = self._now()
            if (type(deadline) not in {int, float} or not math.isfinite(deadline)
                    or not now < deadline <= min(now + 10, self.active_deadline + 10)):
                raise ValueError('remaining outer absolute cleanup deadline required')
        except Exception as exc:
            self._validation_failure('outgoing-grant-validation', exc)
            raise
        self._operation('write', 16, deadline, GRANT + struct.pack('!d', deadline))
        self.deadline = deadline

    def poll_cleanup_deadline(self):
        if self.role != 'inner' or not self.requested or self.deadline is not None:
            raise RuntimeError('one inner grant after successful request only')
        raw = self._receive(16, self.active_deadline + 10)
        if raw is None:
            return None
        deadline = struct.unpack('!d', raw[8:])[0]
        try:
            now = self._now()
            if (raw[:8] != GRANT or not math.isfinite(deadline)
                    or not now < deadline <= min(now + 10, self.active_deadline + 10)):
                raise ValueError('valid remaining outer cleanup grant required')
        except Exception as exc:
            self._validation_failure('incoming-grant-validation', exc)
            raise
        self.deadline = deadline
        return deadline

    def _validation_failure(self, operation, exc):
        self.failed = True
        if len(self.records) < 4096:
            self.records.append({'operation': operation, 'error': type(exc).__name__,
                                 'errno': getattr(exc, 'errno', None)})

    def report(self):
        return {'qualified': False, 'outer_cleanup_complete': False,
                'role': self.role, 'requested': self.requested, 'failed': self.failed,
                'deadline': self.deadline, 'send_attempted': self.send_attempted,
                'pipe_identities': dict(self.identities),
                'records': [dict(row) for row in self.records]}
