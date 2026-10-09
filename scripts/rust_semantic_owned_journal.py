"""Prepared dedicated journal FD owner; no launcher or peer authentication.

Only allocate() creates objects under a reviewed caller. The single-threaded
controller must prevent descriptor replacement, authenticate source/peer and
close unintended inherited endpoints. Tests inject all OS calls; no CLI.
"""
import copy
import math
import os
import stat
import sys
import time


class OwnedJournalPipe:
    def __init__(self, active_deadline, *, os_api=None, fcntl_api=None, clock=None):
        if sys.platform != 'linux':
            raise ValueError('Linux dedicated journal owner required')
        if fcntl_api is None:
            import fcntl
            fcntl_api = fcntl
        self.os = os if os_api is None else os_api
        self.fcntl = fcntl_api
        self.clock = time.monotonic if clock is None else clock
        self.deadline, self.last = active_deadline, None
        self.controller = self.os.getpid()
        self.started = self.ready = False
        self.owned, self.receipt, self.errors = {}, {}, []
        now = self._now()
        if active_deadline - now > 20:
            raise ValueError('shared active deadline <=20s required')

    def _now(self):
        now = self.clock()
        if (self.os.getpid() != self.controller
                or any(type(v) not in (int, float) or not math.isfinite(v)
                       or not 0 <= v <= 2**40 for v in (now, self.deadline))
                or (self.last is not None and now < self.last) or now >= self.deadline):
            raise ValueError('same controller and unexhausted monotonic deadline required')
        self.last = now
        return now

    def _object(self, fd):
        info = self.os.fstat(fd)
        return dict(device=info.st_dev, inode=info.st_ino, uid=info.st_uid,
                    type=stat.S_IFMT(info.st_mode))

    def _identity(self, fd, writer):
        identity = self._object(fd)
        flags = self.fcntl.fcntl(fd, self.fcntl.F_GETFL)
        mode = self.os.O_WRONLY if writer else self.os.O_RDONLY
        if (identity['type'] != stat.S_IFIFO or identity['uid'] != self.os.getuid()
                or any(type(v) is not int or v < 0 for v in identity.values())
                or identity['inode'] == 0 or self.os.get_inheritable(fd)
                or flags & self.os.O_ACCMODE != mode or not flags & self.os.O_NONBLOCK):
            raise ValueError('created CLOEXEC nonblocking journal direction required')
        return identity

    def allocate(self):
        if self.started:
            raise RuntimeError('one allocation attempt; no uncertain FD reopening')
        self.started = True
        try:
            begin = self._now()
            pair = self.os.pipe2(self.os.O_CLOEXEC | self.os.O_NONBLOCK)
            self.receipt['pair'] = copy.deepcopy(pair)
            if type(pair) is not tuple or len(pair) != 2:
                raise ValueError('native pipe2 pair required')
            # Register ALL usable returned descriptors before any rejection.
            # Malformed values never authorize closing arbitrary objects.
            for fd in pair:
                if type(fd) is int and fd >= 0:
                    self.owned.setdefault(fd, None)
            if (any(type(fd) is not int or not 3 <= fd <= 65535 for fd in pair)
                    or pair[0] == pair[1]):
                raise ValueError('two distinct created descriptors within bounds required')
            read, write = pair
            left = self.owned[read] = self._identity(read, False)
            right = self.owned[write] = self._identity(write, True)
            if left != right:
                raise ValueError('both dedicated endpoints must have same pipe identity')
            self.receipt['identity'] = {k: left[k] for k in ('device', 'inode', 'uid')}
            self.ready = True
            borrowed = self.borrow()
            end = self._now()
            self.receipt.update(started=begin, finished=end)
            if end - begin > .5:
                raise ValueError('journal allocation exceeded .5s')
            return borrowed
        except Exception as exc:
            self.errors.append(dict(operation='allocate', error=type(exc).__name__,
                                    errno=getattr(exc, 'errno', None)))
            self.close_all()
            raise

    def borrow(self):
        if not self.ready or self.os.getpid() != self.controller:
            raise RuntimeError('same-controller complete owned journal pair required')
        read, write = self.receipt['pair']
        for fd, writer in ((read, False), (write, True)):
            if fd not in self.owned or self._identity(fd, writer) != self.owned[fd]:
                raise ValueError('journal descriptor retired or replaced')
        return dict(read_fd=read, write_fd=write, expected=dict(self.receipt['identity']))

    def _close(self, descriptors):
        if self.os.getpid() != self.controller:
            raise RuntimeError('only creating controller may close journal FDs')
        for fd in descriptors:
            if fd not in self.owned:
                continue
            identity = self.owned.pop(fd)  # even uncertain close is never retried
            try:
                if identity is not None and self._object(fd) != identity:
                    raise ValueError('replaced journal FD; refuse foreign close')
                self.os.close(fd)
            except (OSError, ValueError) as exc:
                self.errors.append(dict(operation='close', fd=fd,
                                        error=type(exc).__name__, errno=getattr(exc, 'errno', None)))

    def close_parent_writer(self):
        """Only AFTER reviewed spawn inheritance; not evidence of observer exit."""
        pair = self.receipt.get('pair')
        if type(pair) is tuple and len(pair) == 2:
            self._close([pair[1]])

    def close_all(self):
        self.ready = False
        self._close(list(self.owned))

    def report(self):
        return copy.deepcopy(dict(qualified=False, source_authenticated=False,
                                  outer_cleanup_complete=False, ready=self.ready,
                                  controller=self.controller, owned_fds=sorted(self.owned),
                                  receipt=self.receipt, errors=self.errors))
