"""Create a stdio child atomically inside a kill-on-close Windows Job.

No PID-based taskkill, suspended/unassigned interval, shell or foreign process
enumeration. The native Job owns descendants even if their parent exits first.
"""
import ctypes
from ctypes import wintypes as W
import math
import os
import subprocess
from time import monotonic, sleep


class WindowsOwnedProcess:
    def __init__(self, argv, *, cwd, env):
        if os.name != "nt":
            raise OSError("Windows owned process requires Windows")
        import msvcrt
        self.args = list(argv)
        self.returncode = None
        self.stdin = self.stdout = self.stderr = None
        self._process = self._job = None
        self._kernel = ctypes.WinDLL("kernel32.dll", use_last_error=True, winmode=0x800)
        P = ctypes.c_void_p
        S = ctypes.c_size_t
        def function(name, arguments, result):
            selected = getattr(self._kernel, name)
            selected.argtypes, selected.restype = arguments, result
            return selected
        self._close = function("CloseHandle", [W.HANDLE], W.BOOL)
        self._wait = function("WaitForSingleObject", [W.HANDLE, W.DWORD], W.DWORD)
        self._exit = function("GetExitCodeProcess", [W.HANDLE, ctypes.POINTER(W.DWORD)], W.BOOL)
        self._kill = function("TerminateJobObject", [W.HANDLE, W.UINT], W.BOOL)
        self._query = function("QueryInformationJobObject", [W.HANDLE, ctypes.c_int, P, W.DWORD, P], W.BOOL)
        create_job = function("CreateJobObjectW", [P, W.LPCWSTR], W.HANDLE)
        set_job = function("SetInformationJobObject", [W.HANDLE, ctypes.c_int, P, W.DWORD], W.BOOL)
        initialize = function("InitializeProcThreadAttributeList", [P, W.DWORD, W.DWORD, ctypes.POINTER(S)], W.BOOL)
        update = function("UpdateProcThreadAttribute", [P, W.DWORD, S, P, S, P, P], W.BOOL)
        delete = function("DeleteProcThreadAttributeList", [P], None)
        create = function("CreateProcessW", [W.LPCWSTR, W.LPWSTR, P, P, W.BOOL, W.DWORD,
                          P, W.LPCWSTR, P, P], W.BOOL)
        class BasicLimit(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", W.DWORD), ("minimum", S), ("maximum", S),
                        ("active_limit", W.DWORD), ("affinity", S), ("priority", W.DWORD),
                        ("scheduling", W.DWORD)]
        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
        class ExtendedLimit(ctypes.Structure):
            _fields_ = [("basic", BasicLimit), ("io", IoCounters), ("process_memory", S),
                        ("job_memory", S), ("peak_process", S), ("peak_job", S)]
        class Startup(ctypes.Structure):
            _fields_ = [("cb", W.DWORD), ("reserved", W.LPWSTR), ("desktop", W.LPWSTR),
                        ("title", W.LPWSTR), ("x", W.DWORD), ("y", W.DWORD),
                        ("x_size", W.DWORD), ("y_size", W.DWORD), ("x_chars", W.DWORD),
                        ("y_chars", W.DWORD), ("fill", W.DWORD), ("flags", W.DWORD),
                        ("show", W.WORD), ("reserved_size", W.WORD), ("reserved_bytes", P),
                        ("stdin", W.HANDLE), ("stdout", W.HANDLE), ("stderr", W.HANDLE)]
        class StartupEx(ctypes.Structure):
            _fields_ = [("startup", Startup), ("attributes", P)]
        class ProcessInformation(ctypes.Structure):
            _fields_ = [("process", W.HANDLE), ("thread", W.HANDLE), ("pid", W.DWORD), ("tid", W.DWORD)]
        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_int64) for name in (
                "user_time", "kernel_time", "period_user", "period_kernel")] + [
                    (name, W.DWORD) for name in ("page_faults", "total", "active", "terminated")]
        self._Accounting = Accounting
        descriptors = []
        attributes = None
        initialized = False
        information = ProcessInformation()
        try:
            self._job = create_job(None, None)
            if not self._job:
                raise ctypes.WinError(ctypes.get_last_error())
            limit = ExtendedLimit()
            limit.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
            if not set_job(self._job, 9, ctypes.byref(limit), ctypes.sizeof(limit)):
                raise ctypes.WinError(ctypes.get_last_error())
            stdin_read, stdin_write = os.pipe()
            descriptors.extend((stdin_read, stdin_write))
            stdout_read, stdout_write = os.pipe()
            descriptors.extend((stdout_read, stdout_write))
            stderr_read, stderr_write = os.pipe()
            descriptors.extend((stderr_read, stderr_write))
            child_descriptors = (stdin_read, stdout_write, stderr_write)
            for descriptor in child_descriptors:
                os.set_inheritable(descriptor, True)
            handles = (W.HANDLE * 3)(*(msvcrt.get_osfhandle(descriptor) for descriptor in child_descriptors))
            jobs = (W.HANDLE * 1)(self._job)
            size = S()
            initialize(None, 2, 0, ctypes.byref(size))
            if not 0 < size.value <= 65536:
                raise OSError("Windows process attribute list size is invalid")
            attributes = ctypes.create_string_buffer(size.value)
            if not initialize(attributes, 2, 0, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            initialized = True
            # HANDLE_LIST=2 and JOB_LIST=13 with INPUT attribute flag 0x20000.
            for key, value in ((0x20002, handles), (0x2000D, jobs)):
                if not update(attributes, 0, key, value, ctypes.sizeof(value), None, None):
                    raise ctypes.WinError(ctypes.get_last_error())
            startup = StartupEx()
            startup.startup.cb = ctypes.sizeof(startup)
            startup.startup.flags = 0x100  # STARTF_USESTDHANDLES
            startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = handles
            startup.attributes = ctypes.cast(attributes, P)
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(self.args))
            if any("\0" in key or "\0" in value or "=" in key for key, value in env.items()):
                raise ValueError("Windows child environment is invalid")
            environment = ctypes.create_unicode_buffer("\0".join(
                key + "=" + value for key, value in sorted(env.items(), key=lambda item: item[0].upper())) + "\0\0")
            flags = 0x80000 | 0x400 | 0x200  # extended startup, Unicode env, process group
            if not create(self.args[0], command, None, None, True, flags, environment, str(cwd),
                          ctypes.byref(startup), ctypes.byref(information)):
                raise ctypes.WinError(ctypes.get_last_error())
            self._process = information.process
            self.pid = information.pid
            self._close(information.thread)
            information.thread = None
            for descriptor in child_descriptors:
                os.close(descriptor)
                descriptors.remove(descriptor)
            self.stdin = os.fdopen(stdin_write, "wb", buffering=0)
            descriptors.remove(stdin_write)
            self.stdout = os.fdopen(stdout_read, "rb", buffering=0)
            descriptors.remove(stdout_read)
            self.stderr = os.fdopen(stderr_read, "rb", buffering=0)
            descriptors.remove(stderr_read)
        except BaseException:
            if self._job:
                self._kill(self._job, 1)
                self._close(self._job)
                self._job = None
            if self._process:
                self._wait(self._process, 1000)
                self._close(self._process)
                self._process = None
            for stream in (self.stdin, self.stdout, self.stderr):
                if stream is not None:
                    stream.close()
            raise
        finally:
            if initialized:
                delete(attributes)
            if information.thread:
                self._close(information.thread)
            for descriptor in descriptors:
                os.close(descriptor)

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        result = self._wait(self._process, 0)
        if result == 258:  # WAIT_TIMEOUT
            return None
        if result != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = W.DWORD()
        if not self._exit(self._process, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.returncode = code.value
        return self.returncode

    def wait(self, timeout=None):
        if self.poll() is not None:
            return self.returncode
        milliseconds = 0xffffffff if timeout is None else min(0xfffffffe, max(0, math.ceil(timeout * 1000)))
        result = self._wait(self._process, milliseconds)
        if result == 258:
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self.poll()

    def terminate(self):
        if self._job and not self._kill(self._job, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    kill = terminate

    def close_owned_job(self, timeout):
        if not self._job:
            return
        self.terminate()
        deadline = monotonic() + timeout
        try:
            while True:
                accounting = self._Accounting()
                if not self._query(self._job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                if not accounting.active:
                    break
                if monotonic() >= deadline:
                    raise TimeoutError("Windows owned Job did not empty within cleanup grace")
                sleep(min(0.01, max(0, deadline - monotonic())))
        finally:
            self._close(self._job)
            self._job = None
            if self._process:
                self._close(self._process)
                self._process = None

    def __del__(self):
        # Last-resort handle release is not acceptance evidence. Explicit
        # close_owned_job must still prove the owned tree is empty in time.
        close = getattr(self, "_close", None)
        if close is not None:
            job = getattr(self, "_job", None)
            if job:
                self._kill(job, 1)
                close(job)
                self._job = None
            process = getattr(self, "_process", None)
            if process:
                close(process)
                self._process = None
