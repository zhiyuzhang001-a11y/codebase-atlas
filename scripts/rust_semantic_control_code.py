"""Fixed C0-O5 bootstrap source preparation. No execution or spawn entry.

The outer controller must verify tool/source receipts, isolated session, held
true FD, inherited objects and strong limits before evaluating this source.
Returning/compiling it is NOT authority to execute a control or Cargo.
"""
import ast
import hashlib


MODULE_ORDER = tuple('rust_semantic_' + name for name in (
    'linux_resources', 'ptrace', 'control_budget', 'control_pipes', 'observer_wait',
    'wait_state', 'subreaper', 'owned_group', 'cleanup_ipc', 'control_journal',
    'journal_pipe', 'owned_journal', 'adopted_identity', 'adopted_wait',
    'terminal_census', 'terminal_admission', 'cleanup_policy', 'outer_control',
    'control_observer', 'outer_pipes', 'root_launch', 'source_file', 'control_code'))
STDLIB_IMPORTS = frozenset(('__future__', 'ast', 'copy', 'ctypes', 'errno',
                          'fcntl', 'hashlib', 'math', 'os', 'platform', 're',
                          'signal', 'stat', 'struct', 'sys', 'time'))


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


def prepare_module_bundle(sources: dict) -> tuple[bytes, dict]:
    """Prepare definitions-only regular-artifact bytes, NOT a -c argument.

    Caller must supply exact reviewed controller bytes from a frozen manifest,
    never project input. This syntax/import-order check is not a source audit.
    Actual authenticated artifact FD loading and owner/bootstrap are absent.
    """
    if type(sources) is not dict or set(sources) != {n + '.py' for n in MODULE_ORDER}:
        raise ValueError('complete exact fixed controller module set required')
    receipts, chunks, available, total = {}, [], set(), 0
    for name in MODULE_ORDER:
        raw = sources[name + '.py']
        if type(raw) is not bytes or not 0 < len(raw) <= 65536:
            raise ValueError('bounded exact frozen sibling bytes required')
        total += len(raw)
        if total > 192 * 1024:
            raise ValueError('aggregate source bytes exceed 192 KiB')
        tree = ast.parse(raw.decode('utf-8', 'strict'), filename=name)
        compile(raw, name, 'exec')  # syntax only; never execute source here
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name not in STDLIB_IMPORTS for alias in node.names):
                    raise ValueError('unknown import cannot fall back to filesystem')
            elif isinstance(node, ast.ImportFrom):
                if node.level or not node.module:
                    raise ValueError('relative imports outside frozen contract')
                if node.module.startswith('scripts.'):
                    if node.module[8:] not in available:
                        raise ValueError('unprepared/cyclic sibling import')
                elif node.module not in STDLIB_IMPORTS:
                    raise ValueError('unknown import outside frozen standard library')
        receipts[name] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        full = 'scripts.' + name
        chunks.append(f'_m = types.ModuleType({full!r})\n'
                      f'_m.__package__ = "scripts"\n'
                      f'sys.modules[{full!r}] = _m\n'
                      f'setattr(_package, {name!r}, _m)\n'
                      f'exec(compile(bytes.fromhex({raw.hex()!r}), {full!r}, "exec"), _m.__dict__)\n')
        available.add(name)
    prefix = ('import sys, types\n'
              'if "scripts" in sys.modules:\n'
              '    raise RuntimeError("foreign scripts package already loaded")\n'
              '_package = types.ModuleType("scripts")\n'
              '_package.__path__ = []\n'
              'sys.modules["scripts"] = _package\n')
    bundle = (prefix + ''.join(chunks)).encode('utf-8')
    if len(bundle) > 512 * 1024:
        raise ValueError('fixed source artifact exceeds 512 KiB')
    compile(bundle, '<prepared-module-artifact>', 'exec')  # no evaluation
    return bundle, dict(qualified=False, source_authenticated=False,
                        source_policy_audited=False, delivery='owned-regular-artifact-required',
                        source_bytes=total, bytes=len(bundle),
                        sha256=hashlib.sha256(bundle).hexdigest(), modules=receipts)
