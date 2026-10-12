"""Integrated C0-O5 owner protocol draft; NOT a native launch entry.

Connects journal/IPC/group/census/admission/drain to OuterControl and reconciles
every journal lifetime with exact observer-consumed or controller-consumed
terminals. Factories and terminal transport must be reviewed trusted adapters,
never project callbacks. Actual bootstrap/peer/source/strong supervisor remain
unwired; constructing this protocol supplies none of those proofs.
"""
import copy
import struct


HEADER = struct.Struct('!8sI20sI')
TERMINAL = struct.Struct('!IQI')
MAGIC = b'ATTERM01'


def encode_terminals(observer, source_sha, records):
    """Fixed observer summary preparation; source SHA is NOT authentication.

    Call only after actual consuming waits, never for early-exit stops. All
    input comes from reviewed observer evidence, not project data. No I/O.
    """
    if (type(observer) is not int or not 1 < observer <= 2**31-1
            or type(source_sha) is not str or len(source_sha) != 40
            or any(c not in '0123456789abcdef' for c in source_sha)
            or type(records) is not list or len(records) > 3):
        raise ValueError('bounded fixed terminal summary required')
    seen, parts = set(), []
    for row in records:
        if (type(row) is not dict or set(row) != {'pid', 'starttime', 'status'}
                or any(type(v) is not int for v in row.values())
                or not 1 < row['pid'] <= 2**31-1 or row['pid'] == observer
                or row['pid'] in seen or not 0 < row['starttime'] <= 2**64-1
                or not _terminal(row['status'])):
            raise ValueError('unique exact consumed Linux terminal identity required')
        seen.add(row['pid'])
        parts.append(TERMINAL.pack(row['pid'], row['starttime'], row['status']))
    return HEADER.pack(MAGIC, observer, bytes.fromhex(source_sha), len(parts)) + b''.join(parts)


def _terminal(status):
    return (type(status) is int and ((0 <= status <= 0xff00 and status & 0xff == 0)
            or (0 < status <= 0xff and 0 < status & 0x7f <= 64)))


def observer_terminal_summary(observer, source_sha, result):
    """Reconcile observe_fixed's retained consuming waits; no native operation.

    Unadmitted cleanup lifetimes or conflicting receipts are incomplete, never
    silently dropped or converted from early exit. Actual bounded pipe delivery
    and peer/source authentication still belong to the unimplemented bootstrap.
    """
    if type(result) is not dict or type(result.get('stops')) is not dict:
        raise ValueError('retained fixed observer result required')
    stops, cleanup = result['stops'], result.get('cleanup')
    admitted, normal = stops.get('admitted'), stops.get('terminals')
    abort = {} if cleanup is None else cleanup.get('terminals') if type(cleanup) is dict else None
    if any(type(value) is not dict or len(value) > 3 for value in (admitted, normal, abort)):
        raise ValueError('bounded admitted/consuming terminal maps required')
    merged = dict(normal)
    for pid, status in abort.items():
        if pid in merged and (type(status) is not int or status != merged[pid]):
            raise ValueError('conflicting consuming terminal receipts')
        merged[pid] = status
    if len(merged) > 3 or any(type(pid) is not int or pid not in admitted for pid in merged):
        raise ValueError('unadmitted terminal cannot invent journal lifetime identity')
    records = [dict(pid=pid, starttime=admitted[pid], status=status)
               for pid, status in sorted(merged.items())]
    return encode_terminals(observer, source_sha, records)


class ControlOwner:
    def __init__(self, observer, source_sha, budget, journal, ipc, group,
                 terminal_reader, census_factory, admission_factory, wait_factory, clock):
        # encode validates scalar identity without creating/evaluating source.
        encode_terminals(observer.pid, source_sha, [])
        if any(not callable(fn) for fn in (terminal_reader, census_factory,
                                           admission_factory, wait_factory, clock)):
            raise ValueError('trusted bounded transport/native factories required')
        if (journal.role != 'reader' or ipc.role != 'outer'
                or journal.journal.observer != observer.pid
                or journal.active_deadline != budget.active_deadline
                or ipc.active_deadline != budget.active_deadline
                or group.wait is not observer):
            raise ValueError('same observer and shared budget/outer endpoints required')
        self.observer, self.sha, self.budget = observer, source_sha, budget
        self.journal, self.ipc, self.group = journal, ipc, group
        self.reader, self.new_census = terminal_reader, census_factory
        self.new_admission, self.new_wait, self.clock = admission_factory, wait_factory, clock
        self.deadline, self.census = None, None
        self.transition_attempted = self.summary_attempted = self.failed = False
        self.summary_raw, self.consumed, self.adopted = None, {}, {}
        self.summary_seen = self.summary_omitted = 0
        self.admissions, self.bindings, self.waits, self.records = {}, {}, {}, []
        self.retired_admissions, self.close_errors = {}, []

    def _record(self, operation):
        if self.failed or len(self.records) >= 4096:
            raise RuntimeError('failed/bounded owner protocol cannot reuse authority')
        row = {'operation': operation}
        self.records.append(row)
        return row

    def _time(self, cleanup=False):
        check = self.budget.check_cleanup if cleanup else self.budget.check_active
        check(self.clock())

    def _journal_tick(self):
        if not self.journal.ended:
            self.journal.tick()
        if self.journal.failed or self.journal.journal.failed:
            raise ValueError('failed journal cannot grant lifetime authority')

    def begin_cleanup(self, deadline):
        row = self._record('begin-cleanup')
        if self.transition_attempted:
            raise RuntimeError('one owner cleanup transition only')
        self.transition_attempted = True
        try:
            self._time(True)
            if deadline != self.budget.cleanup_deadline or type(deadline) not in {int, float}:
                raise ValueError('exact outer FIRST cleanup deadline required')
            self.deadline = deadline
            self.journal.begin_cleanup(deadline)
            self._time(True)
        except Exception as exc:
            self.failed = True
            row.update(error=type(exc).__name__, errno=getattr(exc, 'errno', None))
            raise

    def poll_cleanup_request(self):
        row = self._record('poll-request')
        try:
            self._time()
            self._journal_tick()
            result = self.ipc.poll_cleanup_request()
            self._time()
            if type(result) is not bool:
                raise ValueError('exact IPC request state required')
            return result
        except Exception as exc:
            self.failed = True
            row.update(error=type(exc).__name__, errno=getattr(exc, 'errno', None))
            raise

    def deliver_cleanup_deadline(self, deadline):
        if not self.transition_attempted or self.deadline != deadline:
            raise ValueError('owner transition must precede IPC grant')
        self.ipc.deliver_cleanup_deadline(deadline)

    def cancel_owned_group(self):
        # Cancellation is independent of journal/parser failure; group adapter
        # owns once-only pre-reap authority and verifies the held session leader.
        return self.group.cancel_owned_group()

    def _after_reap(self):
        self._time(True)
        if (self.deadline is None or self.observer.reap_attempted is not True
                or self.observer.reaped is not True or self.observer.terminal is None):
            raise ValueError('exact consumed observer required before census/reconciliation')

    def _summary(self):
        if self.summary_attempted:
            return
        raw = self.reader()  # None = incomplete byte transport; not exit evidence
        if raw is None:
            return
        self.summary_attempted = True  # decoding failure cannot be retried
        if type(raw) is not bytes:
            raise ValueError('fixed bounded observer terminal bytes required')
        self.summary_raw = raw[:HEADER.size + 3 * TERMINAL.size + 1]
        self.summary_seen = len(raw)
        self.summary_omitted = len(raw) - len(self.summary_raw)
        if not HEADER.size <= len(raw) <= HEADER.size + 3 * TERMINAL.size:
            raise ValueError('terminal summary length outside fixed bound')
        magic, observer, sha, count = HEADER.unpack(raw[:HEADER.size])
        if (magic != MAGIC or observer != self.observer.pid or sha.hex() != self.sha
                or count > 3 or len(raw) != HEADER.size + count * TERMINAL.size):
            raise ValueError('terminal summary identity/shape mismatch')
        decoded = []
        for offset in range(HEADER.size, len(raw), TERMINAL.size):
            pid, start, status = TERMINAL.unpack(raw[offset:offset + TERMINAL.size])
            decoded.append(dict(pid=pid, starttime=start, status=status))
        if encode_terminals(observer, self.sha, decoded) != raw:
            raise ValueError('noncanonical terminal summary')
        for item in decoded:
            bound = self.journal.journal.lookup(item['pid'])
            if item['starttime'] != bound['starttime']:
                raise ValueError('consumed terminal does not match journal lifetime')
        self.consumed = {item['pid']: item for item in decoded}

    def drain_owned_tracees(self):
        row = self._record('drain')
        try:
            self._after_reap()
            self._journal_tick()
            # Do not decode terminals before all first-stop identities arrive.
            if not self.journal.ended:
                return False
            self._summary()
            self._after_reap()
            if not self.summary_attempted:
                return False
            if not self.journal.journal.report()['transport_complete']:
                raise ValueError('partial fixed lifetime registry remains incomplete')
            if self.census is None:
                self.census = self.new_census(self.deadline)
            # At most one adopted tick per known held lifetime per outer tick.
            for pid, waiter in tuple(self.waits.items()):
                if pid not in self.adopted:
                    result = waiter.tick()
                    self._after_reap()
                    if type(result) is not bool:
                        raise ValueError('exact adopted drain state required')
                    if result:
                        receipt = waiter.report()
                        wait = receipt.get('wait', {}) if type(receipt) is dict else {}
                        if (type(receipt) is not dict
                                or receipt.get('known_lifetime_drained') is not True
                                or wait.get('observer_reaped') is not True
                                or wait.get('reap_attempted') is not True
                                or type(wait.get('terminal')) is not dict
                                or wait['terminal'].get('si_pid') != pid
                                or receipt.get('binding') != self.bindings[pid]):
                            raise ValueError('actual consuming adopted terminal required')
                        self.adopted[pid] = copy.deepcopy(receipt)
            known = {item['pid'] for item in self.journal.journal.rows}
            complete = set(self.consumed) | set(self.adopted)
            if complete == known:
                return True  # still owes a separate qualified census and EOF
            found = row['census'] = self.census.tick()
            self._after_reap()
            outcome = found.get('outcome') if type(found) is dict else None
            if outcome == 'pending':
                return False
            if outcome == 'candidate_no_children':
                raise ValueError('ECHILD without every registered consuming terminal')
            if outcome != 'unadmitted_terminal':
                raise ValueError('exact policy-qualified terminal census required')
            pid = found['result']['si_pid']
            if type(pid) is not int or pid not in known or pid in complete or pid in self.waits:
                raise ValueError('unknown/repeated/already-consumed census lifetime')
            identity = self.journal.journal.lookup(pid)
            admission = self.new_admission(self.deadline)
            self.admissions[pid] = admission  # retain before native admission can fail
            binding = admission.admit(identity)
            if (type(binding) is not dict or any(type(binding.get(key)) is not int
                    or binding[key] != value for key, value in identity.items())):
                raise ValueError('admission must preserve exact journal identity')
            self.bindings[pid] = copy.deepcopy(binding)
            self._after_reap()
            self.waits[pid] = self.new_wait(admission, binding, self.deadline)
            self._after_reap()
            return False
        except Exception as exc:
            self.failed = True
            row.update(error=type(exc).__name__, errno=getattr(exc, 'errno', None))
            raise

    def no_owned_children(self):
        row = self._record('final-census')
        try:
            self._after_reap()
            known = {item['pid'] for item in self.journal.journal.rows}
            if (self.census is None or not self.summary_attempted
                    or not self.journal.journal.report()['transport_complete']
                    or set(self.consumed) | set(self.adopted) != known):
                raise ValueError('all registered consuming terminals must precede final census')
            found = row['census'] = self.census.tick()
            self._after_reap()
            if type(found) is not dict or found.get('outcome') not in {'pending', 'candidate_no_children'}:
                raise ValueError('unknown terminal after lifetime reconciliation')
            return found['outcome'] == 'candidate_no_children'
        except Exception as exc:
            self.failed = True
            row.update(error=type(exc).__name__, errno=getattr(exc, 'errno', None))
            raise

    def group_absent_after_reap(self):
        self._after_reap()
        result = self.group.group_absent_after_reap()
        self._after_reap()
        return result

    def close_admissions(self):
        # Explicitly retire owned FDs, not processes; never called while borrowed
        # adapters are still in use. Native admission.close is itself once-only.
        errors = []
        for pid in tuple(self.admissions):
            admission = self.admissions.pop(pid)
            try:
                admission.close()
            except Exception as exc:
                self.close_errors.append(dict(pid=pid, error=type(exc).__name__,
                                              errno=getattr(exc, 'errno', None)))
                errors.append(exc)
            finally:
                self.retired_admissions[pid] = copy.deepcopy(admission.report())
        if errors:
            raise errors[0]

    def report(self):
        return copy.deepcopy(dict(qualified=False, source_authenticated=False,
            outer_cleanup_complete=False, failed=self.failed, deadline=self.deadline,
            summary_raw_hex=None if self.summary_raw is None else self.summary_raw.hex(),
            summary_seen_bytes=self.summary_seen, summary_omitted_bytes=self.summary_omitted,
            observer_consumed_terminals=self.consumed, adopted_terminals=self.adopted,
            admitted_bindings=self.bindings,
            retired_admissions=self.retired_admissions, close_errors=self.close_errors,
            admissions={pid: item.report() for pid, item in self.admissions.items()},
            waits={pid: item.report() for pid, item in self.waits.items()},
            records=self.records, journal=self.journal.report(), ipc=self.ipc.report(),
            group=self.group.report()))
