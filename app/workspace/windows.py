"""Windows ACL and kill-on-close job ownership using only Win32 APIs."""
from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes
from pathlib import Path


def _libraries():
    if os.name != "nt":
        raise OSError("WINDOWS_REQUIRED")
    return ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("advapi32", use_last_error=True)


def secure_path(path: Path) -> None:
    """Protected current-user-only DACL; do not follow reparse points."""
    from .store import reject_links
    reject_links(path)
    kernel, advapi = _libraries()
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    advapi.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    advapi.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    advapi.SetNamedSecurityInfoW.restype = wintypes.DWORD
    token = wintypes.HANDLE()
    sid_text = wintypes.LPWSTR()
    descriptor = ctypes.c_void_p()
    try:
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise OSError("ACCESS_DENIED")
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not size.value:
            raise OSError("ACCESS_DENIED")
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise OSError("ACCESS_DENIED")
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise OSError("ACCESS_DENIED")
        inheritance = "OICI" if path.is_dir() else ""
        sddl = f"D:P(A;{inheritance};FA;;;{sid_text.value})"
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise OSError("ACCESS_DENIED")
        present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if not advapi.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)) or not present.value:
            raise OSError("ACCESS_DENIED")
        if advapi.SetNamedSecurityInfoW(str(path), 1, 0x80000004, None, None, dacl, None):
            raise OSError("ACCESS_DENIED")
    finally:
        if descriptor.value:
            kernel.LocalFree(descriptor)
        if sid_text:
            kernel.LocalFree(ctypes.cast(sid_text, wintypes.HLOCAL))
        if token:
            kernel.CloseHandle(token)


class _BasicLimits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD), ("minimum", ctypes.c_size_t),
                ("maximum", ctypes.c_size_t), ("active", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("read", "write", "other", "read_bytes", "write_bytes", "other_bytes")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", _BasicLimits), ("io", _IoCounters),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]


ExtendedJobLimits = _ExtendedLimits


class ProcessLifetime:
    def __init__(self, parent_pid: int):
        kernel, _ = _libraries()
        self.kernel = kernel
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        # Popen's actual OS parent; no arbitrary process can substitute its PID via CLI.
        if parent_pid != os.getppid():
            raise OSError("ACCESS_DENIED")
        self.parent = kernel.OpenProcess(0x00100000, False, parent_pid)
        self.job = kernel.CreateJobObjectW(None, None)
        if not self.parent or not self.job:
            self.close()
            raise OSError("ACCESS_DENIED")
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise OSError("ACCESS_DENIED")
        if not kernel.AssignProcessToJobObject(self.job, kernel.GetCurrentProcess()):
            self.close()
            raise OSError("ACCESS_DENIED")
        self._stop = threading.Event()
        threading.Thread(target=self._watch_parent, daemon=True).start()

    def _watch_parent(self) -> None:
        while not self._stop.is_set():
            result = self.kernel.WaitForSingleObject(self.parent, 100)
            if result != 258:  # timeout: live parent; anything else fails closed
                os._exit(1)

    def close(self) -> None:
        # Closing the assigned job kills this process too: only close at final exit.
        if hasattr(self, "_stop"):
            self._stop.set()
        if getattr(self, "parent", None):
            self.kernel.CloseHandle(self.parent)
            self.parent = None
        if getattr(self, "job", None):
            self.kernel.CloseHandle(self.job)
            self.job = None
