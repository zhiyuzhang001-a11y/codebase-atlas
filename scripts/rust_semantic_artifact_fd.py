"""Prepared bounded borrowed artifact FD reader; no evaluation or execution.

Caller must create/freeze the owned artifact, authenticate its reviewed source
manifest and exclusively own this open-file description. Hashes are byte
identity only. This reader never opens, seeks to another offset or closes FDs.
"""
import copy
import hashlib
import math
import os
import stat
import sys
import time


class ArtifactFD:
    def __init__(self, fd, expected, source_sha, active_deadline, *,
                 os_api=None, fcntl_api=None, clock=None):
        keys = {'device', 'inode', 'uid', 'size', 'mtime_ns', 'ctime_ns', 'sha256'}
        if (sys.platform != 'linux' or type(fd) is not int or not 3 <= fd <= 65535
                or type(expected) is not dict or set(expected) != keys
                or any(type(expected[k]) is not int or expected[k] < 0
                       for k in keys - {'sha256'})
                or expected['inode'] == 0 or not 0 < expected['size'] <= 512 * 1024
                or type(expected['sha256']) is not str or len(expected['sha256']) != 64
                or any(c not in '0123456789abcdef' for c in expected['sha256'])
                or type(source_sha) is not str or len(source_sha) != 40
                or any(c not in '0123456789abcdef' for c in source_sha)):
            raise ValueError('bounded frozen artifact/commit/borrowed FD receipts required')
        self.os = os if os_api is None else os_api
        if fcntl_api is None:
            import fcntl  # Linux native adapter only; mock imports remain portable.
            fcntl_api = fcntl
        self.fcntl = fcntl_api
        self.clock = time.monotonic if clock is None else clock
        self.fd, self.expected, self.sha = fd, dict(expected), source_sha
        self.deadline, self.pid, self.last = active_deadline, self.os.getpid(), None
        self.started, self.row = False, {}
        now = self._now()
        if active_deadline - now > 20:
            raise ValueError('remaining shared active deadline <=20s required')

    def _now(self):
        now = self.clock()
        if (self.os.getpid() != self.pid
                or any(type(v) not in {int, float} or not math.isfinite(v)
                       or not 0 <= v <= 2**40 for v in (now, self.deadline))
                or now >= self.deadline or (self.last is not None and now < self.last)):
            raise ValueError('same controller and remaining monotonic deadline required')
        self.last = now
        return now

    def _held(self, offset):
        info = self.os.fstat(self.fd)
        actual = dict(device=info.st_dev, inode=info.st_ino, uid=info.st_uid,
                      size=info.st_size, mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns)
        flags = self.fcntl.fcntl(self.fd, self.fcntl.F_GETFL)
        fd_flags = self.fcntl.fcntl(self.fd, self.fcntl.F_GETFD)
        position = self.os.lseek(self.fd, 0, self.os.SEEK_CUR)  # inspect only
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.os.getuid()
                or info.st_mode & 0o222 or info.st_nlink != 1
                or actual != {k: v for k, v in self.expected.items() if k != 'sha256'}
                or type(flags) is not int or flags & self.os.O_ACCMODE != self.os.O_RDONLY
                or not flags & self.os.O_NONBLOCK or type(fd_flags) is not int
                or not fd_flags & self.fcntl.FD_CLOEXEC
                or type(position) is not int or position != offset):
            raise ValueError('unchanged read-only frozen regular CLOEXEC artifact FD required')
        return dict(actual, offset=position)

    def read(self):
        if self.started:
            raise RuntimeError('one artifact read attempt; no uncertain retry')
        self.started = True
        try:
            self.row['started'] = self._now()
            self.row['before'] = self._held(0)
            raw = self.os.read(self.fd, 512 * 1024 + 1)
            if type(raw) is not bytes:
                raise ValueError('raw artifact bytes required')
            self.row.update(read_bytes=len(raw), raw_hex=raw[:512 * 1024].hex(),
                            omitted=max(0, len(raw) - 512 * 1024),
                            sha256=hashlib.sha256(raw).hexdigest())
            if len(raw) != self.expected['size'] or self.row['sha256'] != self.expected['sha256']:
                raise ValueError('exact frozen artifact size/hash required')
            raw.decode('utf-8', 'strict')
            self.row['after'] = self._held(len(raw))
            self.row['finished'] = self._now()
            if self.row['finished'] - self.row['started'] > .5:
                raise ValueError('artifact packet exceeded .5s')
            return raw
        except Exception as exc:
            self.row.update(error=type(exc).__name__, errno=getattr(exc, 'errno', None))
            raise

    def report(self):
        return copy.deepcopy(dict(qualified=False, source_authenticated=False,
                                  source_policy_audited=False, source_sha=self.sha,
                                  borrowed_fd=self.fd, expected=self.expected, sample=self.row))
