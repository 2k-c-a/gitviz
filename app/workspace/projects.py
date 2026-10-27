"""Local project identities, native selection tickets and CAS settings."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .git_service import LocalGit
from .scoped_files import ScopeError, discover, verify


class Projects:
    def __init__(self, connection: sqlite3.Connection, git: LocalGit | None = None):
        self.connection = connection
        self.git = git
        self.tickets: dict[str, tuple[float, dict[str, str]]] = {}

    def inspect(self, selected: str, metadata: list[str], expanded: bool, reconnect: bool = False) -> dict[str, Any]:
        try:
            grant = discover(Path(selected), set(metadata), expanded)
        except ScopeError as error:
            if error.code in ("ROOT_GRANT_REQUIRED", "METADATA_GRANT_REQUIRED"):
                return {"selectionId": None, "confirmation": {"kind": error.code, "path": error.requested_path}}
            raise
        previous = self.connection.execute("SELECT id,canonical_root,version FROM local_project WHERE root_identity=?", (grant["rootIdentity"],)).fetchone()
        if previous and previous[1] != grant["root"].casefold():
            if not reconnect:
                return {"selectionId": None, "confirmation": {"kind": "PROJECT_RECONNECT_REQUIRED", "path": previous[1] + " → " + grant["root"]}}
            grant["reconnectProjectId"] = previous[0]
            grant["reconnectVersion"] = str(previous[2])
        now = time.monotonic()
        self.tickets = {key: value for key, value in self.tickets.items() if value[0] > now}
        if len(self.tickets) >= 16:
            raise ScopeError("BUSY")
        ticket = str(uuid.uuid4())
        self.tickets[ticket] = (now + 60, grant)
        return {"selectionId": ticket, "confirmation": None}

    def _get(self, project_id: str) -> tuple[sqlite3.Row | tuple[Any, ...], dict[str, str]]:
        row = self.connection.execute("SELECT id,display_name,version,metadata_grant FROM local_project WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise ScopeError("RESOURCE_NOT_FOUND")
        return row, json.loads(row[3])

    def grant(self, project_id: str) -> dict[str, str]:
        return self._get(project_id)[1]

    def get(self, project_id: str) -> dict[str, Any]:
        row, grant = self._get(project_id)
        try:
            verify(grant)
            state = "available"
        except (ScopeError, OSError):
            state = "unavailable"
        settings = {key: {"value": json.loads(value), "version": version} for key, value, version in
                    self.connection.execute("SELECT key,value,version FROM project_setting WHERE project_id=?", (project_id,))}
        return {"id": row[0], "displayName": row[1], "version": row[2], "accessState": state,
                "remoteRepositoryId": None, "settings": settings}

    def list(self, cursor: str | None, limit: int) -> dict[str, Any]:
        rows = self.connection.execute("SELECT id FROM local_project WHERE id>? ORDER BY id LIMIT ?", (cursor or "", limit + 1)).fetchall()
        items = [self.get(row[0]) for row in rows[:limit]]
        return {"items": items, "nextCursor": items[-1]["id"] if len(rows) > limit else None}

    def mutate(self, scope: str, operation: str, key: str, payload: dict[str, Any], action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            prior = self.connection.execute("SELECT body_hash,result FROM local_operation WHERE project_scope=? AND operation=? AND idempotency_key=?", (scope, operation, key)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise ScopeError("CONFLICT")
                result = json.loads(prior[1])
            else:
                result = action()
                self.connection.execute("INSERT INTO local_operation VALUES(?,?,?,?,?)", (scope, operation, key, fingerprint, json.dumps(result)))
            self.connection.commit()
            return result
        except Exception:
            self.connection.rollback()
            raise

    def register(self, ticket: str, key: str) -> dict[str, Any]:
        # Keep consumed tickets until expiry so a lost response can be retried.
        def action() -> dict[str, Any]:
            value = self.tickets.get(ticket)
            if not value or value[0] < time.monotonic():
                raise ScopeError("SELECTION_EXPIRED")
            grant = value[1]
            verify(grant)
            # Validate Git and ownership before recording a successful registration.
            (self.git or LocalGit()).status(grant, 1)
            normalized = str(Path(grant["root"])).casefold()
            if "reconnectProjectId" in grant:
                changed = self.connection.execute("UPDATE local_project SET canonical_root=?,git_dir=?,common_dir=?,metadata_grant=?,version=version+1 WHERE id=? AND root_identity=? AND version=?", (normalized, grant["gitDir"], grant["commonDir"], json.dumps(grant), grant["reconnectProjectId"], grant["rootIdentity"], int(grant["reconnectVersion"])))
                if changed.rowcount != 1:
                    raise ScopeError("CONFLICT")
                return self.get(grant["reconnectProjectId"])
            existing = self.connection.execute("SELECT id,root_identity FROM local_project WHERE canonical_root=?", (normalized,)).fetchone()
            if existing:
                if existing[1] != grant["rootIdentity"]:
                    raise ScopeError("PROJECT_MOVED")
                return self.get(existing[0])
            project_id = str(uuid.uuid4())
            self.connection.execute("INSERT INTO local_project VALUES(?,?,?,?,?,?,?,1)",
                                    (project_id, normalized, grant["rootIdentity"], grant["gitDir"], grant["commonDir"], json.dumps(grant), Path(grant["root"]).name))
            self.connection.execute("INSERT INTO project_setting VALUES(?,?,?,1)", (project_id, "recentLimit", "20"))
            self.connection.execute("INSERT INTO project_setting VALUES(?,?,?,1)", (project_id, "editorId", json.dumps("vscode")))
            return self.get(project_id)

        return self.mutate("global", "register", key, {"selectionId": ticket}, action)

    def update(self, project_id: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            self._get(project_id)
            updated = self.connection.execute("UPDATE project_setting SET value=?,version=version+1 WHERE project_id=? AND key='recentLimit' AND version=?", (json.dumps(payload["recentLimit"]), project_id, payload["expectedVersion"]))
            if updated.rowcount != 1:
                raise ScopeError("CONFLICT")
            return self.get(project_id)
        return self.mutate(project_id, "settings", key, payload, action)

    def unregister(self, project_id: str, version: int, key: str) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            row, _ = self._get(project_id)
            if row[2] != version:
                raise ScopeError("CONFLICT")
            if (self.connection.execute('SELECT 1 FROM review_run WHERE project_id=? LIMIT 1', (project_id,)).fetchone()
                    or self.connection.execute('SELECT 1 FROM validation_run WHERE project_id=? LIMIT 1', (project_id,)).fetchone()
                    or self.connection.execute('SELECT 1 FROM comparison WHERE project_id=? LIMIT 1', (project_id,)).fetchone()
                    or self.connection.execute('SELECT 1 FROM impact_analysis WHERE project_id=? LIMIT 1', (project_id,)).fetchone()):
                raise ScopeError('REVIEW_DATA_REQUIRES_EXPLICIT_REMOVAL')
            self.connection.execute("DELETE FROM local_project WHERE id=?", (project_id,))
            return {"unregistered": True}
        return self.mutate(project_id, "unregister", key, {"expectedVersion": version}, action)

    def set_editor(self, project_id: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            self._get(project_id)
            if payload['editorId'] not in ('vscode', 'cursor'):
                raise ScopeError('INVALID_REQUEST')
            updated = self.connection.execute("UPDATE project_setting SET value=?,version=version+1 WHERE project_id=? AND key='editorId' AND version=?",
                (json.dumps(payload['editorId']), project_id, payload['expectedVersion']))
            if updated.rowcount != 1:
                raise ScopeError('CONFLICT')
            return self.get(project_id)
        return self.mutate(project_id, 'setEditor', key, payload, action)

    def status(self, project_id: str, recent_limit: int) -> dict[str, Any]:
        _, grant = self._get(project_id)
        return {"projectId": project_id, **(self.git or LocalGit()).status(grant, recent_limit)}
