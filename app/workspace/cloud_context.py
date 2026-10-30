"""Main-attested public mapping and secret-free one-shot transport handoff."""
from __future__ import annotations

import time
import base64
import queue
import threading
from typing import Any

from .remote_connections import RemoteConnections
from .scoped_files import ScopeError
from .review_transport import Reply, TransportFailure

CLOUD_PROFILES = ('claude-opus-5-5-v1', 'claude-sonnet-5-5-v1', 'gpt-5.4-v1', 'gemini-3.5-flash-lite-v1')


class CloudContexts:
    def __init__(self, remote: RemoteConnections):
        self.remote = remote
        self.values: dict[str, dict[str, Any]] = {}
        self.pending: dict[str, tuple[dict[str, Any], queue.Queue[dict[str, Any]]]] = {}
        self.lock = threading.Lock()

    def authorize(self, project: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        self.values = {key: value for key, value in self.values.items() if value['expiresAt'] > now}
        if payload['phase'] == 'revoke':
            self.values.clear()
            return {'projectId': project, 'accepted': True}
        proof = payload['proof']
        if (payload['profileId'] not in CLOUD_PROFILES or proof['visibility'] != 'public'
                or not now < proof['expiresAt'] <= now + 31):
            raise ScopeError('CLOUD_MAPPING_REQUIRED')
        inspected = self.remote.inspect(project)
        connection = inspected['connection']
        names = ('connectionId', 'accountId', 'serverOrigin', 'repositoryId', 'githubId', 'canonicalUrl', 'configHash')
        if (connection is None or inspected['configHash'] != proof['configHash']
                or any(connection[name] != proof[name] for name in names)):
            raise ScopeError('CLOUD_MAPPING_REQUIRED')
        if payload['credentialRef'] not in self.values and len(self.values) >= 16:
            raise ScopeError('BUSY')
        self.values[payload['credentialRef']] = {name: payload[name] for name in ('profileId', 'credentialRef', 'credentialVersion', 'proof')}
        self.values[payload['credentialRef']].update(projectId=project, expiresAt=proof['expiresAt'])
        return {'projectId': project, 'accepted': True}

    def get(self, project: str, profile: str, reference: str | None = None) -> dict[str, Any]:
        candidates = [v for key, v in self.values.items() if (reference is None or key == reference)
                      and v['projectId'] == project and v['profileId'] == profile and v['expiresAt'] > time.time()]
        if len(candidates) != 1:
            raise ScopeError('CLOUD_MAPPING_REQUIRED')
        value = candidates[0]
        inspected = self.remote.inspect(project)
        if inspected['connection'] is None or inspected['connection']['connectionId'] != value['proof']['connectionId'] or inspected['configHash'] != value['proof']['configHash']:
            raise ScopeError('CLOUD_MAPPING_REQUIRED')
        return value

    def transport(self, profile: dict[str, Any], system: str, user: str, cancelled: threading.Event) -> Reply:
        reference = str(profile.get('credentialRef'))
        value = self.values.pop(reference, None)
        if value is None or value['expiresAt'] <= time.time() or value['profileId'] != profile['id']:
            raise TransportFailure('PROVIDER_BLOCKED', False)
        from .cloud_transport import body, parse
        from .snapshots import image_hash
        wire = body(profile, system, user)
        if len(wire) > 163840:
            raise TransportFailure('OUTPUT_LIMIT', False)
        result: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        request = {'projectId': value['projectId'], 'ready': True, 'profileId': profile['id'], 'provider': profile['provider'],
                   'destination': profile['endpoint'], 'credentialRef': reference, 'requestHash': image_hash(wire),
                   'requestBytes': len(wire), 'bodyBase64': base64.b64encode(wire).decode(), 'claimed': False}
        with self.lock:
            self.pending[reference] = (request, result)
        deadline = time.monotonic() + 95
        try:
            while time.monotonic() < deadline:
                if cancelled.is_set():
                    raise TransportFailure('CANCELLED', bool(request['claimed']))
                try:
                    reply = result.get(timeout=.05)
                except queue.Empty:
                    continue
                if reply['errorCode'] is not None:
                    raise TransportFailure(reply['errorCode'], reply['uncertain'])
                if reply['httpStatus'] != 200:
                    raise TransportFailure('PROVIDER_ERROR', reply['httpStatus'] not in (400, 401, 403, 404, 429))
                return parse(profile, base64.b64decode(reply['rawBase64'], validate=True))
            raise TransportFailure('PROVIDER_TIMEOUT', bool(request['claimed']))
        finally:
            with self.lock:
                self.pending.pop(reference, None)

    def exchange(self, project: str, reference: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            pending = self.pending.get(reference)
            if pending is None:
                return {'projectId': project, 'ready': False}
            request, result = pending
            if request['projectId'] != project:
                raise ScopeError('RESOURCE_NOT_FOUND')
            if payload['phase'] == 'take':
                if request['claimed']:
                    raise ScopeError('CONFLICT')
                request['claimed'] = True
                return {key: value for key, value in request.items() if key != 'claimed'}
            if not request['claimed'] or result.full():
                raise ScopeError('CONFLICT')
            result.put_nowait(payload)
            return {'projectId': project, 'ready': False}
