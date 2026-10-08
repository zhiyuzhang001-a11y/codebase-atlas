"""Preparation-only drain of ONE already admitted adopted tracee lifetime.

No discovery/admission, FD creation/close, spawn, CLI or native control entry.
The reviewed dedicated controller must supply its fixed-source ownership journal,
held proc/pidfd identities and native verifier. A caller-provided PID is NOT
ownership. The observer stays waitable until ALL admitted children are drained.
"""
import copy
import math
import signal
import time

from scripts.rust_semantic_observer_wait import ObserverWait


class AdoptedWait:
    def __init__(self, observer, binding, verifier, cleanup_deadline, *,
                 os_api=None, signal_api=None, clock=None):
        if type(binding) is not dict:
            raise ValueError('exact admitted binding dictionary required')
        self.wait = ObserverWait(binding.get('pid'), binding.get('pidfd'), os_api)
        self.os = self.wait.os
        self.signal = signal if signal_api is None else signal_api
        self.clock = time.monotonic if clock is None else clock
        self.observer, self.verifier = observer, verifier
        self.controller = self.os.getpid()
        expected = {'pid': self.wait.pid, 'pidfd': self.wait.pidfd,
                    'uid': self.wait.uid, 'ppid': self.controller,
                    'session': observer.pid, 'pgrp': observer.pid,
                    'tracer': 0}
        identity_keys = {'starttime', 'proc_dev', 'proc_ino', 'pidfd_dev', 'pidfd_ino'}
        if (type(binding) is not dict or set(binding) != set(expected) | identity_keys
                or any(type(binding[key]) is not int or binding[key] != value
                       for key, value in expected.items())
                or any(type(binding[key]) is not int or binding[key] < 0
                       for key in identity_keys)
                or binding['starttime'] == 0 or binding['proc_ino'] == 0
                or binding['pidfd_ino'] == 0 or self.wait.pid == observer.pid
                or self.controller <= 1 or not callable(verifier)
                or not hasattr(self.signal, 'pidfd_send_signal')
                or type(cleanup_deadline) not in {int, float}
                or not math.isfinite(cleanup_deadline)
                or not 0 < cleanup_deadline <= 2**40):
            raise ValueError('reviewed admitted adopted lifetime and frozen deadline required')
        self.binding = dict(binding)
        self.deadline, self.last = cleanup_deadline, None
        self.kill_attempted = self.failed = self.complete = False
        self.records = []
        if self.deadline - self._now() > 10:
            raise ValueError('shared cleanup deadline must have at most ten seconds remaining')

    def _now(self):
        now = self.clock()
        if (type(now) not in {int, float} or not math.isfinite(now)
                or not 0 <= now < self.deadline
                or (self.last is not None and now < self.last)
                or self.os.getpid() != self.controller):
            raise ValueError('same controller and remaining shared cleanup deadline required')
        self.last = now
        return now

    def _verify(self, row):
        self._now()
        if self.observer.terminal is None or self.observer.reap_attempted:
            raise ValueError('observer terminal must remain held and unreaped')
        raw = self.verifier()  # trusted native adapter; not a project callback
        row.setdefault('verifications', []).append(
            dict(raw) if type(raw) is dict else {'invalid_type': type(raw).__name__})
        policy = {'controller': self.controller, 'threads': 1,
                  'sigchld_default': True, 'sa_no_cldwait': False,
                  'sole_waiter': True, 'journal_admitted': True}
        expected = {**self.binding, **policy}
        if (type(raw) is not dict or set(raw) != set(expected)
                or any(type(raw[key]) is not type(value) or raw[key] != value
                       for key, value in expected.items())):
            raise ValueError('same held lifetime, adoption and exclusive wait policy required')
        self._now()

    def tick(self):
        """At most two nonblocking waits and one pidfd signal; no internal retry.

        Terminal is observed WNOWAIT before the sole consuming wait. An uncertain
        consuming call or identity/deadline failure latches failure. Signal errors
        never count as terminal, but later ticks may still collect actual terminal
        evidence without resending. Complete describes only this known lifetime.
        """
        if self.failed:
            raise RuntimeError('failed adopted wait cannot reuse authority')
        if self.complete:
            return True  # no calls on consumed lifetime
        if len(self.records) >= 4096:
            self.failed = True
            raise ValueError('adopted drain record bound exceeded')
        row = {'operation': 'tick'}
        self.records.append(row)
        try:
            row['started'] = self._now()
            self._verify(row)
            terminal = self.wait.poll()
            self._now()
            if terminal is not None:
                # Recheck held lifetime and ownership immediately before consume.
                self._verify(row)
                self.wait.reap()
                row['consumed'] = True  # retained even if last clock check fails
                row['finished'] = self._now()
                self.complete = True
                return True
            if not self.kill_attempted:
                self._verify(row)
                self.kill_attempted = True
                try:
                    self.signal.pidfd_send_signal(self.wait.pidfd, 9, None, 0)
                    row['signal_sent'] = True
                except OSError as exc:
                    row.update(signal_sent=False, signal_errno=exc.errno)
                self._now()  # includes the send, even on ESRCH/EINTR/error
            row['finished'] = self._now()
            return False
        except Exception as exc:
            self.failed = True
            row.update(error=type(exc).__name__, errno=getattr(exc, 'errno', None))
            raise

    def report(self):
        return {'qualified': False, 'outer_cleanup_complete': False,
                'known_lifetime_drained': self.complete,
                'failed': self.failed, 'kill_attempted': self.kill_attempted,
                'binding': dict(self.binding), 'cleanup_deadline': self.deadline,
                'records': copy.deepcopy(self.records),
                'wait': self.wait.report()}
