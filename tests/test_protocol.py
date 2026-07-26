import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from lib.runner.protocol import EVENT_KINDS, SCHEMA, make_event, validate_event


def test_make_event_constroi_envelope_v0():
    event = make_event(
        "message.assistant.delta",
        {"delta": "oi"},
        seq=1,
        provider={"name": "codex", "surface": "app-server"},
        session_id="sess-1",
        turn_id="turn-1",
        item_id="msg-1",
        raw={"method": "item/agentMessage/delta"},
        ts="2026-07-26T12:00:00.000Z",
    )

    assert event == {
        "schema": SCHEMA,
        "kind": "message.assistant.delta",
        "ts": "2026-07-26T12:00:00.000Z",
        "seq": 1,
        "provider": {"name": "codex", "surface": "app-server"},
        "session_id": "sess-1",
        "turn_id": "turn-1",
        "item_id": "msg-1",
        "parent_item_id": None,
        "payload": {"delta": "oi"},
        "raw": {"method": "item/agentMessage/delta"},
    }


def test_make_event_rejeita_kind_fora_do_contrato():
    with pytest.raises(ValueError, match="kind Frollo desconhecido"):
        make_event(
            "provider.local.shape",
            {},
            seq=1,
            provider={"name": "codex"},
        )


def test_validate_event_exige_campos_obrigatorios():
    with pytest.raises(ValueError, match="payload"):
        validate_event({
            "schema": SCHEMA,
            "kind": "notice",
            "ts": "2026-07-26T12:00:00.000Z",
            "seq": 1,
            "provider": {},
            "session_id": None,
            "turn_id": None,
            "item_id": None,
            "parent_item_id": None,
            "raw": None,
        })


def test_event_kinds_cobrem_familias_do_protocolo_atual():
    assert {
        "message.assistant.delta",
        "reasoning.delta",
        "command.started",
        "file.change.finished",
        "approval.requested",
        "usage.updated",
        "quota.updated",
        "error",
    } <= EVENT_KINDS
