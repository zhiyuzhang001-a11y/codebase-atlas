"""Prepared pidfd-specific observer terminal/reap adapter, no execution entry.

Borrowed pidfd must come from the reviewed sole outer owner's exact spawn and
verified lifetime binding. Never close/reuse it while this adapter is active.
No spawn, PID-number wait, kill or FD creation/close is performed here.
"""
import os
import sys


class ObserverWait:
    """Keep observer waitable until outer owner explicitly requests final reap."""

    def __init__(self, pid, pidfd, os_api=None):
        self.os = os if os_api is None else os_api
        if (sys.platform != 'linux' or type(pid) is not int or not 0 < pid <= 2**31-1
                or type(pidfd) is not int or not 3 <= pidfd <= 65535
                or pid == self.os.getpid()
                or any(not hasattr(self.os, name) for name in
                       ('waitid', 'P_PIDFD', 'WEXITED', 'WNOHANG', 'WNOWAIT', 'getuid'))):
            raise ValueError('Linux owned observer PID and borrowed pidfd APIs required')
        if self.os.get_inheritable(pidfd):
            raise ValueError('observer pidfd must be CLOEXEC')
        self.pid, self.pidfd = pid, pidfd
        self.uid = self.os.getuid()
        self.terminal = None
        self.reap_attempted = self.reaped = False
        self.records = []

    def _wait(self, consume):
        if len(self.records) >= 4096:
            raise ValueError('observer wait record bound exceeded')
        row = {'pidfd': self.pidfd, 'consume': consume}
        self.records.append(row)
        flags = self.os.WEXITED | self.os.WNOHANG
        if not consume:
            flags |= self.os.WNOWAIT
        row['flags'] = flags
        try:
            # Kernel selects one lifetime by FD and enforces parent/wait ownership.
            result = self.os.waitid(self.os.P_PIDFD, self.pidfd, flags)
        except OSError as exc:
            row['errno'] = exc.errno
            raise  # ECHILD/ESRCH is missing evidence, NEVER a verified reap.
        if result is None:
            row['result'] = None
            return None
        raw = {name: getattr(result, name) for name in
               ('si_pid', 'si_uid', 'si_signo', 'si_code', 'si_status')}
        row['result'] = raw
        if (any(type(value) is not int for value in raw.values())
                or raw['si_pid'] != self.pid or raw['si_uid'] != self.uid
                or raw['si_signo'] != 17 or raw['si_code'] not in {1, 2, 3}
                or (raw['si_code'] == 1 and not 0 <= raw['si_status'] <= 255)
                or (raw['si_code'] != 1 and not 1 <= raw['si_status'] <= 64)):
            raise ValueError('exact observer terminal siginfo required')
        return raw

    def poll(self):
        if self.reap_attempted:
            raise RuntimeError('observer reap already attempted; never reuse wait authority')
        result = self._wait(False)
        if self.terminal is not None and result != self.terminal:
            raise ValueError('waitable terminal disappeared or changed')
        if result is not None:
            self.terminal = dict(result)
        return None if result is None else dict(result)

    def reap(self):
        """Exactly one consuming wait, only after a verified nonconsuming terminal.

        Caller must complete tracer/tracee drain before this. This method cannot
        prove that ordering, group absence, successful control or output EOF.
        A failed/ambiguous consuming call is never retried on an uncertain FD.
        """
        if self.terminal is None or self.reap_attempted:
            raise RuntimeError('verified waitable terminal and unused reap authority required')
        self.reap_attempted = True
        result = self._wait(True)
        if result != self.terminal:
            raise ValueError('consuming wait did not match retained observer terminal')
        self.reaped = True
        return dict(result)

    def report(self):
        return {'qualified': False, 'outer_cleanup_complete': False,
                'pid': self.pid, 'borrowed_pidfd': self.pidfd,
                'terminal': None if self.terminal is None else dict(self.terminal),
                'reap_attempted': self.reap_attempted, 'observer_reaped': self.reaped,
                'records': [dict(row, result=dict(row['result']))
                            if isinstance(row.get('result'), dict) else dict(row)
                            for row in self.records]}
