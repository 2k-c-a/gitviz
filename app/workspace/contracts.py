"""Bounded, strict JSONL and shared JSON Schema validation."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, BinaryIO, cast

from jsonschema import Draft7Validator

MAX_FRAME = 262144
MAX_DEPTH = 16
OPERATIONS = (
    "workspace.system.handshake", "workspace.system.health", "workspace.system.shutdown",
    "workspace.projects.selectAndRegister", "workspace.projects.list", "workspace.projects.get",
    "workspace.projects.updateSettings", "workspace.projects.unregister", "workspace.git.status",
    "workspace.reviews.preview", "workspace.reviews.start", "workspace.reviews.get", "workspace.reviews.list", "workspace.reviews.cancel", "workspace.reviews.feedback",
    "workspace.changes.capture", "workspace.files.preview", "workspace.validation.detect", "workspace.validation.plan",
    "workspace.proposals.create", "workspace.proposals.get", "workspace.proposals.reject", "workspace.proposals.requestRevision", "workspace.proposals.approve", "workspace.proposals.apply", "workspace.proposals.recoveryPlan", "workspace.proposals.approveRecovery", "workspace.proposals.recover", "workspace.impact.prepare", "workspace.impact.read", "workspace.impact.append", "workspace.impact.finish", "workspace.impact.get",
    "workspace.projects.setEditor", "workspace.impact.release", "workspace.files.locate", "workspace.proposals.listRecovery",
    "workspace.validation.prepare", "workspace.validation.approve", "workspace.validation.start", "workspace.validation.get", "workspace.validation.cancel", "workspace.comparisons.create", "workspace.comparisons.get",
)
SCHEMA_ROOT = (Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
               / "contracts" / "workspace" / "v1")


class ProtocolError(Exception):
    """Contains classification only, never rejected payloads."""


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("INVALID_REQUEST")
        result[key] = value
    return result


def _constant(_: str) -> Any:
    raise ProtocolError("INVALID_REQUEST")


def check_depth(value: Any, depth: int = 0) -> None:
    if isinstance(value, (dict, list)):
        depth += 1
        if depth > MAX_DEPTH:
            raise ProtocolError("INVALID_REQUEST")
        collection = cast(dict[str, Any], value).values() if isinstance(value, dict) else cast(list[Any], value)
        for item in collection:
            check_depth(item, depth)


def decode(raw: bytes) -> Any:
    if not raw or len(raw) > MAX_FRAME:
        raise ProtocolError("INVALID_REQUEST")
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_pairs,
                           parse_constant=_constant)
        check_depth(value)
        # Reject overflow (1e999) and lone surrogates too.
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProtocolError("INVALID_REQUEST") from exc


def validate(kind: str, value: Any) -> None:
    if kind not in {"request", "response", "bootstrap"}:
        raise ProtocolError("INVALID_REQUEST")
    schema = json.loads((SCHEMA_ROOT / f"{kind}.schema.json").read_text(encoding="utf-8"))
    validator = cast(Any, Draft7Validator(schema))  # Third-party overload includes an untyped deprecated argument.
    if next(validator.iter_errors(value), None) is not None:
        raise ProtocolError("INVALID_REQUEST")


def read_frame(stream: BinaryIO) -> Any | None:
    raw = stream.readline(MAX_FRAME + 2)
    if not raw:
        return None
    if not raw.endswith(b"\n"):
        raise ProtocolError("INVALID_REQUEST")
    return decode(raw[:-1])


def encode(value: dict[str, Any]) -> bytes:
    validate("response", value)
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    if len(raw) > MAX_FRAME:
        raise ProtocolError("INVALID_REQUEST")
    return raw + b"\n"
