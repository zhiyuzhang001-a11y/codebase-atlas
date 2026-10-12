"""Prepared borrowed dedicated journal pipe adapter, not peer authentication.

Reviewed single-threaded owner must create the pipe, freeze expected inode,
close every unintended inherited endpoint and authenticate observer/source.
No allocation, close, launch, inheritance changes or signal operations here.
Pre/post FD checks cannot defeat concurrent FD replacement by another thread.
"""
import copy
import errno
import math
import os
import stat
import sys
import time

from scripts.rust_semantic_control_journal import ControlJournal, FRAME


class JournalPipe:
    def __init__(self, role, fd, expected, controller, observer, active_deadline, *,
                 os_api=None, fcntl_api=None, clock=None):
        if (sys.platform != 'linux' or role not in {'reader', 'writer'}
                or type(fd) is not int or not 3 <= fd <= 65535
                or type(expected) is not dict or set(expected) != {'device', 'inode', 'uid'}
                or any(type(v) is not int or v < 0 for v in expected.values())
                or expected['inode'] == 0):
            raise ValueError('fixed borrowed Linux journal endpoint and owned receipt required')
        if fcntl_api is None:
            import fcntl
            fcntl_api = fcntl
        self.os = os if os_api is None else os_api
        self.fcntl, self.clock = fcntl_api, time.monotonic if clock is None else clock
        self.role, self.fd, self.expected = role, fd, dict(expected)
        self.active_deadline, self.cleanup_deadline = active_deadline, None
        self.last, self.failed, self.ended = None, False, False
        self.records = []
        self.journal = ControlJournal(controller, observer)
        now = self._now(active_deadline)
        if active_deadline - now > 20:
            raise ValueError('remaining fixed active deadline <=20s required')
        self._identity()

    def _now(self, deadline):
        now = self.clock()
        if (any(type(v) not in (int, float) or not math.isfinite(v)
                or not 0 <= v <= 2**40 for v in (now, deadline))
                or (self.last is not None and now < self.last) or now >= deadline):
            raise ValueError('fixed deadline exhausted or clock invalid')
        self.last = now
        return now

    def _identity(self):
        info = self.os.fstat(self.fd)
        flags = self.fcntl.fcntl(self.fd, self.fcntl.F_GETFL)
        mode = self.os.O_RDONLY if self.role == 'reader' else self.os.O_WRONLY
        receipt = dict(device=info.st_dev, inode=info.st_ino, uid=info.st_uid)
        if (not stat.S_ISFIFO(info.st_mode) or info.st_uid != self.os.getuid()
                or receipt != self.expected or flags & self.os.O_ACCMODE != mode
                or not flags & self.os.O_NONBLOCK or self.os.get_inheritable(self.fd)):
            raise ValueError('unchanged owned CLOEXEC nonblocking journal pipe required')
        return receipt

    def begin_cleanup(self, deadline):
        """Reader borrows the outer owner's FIRST shared deadline, never renews."""
        if self.role != 'reader' or self.cleanup_deadline is not None or self.failed:
            raise RuntimeError('one reader cleanup transition only')
        self.cleanup_deadline = deadline  # first attempt retires transition, even failure
        try:
            now = self._now(deadline)
            if deadline - now > 10:
                raise ValueError('shared remaining cleanup deadline <=10s required')
        except Exception:
            self.failed = True
            raise

    def _operation(self, raw=None):
        if self.failed or self.ended or len(self.records) >= 4096:
            raise RuntimeError('failed/ended/bounded journal adapter cannot resume')
        deadline = self.active_deadline if self.cleanup_deadline is None else self.cleanup_deadline
        row = dict(operation=self.role, fd=self.fd, attempted=False, deadline=deadline)
        self.records.append(row)
        try:
            begin = row['started'] = self._now(deadline)
            row['before'] = self._identity()
            if self.role == 'writer':
                if type(raw) is not bytes or len(raw) != FRAME.size:
                    raise ValueError('one exact fixed journal frame required')
                row['raw_hex'] = raw.hex()
                self.journal.feed(raw)  # sequencing committed before one write
                row['attempted'] = True
                result = row['written'] = self.os.write(self.fd, raw)
            else:
                row['attempted'] = True
                try:
                    result = self.os.read(self.fd, FRAME.size * 3 + 1)
                except OSError as exc:
                    row['read_errno'] = exc.errno
                    if exc.errno not in {errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR}:
                        raise
                    result = None  # next tick only, no internal retry
                if result is not None:
                    if type(result) is not bytes or len(result) > FRAME.size * 3 + 1:
                        raise ValueError('bounded raw read result required')
                    row['raw_hex'] = result.hex()
                    if result:
                        self.journal.feed(result)
                    else:
                        self.ended = True
                        self.journal.eof()  # EOF is only byte transport, not exit
            row['after'] = self._identity()
            end = row['finished'] = self._now(deadline)
            if end - begin > .5 or (self.role == 'writer'
                                    and (type(result) is not int or result != FRAME.size)):
                raise ValueError('late operation or partial journal write')
            return result
        except Exception as exc:
            self.failed = True
            row.update(error=type(exc).__name__, errno=getattr(exc, 'errno', None))
            raise

    def write_frame(self, raw):
        if self.role != 'writer':
            raise ValueError('writer endpoint required')
        return self._operation(raw)

    def tick(self):
        if self.role != 'reader':
            raise ValueError('reader endpoint required')
        return self._operation()

    def report(self):
        return dict(qualified=False, source_authenticated=False,
                    outer_cleanup_complete=False, role=self.role, borrowed_fd=self.fd,
                    expected=dict(self.expected), failed=self.failed, eof=self.ended,
                    records=copy.deepcopy(self.records), journal=self.journal.report())
