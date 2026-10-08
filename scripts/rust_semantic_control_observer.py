"""Prepared inner C0-O5 orchestration; no spawn, CLI, or native construction.

The reviewed outer owner must install independent strong cancellation BEFORE
launch, own all FDs/waits and bind root bootstrap kill authority. This function
does not supply those guarantees or authorize evaluating generated source.
"""
import math


def observe_fixed(root, observer, ops, ptrace, *, clock, active_deadline,
                  cleanup_deadline, bootstrap_kill):
    """Connect fixed stop/drain protocols using injected, owned operations.

cleanup_deadline is an outer-owned callback returning its FIRST shared cleanup
deadline, never a renewed grace. bootstrap_kill uses the root's retained pidfd,
not a bare PID; it covers failures before NativeStops obtains an initial stop.
All native operations are caller supplied; tests only supply inert fakes.
"""
    now = clock()
    if (type(now) not in (int, float) or not math.isfinite(now)
            or type(active_deadline) not in (int, float)
            or not math.isfinite(active_deadline)
            or not now < active_deadline <= now + 20):
        raise ValueError('outer-owned finite active deadline required')
    loop = ptrace.FixedForkStops(root, observer, ops)
    result = {'qualified': False, 'outer_cleanup_complete': False,
              'control_complete': False, 'errors': [], 'cleanup': None}
    try:
        loop.run(active_deadline)
        # A final wait can block/finish after the loop's last pre-wait check.
        finished = clock()
        if (type(finished) not in (int, float) or not math.isfinite(finished)
                or not now <= finished < active_deadline):
            raise TimeoutError('control completed beyond active deadline')
        result['control_complete'] = True
    except Exception as exc:
        result['errors'].append({'operation': 'active', 'type': type(exc).__name__})
    finally:
        # Exactly one outer phase transition, including normal completion.
        # Exceptions never erase partial stop/admission/terminal evidence.
        drainer = None
        try:
            deadline = cleanup_deadline()
            now = clock()
            if (type(now) not in (int, float) or not math.isfinite(now)
                    or type(deadline) not in (int, float)
                    or not math.isfinite(deadline) or not now < deadline <= now + 10):
                raise ValueError('remaining outer cleanup deadline required')
            if root not in loop.terminals and root not in ops.handles:
                # Bootstrap root may still be running before its first SIGSTOP.
                # EXITKILL is not yet installed; never silently omit this root.
                try:
                    bootstrap_kill()
                except Exception as exc:
                    result['errors'].append({'operation': 'bootstrap-kill',
                                             'type': type(exc).__name__})
                    # A failed send is not exit evidence. Still consume any
                    # initial stop and let the stopped-handle protocol retry
                    # with verified authority; outer strong cancellation stays.
            drainer = ptrace.FixedForkCleanup(loop, ops)
            result['cleanup'] = drainer.run(deadline)
        except Exception as exc:
            result['errors'].append({'operation': 'cleanup', 'type': type(exc).__name__})
        finally:
            if drainer is not None and result['cleanup'] is None:
                result['cleanup'] = {
                    'qualified': False, 'outer_cleanup_complete': False,
                    'partial': True, 'all_terminal': False, 'root_reaped': False,
                    'known': sorted(drainer.known), 'parents': dict(drainer.parents),
                    'terminals': dict(drainer.terminals), 'events': list(drainer.events),
                    'errors': list(drainer.errors), 'stopped': sorted(drainer.stopped)}
            try:
                ops.close_handles()
            except Exception as exc:
                result['errors'].append({'operation': 'close-handles',
                                         'type': type(exc).__name__})
    result['stops'] = {'parents': dict(loop.parents), 'admitted': dict(loop.admitted),
                       'early': dict(loop.early), 'events': list(loop.events),
                       'samples': list(loop.samples), 'terminals': dict(loop.terminals),
                       'pending_stops': sorted(loop.pending_stops)}
    result['native'] = {'calls': list(ops.calls), 'observations': list(ops.observations)}
    # This deliberately cannot infer group absence, outer reap, output limits,
    # bootstrap provenance, syscall coverage or peak memory from inner drain.
    return result
