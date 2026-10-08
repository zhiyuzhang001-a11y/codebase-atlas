"""Preparation-only nonconsuming discovery; never grants PID ownership.

No launcher, signal, consuming wait, FD allocation or cleanup-success entry.
Verifier must be a reviewed native measurement + fixed-source policy adapter,
not a project callback. The actual owner/journal/admission adapter is absent.
"""
import copy
import errno
import math
import os
import sys
import time


class TerminalCensus:
    def __init__(self, observer, verifier, cleanup_deadline, *, os_api=None, clock=None):
        self.os = os if os_api is None else os_api
        self.clock = time.monotonic if clock is None else clock
        if (sys.platform != 'linux' or not callable(verifier)
                or any(not hasattr(self.os, name) for name in
                       ('getpid', 'getuid', 'waitid', 'P_ALL', 'WEXITED', 'WNOHANG', 'WNOWAIT'))
                or type(cleanup_deadline) not in {int, float}
                or not math.isfinite(cleanup_deadline)
                or not 0 < cleanup_deadline <= 2**40):
            raise ValueError('Linux native policy and frozen cleanup deadline required')
        self.controller, self.uid = self.os.getpid(), self.os.getuid()
        if type(self.controller) is not int or self.controller <= 1:
            raise ValueError('dedicated controller required')
        self.observer, self.verifier = observer, verifier
        self.deadline, self.last = cleanup_deadline, None
        self.failed, self.records = False, []
        if self.deadline - self._now() > 10:
            raise ValueError('at most ten shared cleanup seconds remaining')

    def _now(self):
        now = self.clock()
        if (type(now) not in {int, float} or not math.isfinite(now)
                or not 0 <= now < self.deadline
                or (self.last is not None and now < self.last)
                or self.os.getpid() != self.controller or self.os.getuid() != self.uid):
            raise ValueError('same controller and remaining shared deadline required')
        self.last = now
        return now

    def _verify(self, row):
        self._now()
        if (self.observer.terminal is None or self.observer.reap_attempted is not True
                or self.observer.reaped is not True):
            raise ValueError('exact consumed observer required before census')
        raw = self.verifier()
        row.setdefault('verifications', []).append(copy.deepcopy(raw))
        expected = dict(controller=self.controller, threads=1, sigchld_default=True,
                        sa_no_cldwait=False, sole_waiter=True, subreaper=True,
                        observer_consumed=True, group_cancel_before_reap=True,
                        group_signal_retired=True, no_future_forks=True,
                        no_other_adopter=True, no_escape=True)
        if (type(raw) is not dict or set(raw) != set(expected)
                or any(type(raw[key]) is not type(value) or raw[key] != value
                       for key, value in expected.items())):
            raise ValueError('measured native and fixed-source census policy required')
        self._now()

    def tick(self):
        """One nonblocking WNOWAIT call; no retry, admission or completion.

        None is pending. Policy-qualified ECHILD is only a candidate receipt;
        the outer owner must still reconcile every registered lifetime and EOF.
        Terminal siginfo remains waitable for subsequent exact admission.
        """
        if self.failed:
            raise RuntimeError('failed census cannot reuse authority')
        if len(self.records) >= 4096:
            self.failed = True
            raise ValueError('census record bound exceeded')
        row = {'flags': self.os.WEXITED | self.os.WNOHANG | self.os.WNOWAIT | 0x40000000}
        self.records.append(row)
        try:
            row['started'] = self._now()
            self._verify(row)
            try:
                result = self.os.waitid(self.os.P_ALL, 0, row['flags'])
            except OSError as exc:
                row['wait_errno'] = exc.errno
                if exc.errno != errno.ECHILD:
                    raise
                self._verify(row)
                row['outcome'] = 'candidate_no_children'
            else:
                row['result'] = None if result is None else {
                    name: getattr(result, name) for name in
                    ('si_pid', 'si_uid', 'si_signo', 'si_code', 'si_status')}
                self._verify(row)
                if result is None:
                    row['outcome'] = 'pending'
                else:
                    raw = row['result']
                    if (any(type(value) is not int for value in raw.values())
                            or not 0 < raw['si_pid'] <= 2**31-1
                            or raw['si_pid'] in {self.controller, self.observer.pid}
                            or raw['si_uid'] != self.uid or raw['si_signo'] != 17
                            or raw['si_code'] not in {1, 2, 3}
                            or (raw['si_code'] == 1 and not 0 <= raw['si_status'] <= 255)
                            or (raw['si_code'] != 1 and not 1 <= raw['si_status'] <= 64)):
                        raise ValueError('terminal discovery siginfo required')
                    row['outcome'] = 'unadmitted_terminal'
            row['finished'] = self._now()
            return copy.deepcopy(row)
        except Exception as exc:
            self.failed = True
            row.update(error=type(exc).__name__, errno=getattr(exc, 'errno', None))
            raise

    def report(self):
        return dict(qualified=False, outer_cleanup_complete=False,
                    failed=self.failed, cleanup_deadline=self.deadline,
                    records=copy.deepcopy(self.records))
