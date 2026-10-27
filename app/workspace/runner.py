"""Windows process-tree runner. Suspended creation prevents pre-job children.

This contains no SQL and accepts only an already approved immutable plan.
Same-user test code is not sandboxed. Output is bounded across both pipes.
"""
from __future__ import annotations

import base64
import codecs
import ctypes
import hashlib
import importlib
import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import Any

from .scoped_files import ScopeError, pin_path
from .windows import ExtendedJobLimits


class JobAccounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in ('user', 'kernel', 'period_user', 'period_kernel')] + [
        (name, wintypes.DWORD) for name in ('faults', 'processes', 'active', 'terminated')]


@contextmanager
def runtime_pins(paths: list[Path], expected: dict[str, str]) -> Generator[None, None, None]:
    """Keep executable/CLI bytes and their ancestors stable through launch/run."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    from .snapshots import fingerprint
    with ExitStack() as stack:
        for path in paths:
            for parent in reversed(path.parents):
                pin_path(parent, stack)
            handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ScopeError('PRECONDITION_FAILED')
            stack.callback(kernel.CloseHandle, handle)
            pin_path(path, stack)
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected.get(fingerprint(str(path))):
                raise ScopeError('PRECONDITION_FAILED')
        yield


def execute(plan: dict[str, Any], cwd: str, environment: dict[str, str], cancelled: threading.Event,
            before_resume: Callable[[], None] = lambda: None) -> dict[str, Any]:
    if os.name != 'nt' or not os.path.isabs(plan['executable']) or any('\0' in arg for arg in plan['argv']):
        raise ValueError('COMMAND_NOT_ALLOWED')
    api: Any = importlib.import_module('_winapi')
    msvcrt: Any = importlib.import_module('msvcrt')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.ResumeThread.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    process = thread = None
    descriptors: list[int] = []
    readers: list[threading.Thread] = []
    messages: queue.Queue[tuple[str, bytes | None]] = queue.Queue(maxsize=64)
    started = time.time()
    try:
        limits = ExtendedJobLimits()
        limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE; no BREAKAWAY_OK
        if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise OSError('ACCESS_DENIED')
        out_read, out_write = os.pipe()
        descriptors.extend((out_read, out_write))
        err_read, err_write = os.pipe()
        descriptors.extend((err_read, err_write))
        null = os.open(os.devnull, os.O_RDONLY)
        descriptors.append(null)
        handles = [msvcrt.get_osfhandle(fd) for fd in (null, out_write, err_write)]
        for handle in handles:
            os.set_handle_inheritable(handle, True)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags = subprocess.STARTF_USESTDHANDLES | subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        startup.hStdInput, startup.hStdOutput, startup.hStdError = handles
        startup.lpAttributeList = {'handle_list': handles}
        command = subprocess.list2cmdline([plan['executable'], *plan['argv']])
        # Explicit applicationName, argv quoting, no shell, hidden+suspended.
        process, thread, _, _ = api.CreateProcess(plan['executable'], command, None, None, True,
            0x4 | 0x08000000, environment, cwd, startup)
        for fd in (out_write, err_write, null):
            os.close(fd)
            descriptors.remove(fd)
        if not kernel.AssignProcessToJobObject(job, process):
            raise OSError('ACCESS_DENIED')
        before_resume()
        if cancelled.is_set():
            raise InterruptedError()
        if kernel.ResumeThread(thread) == 0xFFFFFFFF:
            raise OSError('ACCESS_DENIED')
        api.CloseHandle(thread)
        thread = None

        def read(fd: int, stream: str) -> None:
            try:
                while data := os.read(fd, 1024):
                    messages.put((stream, data))
            finally:
                messages.put((stream, None))

        for fd, stream in ((out_read, 'stdout'), (err_read, 'stderr')):
            reader = threading.Thread(target=read, args=(fd, stream), daemon=True)
            reader.start()
            readers.append(reader)
        deadline = time.monotonic() + plan['timeoutMs'] / 1000
        output: list[dict[str, Any]] = []
        total = 0
        ended = 0
        status = 'running'
        root_exited = False
        truncated = False
        # Kill the entire job even when only a detached grandchild survives.
        decoders = {stream: codecs.getincrementaldecoder('utf-8')('replace') for stream in ('stdout', 'stderr')}
        while ended < 2 or status == 'running':
            if status == 'running':
                if cancelled.is_set():
                    status = 'cancelled'
                elif time.monotonic() >= deadline:
                    status = 'timed_out'
                elif api.WaitForSingleObject(process, 0) == 0:
                    root_exited = True
                    status = 'succeeded' if api.GetExitCodeProcess(process) == 0 else 'failed'
                if status != 'running':
                    kernel.TerminateJobObject(job, 1)
            try:
                stream, data = messages.get(timeout=.02)
            except queue.Empty:
                continue
            if data is None:
                ended += 1
                tail = decoders[stream].decode(b'', final=True)
                if tail:
                    output.append({'sequence': len(output), 'stream': stream, 'text': tail, 'bytesBase64': ''})
                continue
            remaining = max(0, plan['outputLimit'] - total)
            accepted = data[:remaining]
            if accepted:
                output.append({'sequence': len(output), 'stream': stream, 'text': decoders[stream].decode(accepted), 'bytesBase64': base64.b64encode(accepted).decode()})
                total += len(accepted)
            if len(data) > remaining:
                truncated = True
                status = 'output_limit'
                kernel.TerminateJobObject(job, 1)
        for reader in readers:
            reader.join(timeout=2)
        api.WaitForSingleObject(process, 5000)
        accounting = JobAccounting()
        stop_deadline = time.monotonic() + 3
        while True:
            if not kernel.QueryInformationJobObject(job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                raise OSError('ACCESS_DENIED')
            if accounting.active == 0:
                break
            if time.monotonic() >= stop_deadline:
                raise OSError('PROCESS_TREE_STOP_UNCONFIRMED')
            time.sleep(.01)
        exit_code = api.GetExitCodeProcess(process) if root_exited else None
        return {'status': status, 'exitCode': exit_code, 'output': output, 'truncated': truncated,
            'startedAt': started, 'finishedAt': time.time(), 'treeTerminated': True,
            'limitations': ['same-user-code-not-sandboxed', 'raw-output-utf8-replacement', 'cross-stream-order-is-read-order']}
    except InterruptedError:
        return {'status': 'cancelled', 'exitCode': None, 'output': [], 'truncated': False,
            'startedAt': started, 'finishedAt': time.time(), 'treeTerminated': True,
            'limitations': ['cancelled-before-user-code-resumed']}
    finally:
        if process:
            # On setup failure the still-suspended process must never resume.
            kernel.TerminateProcess(process, 1)
        if job:
            kernel.TerminateJobObject(job, 1)
        if thread:
            api.CloseHandle(thread)
        if process:
            api.CloseHandle(process)
        if job:
            kernel.CloseHandle(job)
        for fd in descriptors:
            os.close(fd)
