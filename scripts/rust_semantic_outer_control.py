"""Prepared outer control protocol; native owner/launch wiring NOT implemented.

Must run in controller, outside the observer/tracer process, with preinstalled
independent limits. Operations are trusted owned-group/drain adapters, not project
callbacks. This component alone cannot guarantee their nonblocking behavior.
No spawn, native signals, arbitrary command, CLI or CI execution entry.
"""
import copy


class OuterControl:
    def __init__(self, budget, pipes, observer_wait, owner, clock, sleep):
        self.budget, self.pipes, self.wait = budget, pipes, observer_wait
        self.owner, self.clock, self.sleep = owner, clock, sleep
        self.attempted = self.kill_attempted = self.reap_attempted = False
        self.errors, self.events = [], []
        self.active_complete = self.protocol_complete = False
        self.cleanup_requested = False
        self.observer_consumed = False

    def _error(self, phase, exc):
        if len(self.errors) < 128:
            self.errors.append({'phase': phase, 'type': type(exc).__name__,
                                'errno': getattr(exc, 'errno', None)})

    def _kill(self):
        if self.kill_attempted or self.reap_attempted:
            return
        self.kill_attempted = True  # uncertain sends must not be retried
        try:
            # Native owner must bind and preserve unreaped session leader, and
            # refuse group reuse. No numeric-PID fallback is permitted.
            self.owner.cancel_owned_group()
            self.events.append({'operation': 'cancel-owned-group', 'sent': True})
        except Exception as exc:
            self._error('cancel', exc)
            self.events.append({'operation': 'cancel-owned-group', 'sent': False})

    def run(self):
        if self.attempted:
            raise RuntimeError('one outer control run only')
        self.attempted = True
        for _ in range(2048):
            try:
                self.budget.check_active(self.clock())
                self.pipes.tick(self.clock, cleanup=False)
                requested = self.owner.poll_cleanup_request()
                if type(requested) is not bool:
                    raise ValueError('exact inner cleanup request state required')
                if requested:
                    self.budget.check_active(self.clock())
                    self.cleanup_requested = True
                    break
                terminal = self.wait.poll()  # explicit nonconsuming pidfd wait
                self.budget.check_active(self.clock())  # last-call time is included
                if terminal is not None:
                    self.active_complete = True
                    break
                self.sleep(min(.01, self.budget.check_active(self.clock())))
            except Exception as exc:
                self._error('active', exc)
                break
        else:
            self._error('active', ValueError('bounded outer active ticks exhausted'))
        try:
            self.budget.begin_cleanup(self.clock())  # ONE common <=10s budget
        except Exception as exc:
            self._error('cleanup-clock', exc)
            self._kill()
            return self.report()  # incomplete; independent native owner still responsible
        try:
            self.owner.begin_cleanup(self.budget.cleanup_deadline)
        except Exception as exc:
            self._error('cleanup-owner', exc)
            self._kill()  # owner/transport failure must not skip observer reap
        if not self.cleanup_requested:
            self._kill()  # contain failures/terminal leftovers, not a normal live tracer
        else:
            try:
                # Trusted bounded IPC must deliver this SAME absolute deadline,
                # never let inner cleanup start its own fresh grace period.
                self.owner.deliver_cleanup_deadline(self.budget.cleanup_deadline)
            except Exception as exc:
                self._error('cleanup-notify', exc)
                self._kill()
        for _ in range(2048):
            try:
                self.budget.check_cleanup(self.clock())
            except Exception as exc:
                self._error('cleanup-deadline', exc)
                self._kill()
                break
            try:
                self.pipes.tick(self.clock, cleanup=True)
            except Exception as exc:
                self._error('cleanup-pipes', exc)
                self._kill()
            try:
                if not self.reap_attempted:
                    terminal = self.wait.poll()
                    self.budget.check_cleanup(self.clock())
                    if terminal is not None:
                        retained = copy.deepcopy(terminal)
                        self._kill()  # last group authority while leader is held
                        self.budget.check_cleanup(self.clock())
                        self.reap_attempted = True
                        consumed = self.wait.reap()  # uncertain consumption never retried
                        self.events.append({'operation': 'observer-reap',
                                            'result': copy.deepcopy(consumed)})
                        if type(consumed) is not dict or consumed != retained:
                            raise ValueError('observer consuming terminal must match retained evidence')
                        self.observer_consumed = True
                        self.budget.check_cleanup(self.clock())
                if self.observer_consumed:
                    # Trusted native owner must use source/journal admission and
                    # exact consuming terminals, NOT a live proc children snapshot.
                    drained = self.owner.drain_owned_tracees()
                    self.budget.check_cleanup(self.clock())
                    if type(drained) is not bool:
                        raise ValueError('exact tracee drain state required')
                    empty = self.owner.no_owned_children() if drained else False
                    self.budget.check_cleanup(self.clock())
                    if type(empty) is not bool:
                        raise ValueError('policy-qualified P_ALL __WALL ECHILD state required')
                else:
                    drained = empty = False
                # EOF is a FINAL condition, never a prerequisite for observer
                # consume. Empty is a separate policy-checked kernel census.
                if drained and empty and self.budget.report()['all_eof']:
                    absent = self.owner.group_absent_after_reap()  # read-only, NEVER kill now
                    if type(absent) is not bool or not absent:
                        raise ValueError('owned group absence not proved')
                    self.budget.check_cleanup(self.clock())
                    self.protocol_complete = True
                    break
            except Exception as exc:
                self._error('cleanup-drain', exc)
                if not self.reap_attempted:
                    self._kill()  # never send group cancellation after observer reap
                if self.reap_attempted:
                    break  # uncertain authority cannot be reused
            try:
                self.sleep(min(.01, self.budget.check_cleanup(self.clock())))
            except Exception as exc:
                self._error('cleanup-sleep', exc)
                if not self.reap_attempted:
                    self._kill()
                break
        else:
            self._error('cleanup', ValueError('bounded outer cleanup ticks exhausted'))
            if not self.reap_attempted:
                self._kill()
        return self.report()

    def report(self):
        return {'qualified': False, 'outer_cleanup_complete': False,
                'active_terminal_observed': self.active_complete,
                'inner_cleanup_requested': self.cleanup_requested,
                'cleanup_protocol_complete': self.protocol_complete,
                'kill_attempted': self.kill_attempted, 'reap_attempted': self.reap_attempted,
                'observer_consumed': self.observer_consumed,
                'events': copy.deepcopy(self.events), 'errors': list(self.errors),
                'budget': self.budget.report(), 'observer': self.wait.report(),
                'pipes': self.pipes.report(), 'owner': self.owner.report()}
