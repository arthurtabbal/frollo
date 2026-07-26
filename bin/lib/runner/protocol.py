"""Canonical Frollo event protocol helpers.

The payload contracts are still being derived from the Claude and Codex
adapters, but the v0 envelope is stable enough to centralize here. Keeping the
known event names and envelope shape in one module avoids provider-local event
dialects while the protocol hardens.
"""

from datetime import datetime, timezone


SCHEMA = "frollo.event.v0"


EVENT_KINDS = frozenset({
    "approval.requested",
    "approval.resolved",
    "command.failed",
    "command.finished",
    "command.output.delta",
    "command.started",
    "diff.updated",
    "error",
    "file.change.delta",
    "file.change.finished",
    "file.change.started",
    "file.read",
    "file.search",
    "message.assistant.completed",
    "message.assistant.delta",
    "message.user",
    "notice",
    "plan.updated",
    "quota.updated",
    "reasoning.completed",
    "reasoning.delta",
    "reasoning.started",
    "reasoning.summary.started",
    "session.finished",
    "session.started",
    "tool.failed",
    "tool.finished",
    "tool.output.delta",
    "tool.started",
    "turn.failed",
    "turn.finished",
    "turn.interrupted",
    "turn.started",
    "usage.updated",
    "web.search.finished",
    "web.search.started",
})


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def make_event(
    kind,
    payload,
    *,
    seq,
    provider,
    session_id=None,
    turn_id=None,
    item_id=None,
    parent_item_id=None,
    raw=None,
    ts=None,
):
    event = {
        "schema": SCHEMA,
        "kind": kind,
        "ts": ts or utc_now(),
        "seq": seq,
        "provider": provider,
        "session_id": session_id,
        "turn_id": turn_id,
        "item_id": item_id,
        "parent_item_id": parent_item_id,
        "payload": payload,
        "raw": raw,
    }
    return validate_event(event)


def validate_event(event):
    if not isinstance(event, dict):
        raise TypeError("evento Frollo deve ser um dict")
    missing = [field for field in (
        "schema",
        "kind",
        "ts",
        "seq",
        "provider",
        "session_id",
        "turn_id",
        "item_id",
        "parent_item_id",
        "payload",
        "raw",
    ) if field not in event]
    if missing:
        raise ValueError(f"evento Frollo sem campos obrigatórios: {', '.join(missing)}")
    if event["schema"] != SCHEMA:
        raise ValueError(f"schema Frollo inválido: {event['schema']!r}")
    if event["kind"] not in EVENT_KINDS:
        raise ValueError(f"kind Frollo desconhecido: {event['kind']!r}")
    if not isinstance(event["seq"], int) or event["seq"] < 1:
        raise ValueError("seq Frollo deve ser inteiro positivo")
    if not isinstance(event["provider"], dict):
        raise TypeError("provider Frollo deve ser um dict")
    if not isinstance(event["payload"], dict):
        raise TypeError("payload Frollo deve ser um dict")
    return event
