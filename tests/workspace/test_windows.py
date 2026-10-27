from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.workspace.windows import secure_path


@unittest.skipUnless(os.name == "nt", "Windows API fixture")
class WindowsTests(unittest.TestCase):
    def test_parent_exit_terminates_agent_and_grandchild(self):
        agent_code = """
import os,subprocess,sys,time
from app.workspace.windows import ProcessLifetime
lifetime=ProcessLifetime(os.getppid())
child=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(60)'])
print(child.pid,flush=True)
time.sleep(60)
"""
        parent_code = f"""
import subprocess,sys,time
agent=subprocess.Popen([sys.executable,'-B','-c',{agent_code!r}],stdout=subprocess.PIPE)
line=agent.stdout.readline()
print(str(agent.pid)+' '+line.decode().strip(),flush=True)
time.sleep(60)
"""
        parent = subprocess.Popen([getattr(sys, "_base_executable", sys.executable), "-B", "-c", parent_code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        handles: list[int] = []
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        try:
            assert parent.stdout
            pids = parent.stdout.readline().decode().split()
            self.assertEqual(len(pids), 2)
            for pid in pids:
                handle = kernel.OpenProcess(0x00100000, False, int(pid))
                self.assertTrue(handle)
                handles.append(handle)
            parent.kill()
            parent.wait(timeout=5)
            for handle in handles:
                self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0)
        finally:
            for handle in handles:
                kernel.CloseHandle(handle)
            if parent.poll() is None:
                parent.kill()
            parent.communicate(timeout=5)

    def test_current_user_acl_directory_and_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "한글 space"
            directory.mkdir()
            secure_path(directory)
            file = directory / "probe"
            file.write_bytes(b"fixture")
            secure_path(file)
            self.assertEqual(file.read_bytes(), b"fixture")

    def test_job_closes_on_agent_kill(self):
        code = """
import os,subprocess,sys,time
from app.workspace.windows import ProcessLifetime
lifetime=ProcessLifetime(os.getppid())
child=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(60)'])
print(child.pid,flush=True)
time.sleep(60)
"""
        agent = subprocess.Popen([getattr(sys, "_base_executable", sys.executable), "-B", "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        child_pid = None
        try:
            assert agent.stdout
            line = agent.stdout.readline()
            if not line:
                assert agent.stderr
                self.fail(agent.stderr.read().decode())
            child_pid = int(line)
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x00100000, False, child_pid)
            self.assertTrue(handle)
            try:
                agent.kill()
                agent.wait(timeout=5)
                self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0)
            finally:
                kernel.CloseHandle(handle)
        finally:
            if agent.poll() is None:
                agent.kill()
            agent.communicate(timeout=5)
