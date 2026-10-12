"""Prepared cleanup policy composition; no launch/wait/signal/CLI entry.

Native state is measured, not inferred from historical arm or a caller boolean.
Exclusive source policy must come from reviewed fixed-controller source bytes;
a matching hash alone is NOT an audit or authority. Actual bootstrap is absent.
"""
import copy
import math
import os
import time

from scripts.rust_semantic_wait_state import NativeWaitState


class CleanupPolicy:
    def __init__(self, libc, subreaper, group, source_verifier, source_sha256,
                 cleanup_deadline, *, os_api=None, clock=None):
        self.os = os if os_api is None else os_api
        self.clock = time.monotonic if clock is None else clock
        if (not callable(source_verifier) or type(source_sha256) is not str
                or len(source_sha256) != 64
                or any(c not in '0123456789abcdef' for c in source_sha256)
                or type(cleanup_deadline) not in {int, float}
                or not math.isfinite(cleanup_deadline)
                or not 0 < cleanup_deadline <= 2**40):
            raise ValueError('reviewed source verifier and shared deadline required')
        self.pid = self.os.getpid()
        if type(self.pid) is not int or self.pid <= 1 or subreaper.pid != self.pid:
            raise ValueError('same dedicated subreaper controller required')
        self.libc, self.subreaper, self.group = libc, subreaper, group
        self.source_verifier, self.source_sha256 = source_verifier, source_sha256
        self.deadline, self.last = cleanup_deadline, None
        self.records, self.failed = [], False
        if self.deadline - self._now() > 10:
            raise ValueError('remaining shared cleanup deadline at most ten seconds')

    def _now(self):
        now = self.clock()
        if (type(now) not in {int, float} or not math.isfinite(now)
                or not 0 <= now < self.deadline
                or (self.last is not None and now < self.last)
                or self.os.getpid() != self.pid):
            raise ValueError('same controller and remaining shared clock required')
        self.last = now
        return now

    def _source(self, row):
        raw = self.source_verifier()
        row.setdefault('source_receipts', []).append(copy.deepcopy(raw))
        expected = dict(source_sha256=self.source_sha256, sole_waiter=True,
                        no_future_forks=True, no_other_adopter=True, no_escape=True)
        if (type(raw) is not dict or set(raw) != set(expected)
                or any(type(raw[key]) is not type(value) or raw[key] != value
                       for key, value in expected.items())):
            raise ValueError('complete independently audited fixed source policy required')
        wait = self.group.wait
        receipt = self.group.cancel_phase_receipt()
        row.setdefault('cancel_phase_receipts', []).append(copy.deepcopy(receipt))
        expected_phase = dict(observer=wait.pid, reap_attempted=False,
                              reaped=False, identity_verified=True)
        if (self.group.cancel_attempted is not True or wait.terminal is None
                or wait.reap_attempted is not True or wait.reaped is not True
                or type(wait.pid) is not int or wait.pid <= 1
                or type(receipt) is not dict or set(receipt) != set(expected_phase)
                or any(type(receipt[key]) is not type(value) or receipt[key] != value
                       for key, value in expected_phase.items())):
            raise ValueError('owned group cancelled before exact observer consumption required')
        self._now()

    def __call__(self):
        if self.failed:
            raise RuntimeError('failed cleanup policy cannot reuse authority')
        if len(self.records) >= 4096:
            self.failed = True
            raise ValueError('cleanup policy evidence bound exceeded')
        row = {}
        self.records.append(row)
        native = None
        try:
            started = row['started'] = self._now()
            self._source(row)
            row['subreaper_before'] = self.subreaper.measure(self.deadline)
            native = NativeWaitState(self.libc, self.os, self.clock)
            raw = row['wait_state'] = native.measure()
            expected = dict(observer=self.pid, threads=1, sigchld_default=True,
                            sa_no_cldwait=False)
            if (type(raw) is not dict or set(raw) != set(expected)
                    or any(type(raw[key]) is not type(value) or raw[key] != value
                           for key, value in expected.items())):
                raise ValueError('current measured native wait state required')
            row['subreaper_after'] = self.subreaper.measure(self.deadline)
            for phase in ('subreaper_before', 'subreaper_after'):
                value = row[phase]
                if (type(value) is not dict or set(value) != {'controller', 'subreaper'}
                        or type(value['controller']) is not int or value['controller'] != self.pid
                        or value['subreaper'] is not True):
                    raise ValueError('current same-controller subreaper measurements required')
            self._source(row)
            finished = row['finished'] = self._now()
            if finished - started > .5:
                raise ValueError('composite native policy exceeded half-second bound')
            return dict(controller=self.pid, threads=1, sigchld_default=True,
                        sa_no_cldwait=False, sole_waiter=True, subreaper=True,
                        observer_consumed=True, group_cancel_before_reap=True,
                        group_signal_retired=True, no_future_forks=True,
                        no_other_adopter=True, no_escape=True)
        except Exception as exc:
            self.failed = True
            row.update(error=type(exc).__name__, errno=getattr(exc, 'errno', None))
            raise
        finally:
            if native is not None:
                row['native_receipt'] = copy.deepcopy(native.report())

    def report(self):
        return dict(qualified=False, outer_cleanup_complete=False, failed=self.failed,
                    records=copy.deepcopy(self.records),
                    subreaper_receipt=copy.deepcopy(self.subreaper.report()))
