"""Prepared Linux C0-O5 nonblocking drain; no spawn/close/signal/CLI entry.

FDs are BORROWED from an independently reviewed, single-threaded outer owner.
It must install nonblocking flags, hold their ownership without closing/reuse,
and supervise the tracer independently. This adapter is not that supervisor.
"""
import errno
import os
import stat
import sys


class ControlPipes:
    """One bounded read per stream per tick; raw bytes precede post-read checks."""

    def __init__(self, descriptors, budget, os_api=None, fcntl_api=None):
        if sys.platform != 'linux':
            raise ValueError('Linux control pipes only')
        if (type(descriptors) is not dict or set(descriptors) != budget.STREAMS
                or any(type(fd) is not int or not 3 <= fd <= 65535
                       for fd in descriptors.values())
                or len(set(descriptors.values())) != 3):
            raise ValueError('three distinct owned pipe read FDs required')
        if fcntl_api is None:
            import fcntl
            fcntl_api = fcntl
        self.os = os if os_api is None else os_api
        self.fcntl, self.budget = fcntl_api, budget
        self.descriptors = dict(descriptors)
        self.identities, self.errors = {}, []
        self.ticks = 0
        for name, fd in sorted(self.descriptors.items()):
            identity = self._identity(fd)
            if identity in self.identities.values():
                raise ValueError('aliased stream pipe inode')
            self.identities[name] = identity

    def _identity(self, fd):
        info = self.os.fstat(fd)
        flags = self.fcntl.fcntl(fd, self.fcntl.F_GETFL)
        if (not stat.S_ISFIFO(info.st_mode) or info.st_uid != self.os.getuid()
                or flags & self.os.O_ACCMODE != self.os.O_RDONLY
                or not flags & self.os.O_NONBLOCK
                or self.os.get_inheritable(fd)):
            raise ValueError('owned CLOEXEC nonblocking read pipe required')
        return (info.st_dev, info.st_ino, info.st_uid, stat.S_IFMT(info.st_mode))

    def _check(self, name, fd):
        if self._identity(fd) != self.identities[name]:
            raise ValueError('pipe descriptor identity changed')

    def tick(self, clock, cleanup=False):
        """No wait/select/retry loop. Caller must handle errors and cancellation.

        A single trusted outer thread must own these FDs: pre/post checks do NOT
        prevent another thread replacing one between check and read. No project
        FD, regular file, terminal, socket or blocking descriptor is accepted.
        """
        if type(cleanup) is not bool or self.ticks >= 65536:
            raise ValueError('bounded drain ticks and exact phase required')
        self.ticks += 1
        check_time = self.budget.check_cleanup if cleanup else self.budget.check_active
        count = 0
        for name, fd in sorted(self.descriptors.items()):
            check_time(clock())
            if self.budget.records[name]['eof']:
                continue
            try:
                self._check(name, fd)
                try:
                    raw = self.os.read(fd, 65536)
                except OSError as exc:
                    if exc.errno not in {errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR}:
                        raise
                    raw = None  # retry on a later tick, never renew deadline
                if raw is not None:
                    if type(raw) is not bytes or len(raw) > 65536:
                        raise ValueError('unexpected raw read result')
                    if raw:
                        # Preserve observed bytes even if post-check/deadline fails.
                        self.budget.feed(name, raw)
                        count += len(raw)
                    else:
                        self.budget.eof(name)
                self._check(name, fd)
                check_time(clock())
            except (OSError, ValueError, RuntimeError, OverflowError) as exc:
                if self.budget.failure is None:
                    self.budget.failure = 'pipe-validation-or-read-failed'
                if len(self.errors) < 128:
                    self.errors.append({'stream': name, 'fd': fd,
                                        'error': type(exc).__name__,
                                        'errno': getattr(exc, 'errno', None)})
                raise
        return count

    def report(self):
        return {'qualified': False, 'outer_cleanup_complete': False,
                'borrowed_fds': dict(self.descriptors),
                'pipe_identities': dict(self.identities),
                'ticks': self.ticks, 'errors': list(self.errors)}
