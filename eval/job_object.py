"""Windows Job Object containment for the evaluation harness's child process.

    job = JobObject()                       # kill-on-close job
    proc = job.create_process(cmd, cwd)     # born INSIDE the job, and suspended
    if not job.contains(proc): ...          # verified before anything runs
    proc.resume()                           # only now does its first instruction execute

The job is handed to CreateProcessW through PROC_THREAD_ATTRIBUTE_JOB_LIST, so the
child is a member from the moment it exists: there is no instant in which it
exists outside the job, and therefore none in which a dying harness could leave it
behind. It is also created suspended, so it runs no code until membership has been
verified. Every process the child starts later belongs to the job too. If the
harness dies in any way that closes its handles (TerminateProcess, crash, closing
the console), Windows kills everything in the job, suspended or running. The job is
armed for its whole life: closing its handle always kills every process still in it,
at any depth, and there is no way to disarm it.

Needs Windows 10 or newer for the job-list attribute (checked here on Windows 11
build 26200 only); on a system that refuses it, CreateProcessW fails, nothing is
created and the caller must refuse the trial. Not covered: power loss, and
processes that leave the job (it grants no breakaway right). Stdlib + ctypes only;
nothing here imports the agent.
"""

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JobObjectBasicAccountingInformation = 1
_JobObjectExtendedLimitInformation = 9
_EXTENDED_STARTUPINFO_PRESENT, _CREATE_SUSPENDED = 0x00080000, 0x00000004
_PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
_WAIT_TIMEOUT, _INFINITE = 258, 0xFFFFFFFF


class _BasicLimit(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD),
                ("SchedulingClass", wt.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount",
                                               "OtherOperationCount", "ReadTransferCount",
                                               "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimit), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _BasicAccounting(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64),
                ("ThisPeriodTotalKernelTime", ctypes.c_int64), ("TotalPageFaultCount", wt.DWORD),
                ("TotalProcesses", wt.DWORD), ("ActiveProcesses", wt.DWORD),
                ("TotalTerminatedProcesses", wt.DWORD)]


class _StartupInfo(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR),
                ("lpTitle", wt.LPWSTR), ("dwX", wt.DWORD), ("dwY", wt.DWORD), ("dwXSize", wt.DWORD),
                ("dwYSize", wt.DWORD), ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD),
                ("dwFillAttribute", wt.DWORD), ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD),
                ("cbReserved2", wt.WORD), ("lpReserved2", ctypes.c_void_p), ("hStdInput", wt.HANDLE),
                ("hStdOutput", wt.HANDLE), ("hStdError", wt.HANDLE)]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", ctypes.c_void_p)]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE), ("dwProcessId", wt.DWORD),
                ("dwThreadId", wt.DWORD)]


def _kernel32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    H, P = wt.HANDLE, ctypes.c_void_p
    for name, argtypes, restype in [
        ("CreateJobObjectW", [P, wt.LPCWSTR], H),
        ("SetInformationJobObject", [H, ctypes.c_int, P, wt.DWORD], wt.BOOL),
        ("QueryInformationJobObject", [H, ctypes.c_int, P, wt.DWORD, P], wt.BOOL),
        ("IsProcessInJob", [H, H, ctypes.POINTER(wt.BOOL)], wt.BOOL),
        ("TerminateJobObject", [H, wt.UINT], wt.BOOL),
        ("TerminateProcess", [H, wt.UINT], wt.BOOL),
        ("InitializeProcThreadAttributeList", [P, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.c_size_t)], wt.BOOL),
        ("UpdateProcThreadAttribute", [P, wt.DWORD, ctypes.c_size_t, P, ctypes.c_size_t, P, P], wt.BOOL),
        ("DeleteProcThreadAttributeList", [P], None),
        ("CreateProcessW", [wt.LPCWSTR, wt.LPWSTR, P, P, wt.BOOL, wt.DWORD, P, wt.LPCWSTR,
                            ctypes.POINTER(_StartupInfoEx), ctypes.POINTER(_ProcessInformation)], wt.BOOL),
        ("ResumeThread", [H], wt.DWORD),
        ("WaitForSingleObject", [H, wt.DWORD], wt.DWORD),
        ("GetExitCodeProcess", [H, ctypes.POINTER(wt.DWORD)], wt.BOOL),
        ("CloseHandle", [H], wt.BOOL),
    ]:
        fn = getattr(k, name)
        fn.argtypes, fn.restype = argtypes, restype
    return k


def _fail(what):
    raise ctypes.WinError(ctypes.get_last_error(), f"{what} failed")


class ContainedProcess:
    """The few Popen methods run_trial needs, over a process created by JobObject.create_process."""

    def __init__(self, k, info):
        self._k, self.pid, self.returncode = k, info.dwProcessId, None
        self._process, self._thread = info.hProcess, info.hThread

    def resume(self):
        if self._k.ResumeThread(self._thread) == 0xFFFFFFFF:
            _fail("ResumeThread")

    def poll(self):
        if self.returncode is None and self._process:
            if self._k.WaitForSingleObject(self._process, 0) != _WAIT_TIMEOUT:
                code = wt.DWORD()
                if not self._k.GetExitCodeProcess(self._process, ctypes.byref(code)):
                    _fail("GetExitCodeProcess")
                self.returncode = code.value
        return self.returncode

    def wait(self, timeout=None):
        ms = _INFINITE if timeout is None else int(timeout * 1000)
        if self._k.WaitForSingleObject(self._process, ms) == _WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(self.pid, timeout)
        return self.poll()

    def kill(self):
        if self.poll() is None:
            self._k.TerminateProcess(self._process, 1)

    def close(self):
        for name in ("_thread", "_process"):
            if getattr(self, name):
                self._k.CloseHandle(getattr(self, name))
                setattr(self, name, None)


class JobObject:
    """A job whose processes are all killed when its last handle closes."""

    def __init__(self):
        if sys.platform != "win32":
            raise OSError("Job Objects exist only on Windows")
        self._k = _kernel32()
        self.handle = self._k.CreateJobObjectW(None, None)
        if not self.handle:
            _fail("CreateJobObject")
        try:
            self._arm()
        except OSError:
            self.close()
            raise

    def _arm(self):
        """Kill-on-close, for the job's whole life: nothing in this class can clear it."""
        info = _ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._k.SetInformationJobObject(self.handle, _JobObjectExtendedLimitInformation,
                                               ctypes.byref(info), ctypes.sizeof(info)):
            _fail("SetInformationJobObject")

    def create_process(self, cmd, cwd=None):
        """Start cmd inside this job, suspended. OSError if Windows cannot (nothing is created).

        Standard handles are not redirected and the environment is inherited, exactly as
        for subprocess.Popen(cmd, cwd=cwd); the child shares the harness's console.
        """
        k = self._k
        size = ctypes.c_size_t(0)
        k.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))   # sizing call (fails by design)
        attrs = ctypes.create_string_buffer(size.value)
        if not k.InitializeProcThreadAttributeList(attrs, 1, 0, ctypes.byref(size)):
            _fail("InitializeProcThreadAttributeList")
        try:
            jobs = (wt.HANDLE * 1)(self.handle)
            if not k.UpdateProcThreadAttribute(attrs, 0, _PROC_THREAD_ATTRIBUTE_JOB_LIST, jobs,
                                               ctypes.sizeof(jobs), None, None):
                _fail("UpdateProcThreadAttribute(JOB_LIST)")
            startup = _StartupInfoEx()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.lpAttributeList = ctypes.cast(attrs, ctypes.c_void_p)
            info = _ProcessInformation()
            cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(a) for a in cmd]))
            if not k.CreateProcessW(None, cmdline, None, None, False,
                                    _EXTENDED_STARTUPINFO_PRESENT | _CREATE_SUSPENDED, None,
                                    None if cwd is None else str(cwd), ctypes.byref(startup),
                                    ctypes.byref(info)):
                _fail("CreateProcess")
        finally:
            k.DeleteProcThreadAttributeList(attrs)
        return ContainedProcess(k, info)

    def contains(self, proc):
        """True if the process is a member of this job."""
        inside = wt.BOOL()
        if not self._k.IsProcessInJob(proc._process, self.handle, ctypes.byref(inside)):
            _fail("IsProcessInJob")
        return bool(inside.value)

    def active_processes(self):
        info = _BasicAccounting()
        if not self._k.QueryInformationJobObject(self.handle, _JobObjectBasicAccountingInformation,
                                                 ctypes.byref(info), ctypes.sizeof(info), None):
            _fail("QueryInformationJobObject")
        return info.ActiveProcesses

    def terminate(self, exit_code=1):
        """Kill every process in the job (the child and everything it started)."""
        if not self._k.TerminateJobObject(self.handle, exit_code):
            _fail("TerminateJobObject")

    def wait_empty(self, seconds):
        """Wait until no process is left in the job; False if some still are after `seconds`."""
        end = time.time() + seconds
        while self.active_processes():
            if time.time() >= end:
                return False
            time.sleep(0.05)
        return True

    def close(self):
        if self.handle:
            self._k.CloseHandle(self.handle)         # kills every process still in the job
            self.handle = None
