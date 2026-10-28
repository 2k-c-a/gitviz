from __future__ import annotations

import base64
import tempfile
import unittest
import os
import json
import subprocess
import sys
import queue
import threading
from pathlib import Path
from typing import Any

from app.workspace.contracts import ProtocolError, validate
from app.workspace.dispatcher import Dispatcher


def request(op: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"protocolVersion": 1, "requestId": "00000000-0000-4000-8000-000000000001",
            "operation": "workspace.system." + op, "payload": {} if payload is None else payload}


class DispatcherTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows termination fixture")
    def test_encrypted_probe_survives_each_publication_boundary(self):
        code = r'''
import sys,os,threading,base64
from pathlib import Path
sys.path.insert(0,sys.argv[3])
from app.workspace.dispatcher import Dispatcher
phase=sys.argv[1];directory=Path(sys.argv[2])
def checkpoint(name):
    if name==phase:
        print(name,flush=True);threading.Event().wait(20)
        raise RuntimeError('fixture not terminated')
opening=Path.open
class Stream:
    def __init__(self,raw):self.raw=raw
    def __enter__(self):return self
    def __exit__(self,*args):return self.raw.__exit__(*args)
    def __getattr__(self,name):return getattr(self.raw,name)
    def write(self,data):
        midpoint=len(data)//2
        self.raw.write(data[:midpoint]);self.raw.flush();checkpoint('probe_partial')
        self.raw.write(data[midpoint:]);return len(data)
def wrapped_open(path,*args,**kwargs):
    raw=opening(path,*args,**kwargs)
    return Stream(raw) if path.name.startswith('secret-check.') and path.name.endswith('.pending') else raw
Path.open=wrapped_open
sync=os.fsync
def wrapped_sync(fd):
    sync(fd);checkpoint('probe_synced')
os.fsync=wrapped_sync
link=os.link
def wrapped_link(*args,**kwargs):
    link(*args,**kwargs);checkpoint('probe_published')
os.link=wrapped_link
def secure(path):
    if path.name.startswith('secret-check.') and path.name.endswith('.pending'):checkpoint('probe_open')
d=Dispatcher(directory,secure)
d.dispatch({'protocolVersion':1,'requestId':'00000000-0000-4000-8000-000000000001','operation':'workspace.system.handshake','payload':{'appVersion':'0.1.0','protocolVersion':1,'localSchemaRange':{'min':7,'max':7}}})
d.bootstrap({'type':'bootstrap','protocolVersion':1,'keyVersion':1,'keyBase64':base64.b64encode(bytes(range(32))).decode()})
assert d.state=='ready'
checkpoint('ready')
raise RuntimeError('checkpoint not reached')
'''
        for phase in ("probe_open", "probe_partial", "probe_synced", "probe_published", "ready"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp) / "data"
                child = subprocess.Popen([getattr(sys, "_base_executable", sys.executable), "-B", "-c", code,
                                          phase, str(directory), str(Path(sys.prefix) / "Lib/site-packages")],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                lines: queue.Queue[bytes] = queue.Queue()
                assert child.stdout
                output = child.stdout
                threading.Thread(target=lambda: lines.put(output.readline()), daemon=True).start()
                try:
                    self.assertEqual(lines.get(timeout=10).decode().strip(), phase)
                    child.kill()
                    child.communicate(timeout=5)
                    probe = directory / "secret-check.bin"
                    before = probe.read_bytes() if probe.exists() else None
                    dispatcher = Dispatcher(directory, lambda _: None)
                    try:
                        dispatcher.dispatch(request("handshake", {"appVersion": "0.1.0", "protocolVersion": 1, "localSchemaRange": {"min": 7, "max": 7}}))
                        dispatcher.bootstrap({"type": "bootstrap", "protocolVersion": 1, "keyVersion": 1, "keyBase64": base64.b64encode(bytes(range(32))).decode()})
                        self.assertEqual(dispatcher.state, "ready")
                        if before is not None:
                            self.assertEqual(probe.read_bytes(), before)
                    finally:
                        dispatcher.close()
                finally:
                    if child.poll() is None:
                        child.kill()
                    child.communicate(timeout=5)

    @unittest.skipUnless(os.name == "nt", "actual Windows agent")
    def test_stdio_schema_mismatch_and_invalid_operation_do_not_create_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "not-opened"
            hello = request("handshake", {"appVersion": "0.1.0", "protocolVersion": 1, "localSchemaRange": {"min": 8, "max": 9}})
            # Windows venv python.exe is a redirector with a second PID. Use the
            # real interpreter so the tested parent handle is the actual parent.
            executable = getattr(sys, "_base_executable", sys.executable)
            code = f"import sys;sys.path.insert(0,{str(Path(sys.prefix) / 'Lib/site-packages')!r});from app.workspace.__main__ import main;raise SystemExit(main())"
            command = [executable, "-B", "-c", code, "--data-dir", str(directory), "--parent-pid", str(os.getpid())]
            result = subprocess.run(command,
                                    input=(json.dumps(hello) + "\n").encode(), capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "UNSUPPORTED_PROTOCOL")
            self.assertFalse(directory.exists())
            malicious = {**hello, "operation": "workspace.files.write", "payload": {"secret": "never-log-this-fixture"}}
            result = subprocess.run(command,
                                    input=(json.dumps(malicious) + "\n").encode(), capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn(b"never-log-this-fixture", result.stdout + result.stderr)
            self.assertFalse(directory.exists())

    def test_handshake_before_db_and_scope_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "data"
            dispatcher = Dispatcher(directory, lambda _: None)
            self.assertFalse(dispatcher.dispatch(request("health"))["ok"])
            result = dispatcher.dispatch(request("handshake", {"appVersion": "0.1.0", "protocolVersion": 1, "localSchemaRange": {"min": 8, "max": 9}}))
            self.assertFalse(result["ok"])
            self.assertFalse(directory.exists())
            with self.assertRaises(ProtocolError):
                dispatcher.dispatch(request("files.write"))

    def test_bootstrap_once_restart_key_and_shutdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "data"
            bootstrap = {"type": "bootstrap", "protocolVersion": 1, "keyVersion": 1,
                         "keyBase64": base64.b64encode(bytes(range(32))).decode()}
            for turn in range(3):
                dispatcher = Dispatcher(directory, lambda _: None)
                try:
                    hello = dispatcher.dispatch(request("handshake", {"appVersion": "0.1.0", "protocolVersion": 1, "localSchemaRange": {"min": 7, "max": 7}}))
                    validate("response", hello)
                    dispatcher.bootstrap({**bootstrap, "keyBase64": base64.b64encode(bytes(32)).decode()} if turn == 2 else bootstrap)
                    self.assertEqual(dispatcher.state, "unavailable" if turn == 2 else "ready")
                    validate("response", dispatcher.dispatch(request("health")))
                    with self.assertRaises(ProtocolError):
                        dispatcher.bootstrap(bootstrap)
                    self.assertTrue(dispatcher.dispatch(request("shutdown"))["result"]["accepted"])
                finally:
                    dispatcher.close()
