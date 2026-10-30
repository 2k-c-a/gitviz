"""Foundation state machine. No project, shell, network or server operations."""
from __future__ import annotations

import base64
import json
import sqlite3
import os
import uuid
from pathlib import Path
from typing import Any, Callable

from .contracts import OPERATIONS, ProtocolError, validate
from .crypto import PayloadCipher
from .store import LocalStore, reject_links
from .projects import Projects
from .scoped_files import ScopeError
from .snapshots import Snapshots
from .detection import Detection
from .migrations import SCHEMA_VERSION
from .review import Reviews
from .proposals import Proposals
from .apply import Application
from .impact import Impact
from .validation import Validation
from .comparisons import Comparisons
from .notifications import Notifications
from .remote_connections import RemoteConnections
from . import events


class Dispatcher:
    def __init__(self, directory: Path, secure: Callable[[Path], None]):
        self.directory = directory
        self.secure = secure
        self.store: LocalStore | None = None
        self.negotiated = False
        self.bootstrapped = False
        self.stopping = False
        self.state = "unavailable"
        self.error_code: str | None = None
        self.projects: Projects | None = None
        self.snapshots: Snapshots | None = None
        self.detection: Detection | None = None
        self.reviews: Reviews | None = None
        self.proposals: Proposals | None = None
        self.application: Application | None = None
        self.impact: Impact | None = None
        self.validation: Validation | None = None
        self.comparisons: Comparisons | None = None
        self.notifications: Notifications | None = None
        self.remote_connections: RemoteConnections | None = None

    def bootstrap(self, message: Any) -> None:
        validate("bootstrap", message)
        if not self.negotiated or self.bootstrapped:
            raise ProtocolError("INVALID_REQUEST")
        self.bootstrapped = True
        try:
            key = base64.b64decode(message["keyBase64"], validate=True)
            cipher = PayloadCipher(key, message["keyVersion"])
            self.store = LocalStore(self.directory, self.secure)
            self.store.open()
            probe = self.directory / "secret-check.bin"
            reject_links(probe)
            aad = b"GitVizWorkspace/system/secret-check/schema-1"
            if probe.exists():
                with probe.open("rb") as stream:
                    if cipher.decrypt(stream.read(4097), aad) != b"workspace-foundation-v1":
                        raise ValueError("DECRYPTION_FAILED")
            else:
                pending = self.directory / f"secret-check.{uuid.uuid4()}.pending"
                try:
                    with pending.open("xb") as stream:
                        self.secure(pending)
                        stream.write(cipher.encrypt(b"workspace-foundation-v1", aad))
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.link(pending, probe)  # Exclusive publication, never replace an existing probe.
                finally:
                    pending.unlink(missing_ok=True)
                self.secure(probe)
            self.state = "ready"
            assert self.store.connection is not None
            self.projects = Projects(self.store.connection)
            self.snapshots = Snapshots(self.projects, cipher)
            self.detection = Detection(self.snapshots)
            self.reviews = Reviews(self.snapshots)
            self.proposals = Proposals(self.reviews)
            self.application = Application(self.proposals)
            self.impact = Impact(self.proposals)
            self.validation = Validation(self.detection)
            self.comparisons = Comparisons(self.validation)
            self.notifications = Notifications(self.projects)
            self.remote_connections = RemoteConnections(self.projects)
        except ValueError as error:
            self.state = "unavailable"
            self.error_code = "UNSUPPORTED_PROTOCOL" if str(error) == "UNSUPPORTED_SCHEMA" else "RECOVERY_REQUIRED"
            self.close()
        except Exception:
            self.state = "unavailable"
            self.error_code = "STORAGE_UNAVAILABLE"
            self.close()

    def dispatch(self, request: Any) -> dict[str, Any]:
        validate("request", request)
        response: dict[str, Any] = {"protocolVersion": 1, "requestId": request["requestId"]}
        op, payload = request["operation"], request["payload"]
        if op == OPERATIONS[0]:
            bounds = payload["localSchemaRange"]
            if self.negotiated or self.bootstrapped or not bounds["min"] <= SCHEMA_VERSION <= bounds["max"]:
                return {**response, "ok": False, "error": {"code": "UNSUPPORTED_PROTOCOL", "message": "호환되는 초기화 계약이 필요합니다.", "retryable": False, "details": {}}}
            self.negotiated = True
            reject_links(self.directory)
            self.directory.mkdir(parents=True, exist_ok=True)
            self.secure(self.directory)
            result = {"agentVersion": "0.8.0", "protocolVersion": 1,
                      "localSchemaRange": {"min": SCHEMA_VERSION, "max": SCHEMA_VERSION}, "capabilities": list(OPERATIONS), "state": "initializing"}
        elif not self.negotiated:
            return {**response, "ok": False, "error": {"code": "UNSUPPORTED_PROTOCOL", "message": "초기화가 필요합니다.", "retryable": False, "details": {}}}
        elif op == OPERATIONS[1]:
            result = {"state": self.state, "components": {"store": self.state, "secrets": self.state, "lifecycle": "ready"}}
            if self.error_code:
                result["errorCode"] = self.error_code
        elif op == OPERATIONS[2]:
            self.stopping = True
            result = {"accepted": True}
        else:
            try:
                if self.state != "ready" or self.projects is None:
                    raise ScopeError("STORAGE_UNAVAILABLE")
                projects = self.projects
                if self.validation is not None:
                    self.validation.drain()
                    if self.validation.active is not None and op in ('workspace.changes.capture', 'workspace.proposals.apply', 'workspace.proposals.recover', 'workspace.validation.start'):
                        # start replays must still reach their durable idempotency ledger.
                        if op != 'workspace.validation.start':
                            raise ScopeError('BUSY')
                if (self.proposals is not None and request.get('projectId') and self.proposals.recovery_required(request['projectId'])
                        and op in ('workspace.changes.capture', 'workspace.reviews.preview', 'workspace.reviews.start', 'workspace.validation.plan', 'workspace.validation.prepare', 'workspace.validation.approve', 'workspace.validation.start')):
                    raise ScopeError('RECOVERY_REQUIRED')
                if op.startswith('workspace.notifications.') or op == 'workspace.events.read':
                    # Terminal background work becomes durable events before the inbox is read.
                    assert self.reviews is not None and self.notifications is not None
                    self.reviews.jobs.drain()
                if op == 'workspace.notifications.list':
                    assert self.notifications is not None
                    result = self.notifications.list(payload)
                elif op == 'workspace.notifications.markRead':
                    assert self.notifications is not None
                    result = self.notifications.mark_read(request['projectId'], payload, request['idempotencyKey'])
                elif op == 'workspace.events.read':
                    result = events.read(projects.connection, payload)
                elif op == 'workspace.impact.historical':
                    assert self.impact is not None
                    result = self.impact.historical(request['projectId'], payload, request['idempotencyKey'])
                elif op == 'workspace.reviews.cloudExchange':
                    assert self.reviews is not None
                    result = self.reviews.cloud_exchange(request['projectId'], payload)
                elif op == 'workspace.reviews.cloudContext':
                    assert self.reviews is not None
                    result = self.reviews.cloud.authorize(request['projectId'], payload)
                elif op == 'workspace.projects.connectRemote':
                    assert self.remote_connections is not None
                    result = (self.remote_connections.inspect(request['projectId']) if payload['phase'] == 'inspect'
                              else self.remote_connections.connect(request['projectId'], payload, request['idempotencyKey']))
                elif op == 'workspace.remote.connections':
                    assert self.remote_connections is not None
                    result = self.remote_connections.connections(payload)
                elif op == 'workspace.remote.observeAnalysis':
                    assert self.remote_connections is not None
                    result = self.remote_connections.observe(request['projectId'], payload, request['idempotencyKey'])
                elif op == 'workspace.remote.getAnalysis':
                    assert self.remote_connections is not None
                    result = self.remote_connections.get(request['projectId'], payload)
                elif op == "workspace.projects.selectAndRegister":
                    result = (projects.inspect(payload["selectedRoot"], payload["metadataScopes"], payload["expandedRoot"], payload["reconnectApproved"])
                              if payload["phase"] == "inspect" else projects.register(payload["selectionId"], request["idempotencyKey"]))
                elif op == "workspace.projects.list":
                    result = projects.list(payload["cursor"], payload["limit"])
                elif op == "workspace.projects.get":
                    result = projects.get(request["projectId"])
                elif op == "workspace.projects.updateSettings":
                    result = projects.update(request["projectId"], payload, request["idempotencyKey"])
                elif op == 'workspace.projects.setEditor':
                    result = projects.set_editor(request['projectId'], payload, request['idempotencyKey'])
                elif op == "workspace.projects.unregister":
                    result = projects.unregister(request["projectId"], payload["expectedVersion"], request["idempotencyKey"])
                elif op == 'workspace.git.status':
                    result = projects.status(request["projectId"], payload["recentLimit"])
                elif op == 'workspace.changes.capture':
                    assert self.snapshots is not None
                    result = self.snapshots.capture(request['projectId'], payload, request['idempotencyKey'])
                elif op == 'workspace.files.preview':
                    assert self.snapshots is not None
                    result = self.snapshots.preview(request['projectId'], payload)
                elif op == 'workspace.validation.detect':
                    assert self.detection is not None
                    result = self.detection.detect(request['projectId'], payload)
                elif op == 'workspace.validation.plan':
                    assert self.detection is not None
                    result = self.detection.plan(request['projectId'], payload, request['idempotencyKey'])
                elif op == 'workspace.files.locate':
                    assert self.validation is not None
                    result = self.validation.locate(request['projectId'], payload)
                elif op.startswith('workspace.validation.'):
                    assert self.validation is not None
                    name = op.rsplit('.', 1)[1]
                    project = request['projectId']
                    if name in ('approve', 'start'):
                        result = getattr(self.validation, name)(project, payload, request['idempotencyKey'])
                    else:
                        result = getattr(self.validation, name)(project, payload)
                elif op.startswith('workspace.comparisons.'):
                    assert self.comparisons is not None
                    result = (self.comparisons.create(request['projectId'], payload, request['idempotencyKey']) if op.endswith('.create')
                              else self.comparisons.get(request['projectId'], payload))
                elif op.startswith('workspace.reviews.'):
                    assert self.reviews is not None
                    service = self.reviews
                    project = request['projectId']
                    if op.endswith('.preview'):
                        result = service.preview(project, payload)
                    elif op.endswith('.start'):
                        result = service.start(project, payload, request['idempotencyKey'])
                    elif op.endswith('.get'):
                        result = service.jobs.get(project, payload['runId'])
                    elif op.endswith('.list'):
                        result = service.list(project, payload)
                    elif op.endswith('.cancel'):
                        result = service.jobs.cancel(project, payload, request['idempotencyKey'])
                    else:
                        result = service.feedback(project, payload, request['idempotencyKey'])
                elif op.startswith('workspace.proposals.'):
                    assert self.proposals is not None and self.application is not None
                    service = self.proposals
                    application = self.application
                    project = request['projectId']
                    name = op.rsplit('.', 1)[1]
                    if name == 'listRecovery':
                        result = service.list_recovery(project)
                    elif name == 'create':
                        result = service.create(project, payload, request['idempotencyKey'])
                    elif name == 'get':
                        result = service.get(project, payload['proposalId'])
                    elif name == 'reject':
                        result = service.reject(project, payload, request['idempotencyKey'])
                    elif name == 'requestRevision':
                        result = service.request_revision(project, payload, request['idempotencyKey'])
                    elif name == 'approve':
                        result = service.approve(project, payload, request['idempotencyKey'])
                    elif name == 'apply':
                        result = application.apply(project, payload, request['idempotencyKey'])
                    elif name == 'recoveryPlan':
                        result = application.recovery_plan(project, payload['proposalId'])
                    elif name == 'approveRecovery':
                        result = application.approve_recovery(project, payload, request['idempotencyKey'])
                    else:
                        result = application.recover(project, payload, request['idempotencyKey'])
                else:
                    assert self.impact is not None
                    impact = self.impact
                    project = request['projectId']
                    name = op.rsplit('.', 1)[1]
                    if name == 'prepare':
                        result = impact.prepare(project, payload)
                    elif name == 'read':
                        result = impact.read(project, payload)
                    elif name == 'append':
                        result = impact.append(project, payload)
                    elif name == 'release':
                        result = impact.release(project, payload)
                    elif name == 'finish':
                        result = impact.finish(project, payload, request['idempotencyKey'])
                    else:
                        result = impact.get(project, payload)
                if len(json.dumps(result, ensure_ascii=False).encode()) > 200000:
                    if op in ('workspace.git.status', 'workspace.changes.capture', 'workspace.validation.detect'):
                        raise ScopeError('CHANGESET_TOO_LARGE', details={'operation': 'result', 'limitBytes': 200000,
                            'observedBytes': len(json.dumps(result, ensure_ascii=False).encode())})
                    raise ScopeError("OUTPUT_LIMIT")
            except ScopeError as error:
                return {**response, "ok": False, "error": {"code": error.code, "message": error.code, "retryable": False, "details": error.details if error.code == 'CHANGESET_TOO_LARGE' else {}}}
            except (OSError, ValueError):
                return {**response, "ok": False, "error": {"code": "ACCESS_DENIED", "message": "ACCESS_DENIED", "retryable": False, "details": {}}}
            except sqlite3.Error:
                return {**response, "ok": False, "error": {"code": "STORAGE_UNAVAILABLE", "message": "STORAGE_UNAVAILABLE", "retryable": False, "details": {}}}
        answer = {**response, "ok": True, "result": result}
        try:
            validate("response", answer)
        except ProtocolError:
            return {**response, "ok": False, "error": {"code": "OUTPUT_INVALID", "message": "OUTPUT_INVALID", "retryable": False, "details": {}}}
        return answer

    def close(self) -> None:
        try:
            if self.validation:
                self.validation.close()
            if self.reviews:
                self.reviews.jobs.close()
        finally:
            if self.store:
                self.store.close()
