from __future__ import annotations

import base64
import ctypes
import hashlib
import os
import sys
import tempfile
import threading
import unittest
from ctypes import wintypes
from pathlib import Path
from typing import Any

from app.workspace.runner import execute, runtime_pins
from app.workspace.scoped_files import ScopeError
from app.workspace.snapshots import fingerprint


class RunnerTests(unittest.TestCase):
    def run_fixture(self, source: str, *, timeout: int = 3000, limit: int = 65536,
                    args: list[str] | None = None, cancelled: threading.Event | None = None) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix='gitviz-p07-한글 ') as directory:
            file = Path(directory) / 'fixture space.py'
            file.write_text(source, encoding='utf-8')
            environment = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'TEMP', 'TMP') if key in os.environ}
            return execute({'executable': sys.executable, 'argv': ['-X', 'utf8', '-B', str(file), *(args or [])],
                'timeoutMs': timeout, 'outputLimit': limit}, directory, environment, cancelled or threading.Event())

    def test_argv_unicode_spaces_metacharacters_and_separate_streams(self) -> None:
        value = '한글 space " & | > ; $(echo x) -leading'
        result = self.run_fixture('import sys\nprint(sys.argv[1])\nprint("err",file=sys.stderr)\n', args=[value])
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['exitCode'], 0)
        self.assertEqual(''.join(item['text'] for item in result['output'] if item['stream'] == 'stdout').strip(), value)
        self.assertEqual(''.join(item['text'] for item in result['output'] if item['stream'] == 'stderr').strip(), 'err')

    def test_failure_is_not_success(self) -> None:
        result = self.run_fixture('raise SystemExit(7)\n')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['exitCode'], 7)

    def test_non_utf8_output_retains_original_bytes(self) -> None:
        result = self.run_fixture('import os\nos.write(1,bytes([255,0,128,10]))\n')
        self.assertEqual(result['status'], 'succeeded')
        raw = b''.join(base64.b64decode(item['bytesBase64'], validate=True) for item in result['output'] if item['stream'] == 'stdout')
        self.assertEqual(raw, bytes([255, 0, 128, 10]))

    def test_closed_pipes_do_not_bypass_timeout(self) -> None:
        result = self.run_fixture('import os,time\nos.close(1)\nos.close(2)\ntime.sleep(20)\n', timeout=250)
        self.assertEqual(result['status'], 'timed_out')
        self.assertIsNone(result['exitCode'])

    def test_cancellation_and_output_flood_are_bounded(self) -> None:
        cancelled = threading.Event()
        timer = threading.Timer(.15, cancelled.set)
        timer.start()
        try:
            result = self.run_fixture('import time\ntime.sleep(20)\n', cancelled=cancelled)
            self.assertEqual(result['status'], 'cancelled')
        finally:
            timer.cancel()
        result = self.run_fixture('import sys\nwhile True: sys.stdout.write("x"*4096);sys.stdout.flush()\n', limit=9000)
        self.assertEqual(result['status'], 'output_limit')
        self.assertTrue(result['truncated'])
        self.assertLessEqual(sum(len(item['text'].encode()) for item in result['output']), 9000)

    def test_grandchild_does_not_survive_parent_exit(self) -> None:
        source = 'import subprocess,sys\np=subprocess.Popen([sys.executable,"-B","-c","import time;time.sleep(30)"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\nprint(p.pid,flush=True)\n'
        result = self.run_fixture(source)
        self.assertEqual(result['status'], 'succeeded')
        pid = int(''.join(item['text'] for item in result['output'] if item['stream'] == 'stdout').strip())
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if handle:
            try:
                self.assertEqual(kernel.WaitForSingleObject(handle, 3000), 0)
            finally:
                kernel.CloseHandle(handle)
        self.assertLess(result['finishedAt'] - result['startedAt'], 4)

    def test_relative_executable_never_runs(self) -> None:
        with self.assertRaises(ValueError):
            execute({'executable': 'python.exe', 'argv': []}, '.', {}, threading.Event())

    def test_pre_resume_failure_never_executes_user_code_and_runtime_bytes_stay_pinned(self) -> None:
        with tempfile.TemporaryDirectory(prefix='gitviz-p07-preflight-') as temporary:
            marker = Path(temporary) / 'must-not-exist'
            plan = {'executable': sys.executable, 'argv': ['-B', '-c', f'open({str(marker)!r},"w").write("bad")'], 'timeoutMs': 1000, 'outputLimit': 4096}
            def changed() -> None:
                raise ScopeError('PRECONDITION_FAILED')
            with self.assertRaisesRegex(ScopeError, 'PRECONDITION_FAILED'):
                execute(plan, temporary, {key: os.environ[key] for key in ('SystemRoot', 'WINDIR') if key in os.environ}, threading.Event(), changed)
            self.assertFalse(marker.exists())
            binary = Path(temporary) / 'runtime.fixture'
            binary.write_bytes(b'fixed runtime bytes')
            expected = {fingerprint(str(binary)): hashlib.sha256(binary.read_bytes()).hexdigest()}
            with runtime_pins([binary], expected), self.assertRaises(PermissionError):
                binary.write_bytes(b'changed')
            self.assertEqual(binary.read_bytes(), b'fixed runtime bytes')
