import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from lib.runner.codex import _CodexAdapter


def _adapter():
    client = MagicMock()
    client.session_id = None
    client.observed_model = ""
    return _CodexAdapter(client)


class TestCodexAdapter:
    def test_mapeia_delta_e_completion_de_assistente(self):
        adapter = _adapter()
        adapter.session_id = "thread-1"
        adapter.turn_id = "turn-1"

        events = adapter.normalize({
            "method": "item/agentMessage/delta",
            "params": {"turnId": "turn-1", "itemId": "msg-1", "delta": "oi"},
        })
        assert events[0]["kind"] == "message.assistant.delta"
        assert events[0]["payload"]["delta"] == "oi"
        assert events[0]["item_id"] == "msg-1"

        events = adapter.normalize({
            "method": "item/completed",
            "params": {
                "turnId": "turn-1",
                "item": {"type": "agentMessage", "id": "msg-1", "text": "oi", "phase": "final_answer"},
            },
        })
        assert events[0]["kind"] == "message.assistant.completed"
        assert events[0]["payload"]["text"] == "oi"
        assert events[0]["payload"]["phase"] == "final_answer"

    def test_mapeia_command_execution_falha_sem_falhar_turno(self):
        adapter = _adapter()
        adapter.session_id = "thread-1"
        adapter.turn_id = "turn-1"

        events = adapter.normalize({
            "method": "item/completed",
            "params": {
                "turnId": "turn-1",
                "item": {
                    "type": "commandExecution",
                    "id": "cmd-1",
                    "command": "exit 7",
                    "cwd": "/repo",
                    "source": "userShell",
                    "status": "failed",
                    "aggregatedOutput": "boom\n",
                    "exitCode": 7,
                    "durationMs": 12,
                },
            },
        })
        assert events[0]["kind"] == "command.failed"
        assert events[0]["payload"]["status"] == "failed"
        assert events[0]["payload"]["command"]["exit_code"] == 7

        events = adapter.normalize({
            "method": "turn/completed",
            "params": {
                "turn": {"id": "turn-1", "status": "completed", "durationMs": 20, "error": None},
            },
        })
        assert events[0]["kind"] == "turn.finished"

    def test_mapeia_approval_request_e_nao_duplica_resolved(self):
        adapter = _adapter()
        adapter.session_id = "thread-1"
        adapter.turn_id = "turn-1"

        events = adapter.normalize({
            "method": "item/commandExecution/requestApproval",
            "id": 0,
            "params": {
                "turnId": "turn-1",
                "itemId": "cmd-1",
                "reason": "permitir?",
                "command": "touch /tmp/x",
                "cwd": "/repo",
                "availableDecisions": ["accept", "cancel"],
            },
        })
        assert events[0]["kind"] == "approval.requested"
        assert events[0]["payload"]["approval"]["request_id"] == 0
        assert events[0]["payload"]["approval"]["available_decisions"] == ["accept", "cancel"]

        adapter.resolved_approvals.add(0)
        events = adapter.normalize({
            "method": "serverRequest/resolved",
            "params": {"threadId": "thread-1", "requestId": 0},
        })
        assert events == []

