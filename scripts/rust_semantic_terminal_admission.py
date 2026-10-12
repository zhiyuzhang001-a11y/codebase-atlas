"""Prepared admission of ONE source-journal-known, still waitable terminal.

Caller must provide the reviewed controller's trusted census/policy and exact
creation/lifetime journal; project data cannot supply either. No launch, signal,
consuming wait or execution entry. Unknown discovery remains incomplete.
"""
import copy
import os
import stat
import time

from scripts.rust_semantic_adopted_identity import AdoptedIdentity
from scripts.rust_semantic_observer_wait import ObserverWait
from scripts.rust_semantic_terminal_census import TerminalCensus


class TerminalAdmission:
    def __init__(self, observer, verifier, resources, cleanup_deadline, *,
                 os_api=None, clock=None):
        self.os = os if os_api is None else os_api
        self.clock = time.monotonic if clock is None else clock
        self.census = TerminalCensus(observer, verifier, cleanup_deadline,
                                     os_api=self.os, clock=self.clock)
        self.observer, self.resources = observer, resources
        self.deadline = cleanup_deadline
        self.attempted = self.admitted = False
        self.owned, self.records = {}, []
        self.identity = self.wait = None

    def _retain(self, name, fd, row):
        row[name] = fd
        if type(fd) is not int or fd < 0 or fd in self.owned.values():
            raise ValueError('distinct newly owned bounded FD required')
        self.owned[name] = fd  # retain before validation/fstat can fail
        if not 3 <= fd <= 65535 or self.os.get_inheritable(fd):
            raise ValueError('new held identity FD must be CLOEXEC')
        return self.os.fstat(fd)

    def admit(self, journal):
        """One attempt; exact source-journal identity precedes pidfd creation.

        Own proc directory + pidfd before returning borrowed identity/wait
        adapters. A nonconsuming P_PIDFD result must match the P_ALL discovery.
        Ownership remains held on failure until explicit close(), never silently
        leaked or retried. Closing a descriptor does NOT prove process cleanup.
        """
        if self.attempted:
            raise RuntimeError('one admission attempt only')
        self.attempted = True
        row = {}
        self.records.append(row)
        try:
            begin = row['started'] = self.census._now()
            if (type(journal) is not dict or set(journal) != {'pid', 'starttime', 'session', 'pgrp'}
                    or any(type(v) is not int or not 0 < v <= 2**64-1 for v in journal.values())
                    or not 1 < journal['pid'] <= 2**31-1
                    or journal['pid'] in {self.census.controller, self.observer.pid}
                    or journal['session'] != self.observer.pid
                    or journal['pgrp'] != self.observer.pid):
                raise ValueError('exact trusted creation/lifetime journal required')
            row['journal'] = dict(journal)
            discovery = row['discovery'] = self.census.tick()
            if (discovery['outcome'] != 'unadmitted_terminal'
                    or discovery['result']['si_pid'] != journal['pid']):
                raise ValueError('discovery must match existing journal, never admit unknown PID')
            self.census._verify(row)
            directory = self.os.open(f"/proc/{journal['pid']}", self.os.O_RDONLY |
                                     self.os.O_DIRECTORY | self.os.O_CLOEXEC | self.os.O_NOFOLLOW)
            proc = self._retain('proc_fd', directory, row)
            if not stat.S_ISDIR(proc.st_mode) or proc.st_uid != self.census.uid:
                raise ValueError('owned proc directory required')
            # Recheck measured/source wait policy before opening the numeric PID.
            # Sole non-auto-reaping waiter + held WNOWAIT terminal prevents reuse;
            # the post-open native verifier still checks exact starttime/FD identity.
            self.census._verify(row)
            fd = self.os.pidfd_open(journal['pid'], 0)
            handle = self._retain('pidfd', fd, row)
            binding = dict(pid=journal['pid'], pidfd=fd, uid=self.census.uid,
                           ppid=self.census.controller, session=journal['session'],
                           pgrp=journal['pgrp'], tracer=0, starttime=journal['starttime'],
                           proc_dev=proc.st_dev, proc_ino=proc.st_ino,
                           pidfd_dev=handle.st_dev, pidfd_ino=handle.st_ino)
            row['binding'] = dict(binding)
            self.identity = AdoptedIdentity(self.observer, binding, directory,
                                           self.resources, self.deadline,
                                           os_api=self.os, clock=self.clock)
            self.identity.verify()
            self.wait = ObserverWait(journal['pid'], fd, self.os)
            terminal = row['pidfd_terminal'] = self.wait.poll()
            if terminal != discovery['result']:
                raise ValueError('held PIDFD terminal must match census exactly')
            self.census._verify(row)
            self.identity.verify()
            end = row['finished'] = self.census._now()
            if end - begin > .5:
                raise ValueError('terminal admission exceeded half-second packet bound')
            self.admitted = True
            return dict(binding)
        except Exception as exc:
            row.update(error=type(exc).__name__, errno=getattr(exc, 'errno', None))
            raise

    def close(self):
        """Retire each owned FD before one close attempt, including failures."""
        errors = []
        for name in tuple(self.owned):
            fd = self.owned.pop(name)
            row = {'close': name, 'fd': fd}
            self.records.append(row)
            try:
                self.os.close(fd)
            except OSError as exc:
                row['close_errno'] = exc.errno
                errors.append(exc)
        if errors:
            raise errors[0]

    def report(self):
        return dict(qualified=False, outer_cleanup_complete=False,
                    known_terminal_admitted=self.admitted,
                    owned_fds=dict(self.owned), records=copy.deepcopy(self.records),
                    census=self.census.report(),
                    identity=None if self.identity is None else self.identity.report(),
                    wait=None if self.wait is None else self.wait.report())
