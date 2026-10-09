"""Prepared same-FD read of one frozen controller sibling; never execution.

The reviewed caller must freeze the exact clean PR commit and owned-directory/
file receipts. Matching hashes are byte identity, not provenance or source-policy
approval. No project paths, imports, eval, spawn, signals or borrowed-FD close.
"""
import copy
import hashlib
import math
import os
import stat
import sys
import time


SOURCE_NAMES = frozenset('rust_semantic_' + name + '.py' for name in (
    'subreaper', 'owned_group', 'control_pipes', 'control_journal', 'journal_pipe',
    'terminal_census', 'cleanup_ipc', 'wait_state', 'ptrace', 'control_budget',
    'adopted_identity', 'adopted_wait', 'terminal_admission', 'cleanup_policy',
    'outer_control', 'owned_journal', 'control_observer', 'outer_pipes',
    'root_launch', 'control_code', 'linux_resources', 'source_file', 'observer_wait'))


class SourceFile:
    def __init__(self, name, directory_fd, directory, expected, source_sha,
                 active_deadline, *, os_api=None, clock=None):
        keys = {'device', 'inode', 'uid', 'size', 'mtime_ns', 'ctime_ns', 'sha256'}
        if (sys.platform != 'linux' or type(name) is not str or name not in SOURCE_NAMES
                or type(directory_fd) is not int or not 3 <= directory_fd <= 65535
                or type(directory) is not dict or set(directory) != {'device', 'inode', 'uid'}
                or any(type(v) is not int or v < 0 for v in directory.values())
                or directory['inode'] == 0 or type(expected) is not dict or set(expected) != keys
                or any(type(expected[k]) is not int or expected[k] < 0 for k in keys - {'sha256'})
                or expected['inode'] == 0 or not 0 < expected['size'] <= 65536
                or type(expected['sha256']) is not str or len(expected['sha256']) != 64
                or any(c not in '0123456789abcdef' for c in expected['sha256'])
                or type(source_sha) is not str or len(source_sha) != 40
                or any(c not in '0123456789abcdef' for c in source_sha)):
            raise ValueError('exact frozen sibling/commit/owned FD receipts required')
        self.os = os if os_api is None else os_api
        self.clock = time.monotonic if clock is None else clock
        self.name, self.fd = name, directory_fd
        self.directory, self.expected = dict(directory), dict(expected)
        self.sha, self.deadline = source_sha, active_deadline
        self.pid, self.last = self.os.getpid(), None
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
            raise ValueError('same controller and remaining shared monotonic deadline required')
        self.last = now
        return now

    def _held(self):
        info = self.os.fstat(self.fd)
        actual = dict(device=info.st_dev, inode=info.st_ino, uid=info.st_uid)
        if (not stat.S_ISDIR(info.st_mode) or actual != self.directory
                or info.st_uid != self.os.getuid() or info.st_mode & 0o022
                or self.os.get_inheritable(self.fd)):
            raise ValueError('unchanged owned non-writable CLOEXEC source directory required')
        return actual

    def _file(self, info):
        actual = dict(device=info.st_dev, inode=info.st_ino, uid=info.st_uid,
                      size=info.st_size, mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.os.getuid()
                or info.st_mode & 0o022 or info.st_nlink != 1
                or actual != {k: v for k, v in self.expected.items() if k != 'sha256'}):
            raise ValueError('unchanged non-writable regular frozen source file required')
        return actual

    def read(self):
        if self.started:
            raise RuntimeError('one source read attempt; no uncertain reopen')
        self.started = True
        owned, primary = None, False
        try:
            self.row['started'] = self._now()
            self.row['directory_before'] = self._held()
            flags = self.os.O_RDONLY | self.os.O_CLOEXEC | self.os.O_NOFOLLOW | self.os.O_NONBLOCK
            owned = self.os.open(self.name, flags, dir_fd=self.fd)
            self.row['opened_fd'] = owned  # ownership before bounds/identity checks
            if type(owned) is not int or owned < 0 or owned == self.fd:
                owned = None  # invalid result cannot authorize foreign/borrowed close
                raise ValueError('distinct native created descriptor required')
            if not 3 <= owned <= 65535:
                raise ValueError('source FD outside frozen bounds')
            self.row['before'] = self._file(self.os.fstat(owned))
            if self.os.get_inheritable(owned):
                raise ValueError('source file CLOEXEC required')
            raw = self.os.read(owned, 65537)  # one bounded read, no internal retry
            if type(raw) is not bytes:
                raise ValueError('raw source bytes required')
            self.row.update(read_bytes=len(raw), raw_hex=raw[:65536].hex(),
                            omitted=max(0, len(raw) - 65536), sha256=hashlib.sha256(raw).hexdigest())
            if len(raw) != self.expected['size'] or self.row['sha256'] != self.expected['sha256']:
                raise ValueError('exact frozen source size/hash required')
            raw.decode('utf-8', 'strict')
            self.row['after'] = self._file(self.os.fstat(owned))
            self.row['path_after'] = self._file(self.os.stat(
                self.name, dir_fd=self.fd, follow_symlinks=False))
            self.row['directory_after'] = self._held()
            fd, owned = owned, None  # retire BEFORE even a successful-path close
            try:
                self.os.close(fd)
            except OSError as exc:
                self.row['close_errno'] = exc.errno
                raise
            self.row['finished'] = self._now()
            if self.row['finished'] - self.row['started'] > .5:
                raise ValueError('source sample exceeded .5s')
            return raw
        except Exception as exc:
            primary = True
            self.row.update(error=type(exc).__name__, errno=getattr(exc, 'errno', None))
            raise
        finally:
            if owned is not None:
                fd, owned = owned, None  # retire before once-only close
                try:
                    self.os.close(fd)
                except OSError as exc:
                    self.row['close_errno'] = exc.errno
                    if not primary:
                        raise

    def report(self):
        return copy.deepcopy(dict(qualified=False, source_authenticated=False,
                                  source_policy_audited=False, source_sha=self.sha,
                                  name=self.name, borrowed_directory_fd=self.fd,
                                  expected=self.expected, sample=self.row))
