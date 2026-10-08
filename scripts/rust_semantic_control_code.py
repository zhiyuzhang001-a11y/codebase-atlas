"""Fixed C0-O5 bootstrap source preparation. No execution or spawn entry.

The outer controller must verify tool/source receipts, isolated session, held
true FD, inherited objects and strong limits before evaluating this source.
Returning/compiling it is NOT authority to execute a control or Cargo.
"""
import hashlib


ROOT_SOURCE = r'''
def fixed_root(libc, true_fd, control_env):
    # Trusted, unobserved bootstrap, followed by exactly two serial forks.
    # The observer is the exclusive tracer; no attach or arbitrary commands.
    try:
        if set(control_env) != {'PATH', 'HOME', 'LC_ALL'}:
            os._exit(120)
        libc.ptrace.restype = ctypes.c_long
        libc.ptrace.argtypes = [ctypes.c_uint, ctypes.c_int,
                               ctypes.c_void_p, ctypes.c_void_p]
        libc.execveat.restype = ctypes.c_int
        libc.execveat.argtypes = [ctypes.c_int, ctypes.c_char_p,
                                 ctypes.POINTER(ctypes.c_char_p),
                                 ctypes.POINTER(ctypes.c_char_p), ctypes.c_int]
        ctypes.set_errno(0)
        if libc.ptrace(0, 0, None, None) != 0:
            os._exit(121)
        os.kill(os.getpid(), 19)  # Linux SIGSTOP, before any control fork.
        for mode in ('path', 'fd'):
            child = os.fork()
            if child == 0:
                try:
                    if mode == 'path':
                        os.execve('/usr/bin/true', ['/usr/bin/true', 'atlas-c0-o5-path'], control_env)
                    else:
                        argv = (ctypes.c_char_p * 3)(b'/usr/bin/true', b'atlas-c0-o5-fd', None)
                        values = [k.encode('ascii') + b'=' + v.encode('utf-8')
                                  for k, v in sorted(control_env.items())]
                        envp = (ctypes.c_char_p * (len(values) + 1))(*values, None)
                        # AT_EMPTY_PATH; exact inherited read-only, verified true FD.
                        libc.execveat(true_fd, b'', argv, envp, 0x1000)
                except BaseException:
                    pass
                os._exit(122)  # failed exec is not successful control execution.
            pid, status = os.waitpid(child, 0)
            if pid != child or status != 0:
                os._exit(123)
        os._exit(0)
    except BaseException:
        os._exit(124)
'''


def prepare_sources(resources: bytes, ptrace: bytes) -> tuple[str, dict]:
    """Embed exact caller-measured owned sibling bytes, not sys.path imports.

    Only trusted controller source bytes may enter this API, never project data.
    The controller must bind their hashes to its exact clean PR source identity.
    This function produces definitions only: it does not call fixed_root or fork.
    """
    receipts = {}
    prefix = 'import ctypes, os, types\n'
    for name, raw in (('resources', resources), ('ptrace', ptrace)):
        if type(raw) is not bytes or not 0 < len(raw) <= 65536:
            raise ValueError('bounded measured source bytes required')
        raw.decode('utf-8', 'strict')
        compile(raw, f'<measured-{name}>', 'exec')  # syntax, never execution
        receipts[name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        prefix += f'{name} = types.ModuleType("atlas_c0_{name}")\n'
        prefix += f'exec(compile(bytes.fromhex({raw.hex()!r}), "<measured-{name}>", "exec"), {name}.__dict__)\n'
    source = prefix + ROOT_SOURCE
    if len(source.encode()) > 100 * 1024:
        raise ValueError('prepared isolated -c argument exceeds 100 KiB cap')
    compile(source, '<prepared-fixed-control>', 'exec')  # syntax only
    receipts['root'] = {'bytes': len(ROOT_SOURCE.encode()),
                        'sha256': hashlib.sha256(ROOT_SOURCE.encode()).hexdigest()}
    receipts['assembled'] = {'bytes': len(source.encode()),
                             'sha256': hashlib.sha256(source.encode()).hexdigest()}
    return source, receipts
