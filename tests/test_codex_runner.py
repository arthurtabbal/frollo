import base64
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

import lib.runner.codex as codex_mod
from lib.runner.codex import _CodexAdapter, _CodexRenderer, _codex_turn_start_params
from lib.theme import CHAT_FG, RESET


def _adapter():
    client = MagicMock()
    client.session_id = None
    client.observed_model = ""
    return _CodexAdapter(client)


class TestCodexAdapter:
    def test_turn_start_params_forcam_summary_detailed(self):
        params = _codex_turn_start_params("thread-1", "oi")

        assert params["threadId"] == "thread-1"
        assert params["summary"] == "detailed"
        assert params["input"] == [{"type": "text", "text": "oi"}]

    def test_turn_start_params_converte_imagem_base64_para_local_image(self, tmp_path, monkeypatch):
        monkeypatch.setattr(codex_mod, "RUNDIR", tmp_path)

        params = _codex_turn_start_params("thread-1", "olha [img]", [{
            "data": base64.b64encode(b"fake-jpeg").decode(),
            "media_type": "image/jpeg",
        }])

        image_item, text_item = params["input"]
        assert image_item["type"] == "localImage"
        assert image_item["path"].endswith(".jpg")
        assert Path(image_item["path"]).read_bytes() == b"fake-jpeg"
        assert text_item == {"type": "text", "text": "olha"}

    def test_turn_start_params_aceita_imagem_por_path(self, tmp_path):
        image_path = tmp_path / "frame.png"
        image_path.write_bytes(b"png")

        params = _codex_turn_start_params("thread-1", "", [{"path": image_path}])

        assert params["input"] == [{"type": "localImage", "path": str(image_path.resolve())}]

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

    def test_mapeia_warning_sandbox_codex_com_codigo_suprimivel(self):
        adapter = _adapter()

        events = adapter.normalize({
            "method": "configWarning",
            "params": {
                "summary": "Codex's Linux sandbox uses bubblewrap and needs access to create user namespaces.",
                "details": None,
            },
        })

        assert events[0]["kind"] == "notice"
        assert events[0]["payload"]["notice"]["code"] == "codex_linux_sandbox_userns"

    def test_mapeia_reasoning_delta_e_completion(self):
        adapter = _adapter()
        adapter.session_id = "thread-1"
        adapter.turn_id = "turn-1"

        events = adapter.normalize({
            "method": "item/reasoning/summaryTextDelta",
            "params": {"turnId": "turn-1", "itemId": "rs-1", "summaryIndex": 0, "delta": "checando"},
        })
        assert events[0]["kind"] == "reasoning.delta"
        assert events[0]["payload"]["visibility"] == "summary"
        assert events[0]["payload"]["delta"] == "checando"

        events = adapter.normalize({
            "method": "item/completed",
            "params": {
                "turnId": "turn-1",
                "item": {
                    "type": "reasoning",
                    "id": "rs-1",
                    "summary": [{"type": "summary_text", "text": "resumo"}],
                    "content": [{"type": "reasoning_text", "text": "interno"}],
                },
            },
        })
        assert events[0]["kind"] == "reasoning.completed"
        assert events[0]["payload"]["summary_text"] == "resumo"

    def test_mapeia_partes_de_summary_sem_grudar_texto(self):
        adapter = _adapter()
        adapter.session_id = "thread-1"
        adapter.turn_id = "turn-1"

        events = adapter.normalize({
            "method": "item/completed",
            "params": {
                "turnId": "turn-1",
                "item": {
                    "type": "reasoning",
                    "id": "rs-1",
                    "summary": [
                        {"type": "summary_text", "text": "**Compondo resposta**"},
                        {"type": "summary_text", "text": "**Resumo final**\n\nFechando a análise."},
                    ],
                    "content": [],
                },
            },
        })

        payload = events[0]["payload"]
        assert payload["summary_parts"] == [
            "**Compondo resposta**",
            "**Resumo final**\n\nFechando a análise.",
        ]
        assert payload["summary_text"] == "**Compondo resposta**\n**Resumo final**\n\nFechando a análise."


class TestCodexRenderer:
    def _renderer(self):
        client = MagicMock()
        client.tmux_srv = ""
        client._streaming_text = False
        client._last_response_text = ""
        client.observed_model = ""
        render = MagicMock()
        renderer = _CodexRenderer(client, {"typewriter": False, "thinking_autoresize": False}, render, 0)
        renderer.render = render
        return renderer, render

    def test_suprime_warning_sandbox_conhecido_no_tools(self):
        renderer, _ = self._renderer()

        with patch("lib.runner.codex._log") as mock_log:
            renderer.handle({
                "kind": "notice",
                "payload": {"notice": {
                    "message": "Codex's Linux sandbox uses bubblewrap and needs access to create user namespaces.",
                    "code": "codex_linux_sandbox_userns",
                }},
                "provider": {},
            })

        mock_log.assert_not_called()

    def test_reasoning_vazio_escreve_fallback_no_thinking(self):
        renderer, render = self._renderer()

        with patch("lib.runner.codex._log") as mock_log:
            renderer.handle({
                "kind": "reasoning.started",
                "payload": {"summary_text": "", "content_text": ""},
                "provider": {},
            })
            renderer.handle({
                "kind": "reasoning.completed",
                "payload": {"summary_text": "", "content_text": ""},
                "provider": {},
            })

        logged = "".join(call.args[1] for call in mock_log.call_args_list)
        assert "sem resumo textual" in logged
        render.join.assert_called()

    def test_reasoning_com_delta_nao_escreve_fallback(self):
        renderer, render = self._renderer()

        with patch("lib.runner.codex._log") as mock_log:
            renderer.handle({
                "kind": "reasoning.started",
                "item_id": "rs-1",
                "payload": {"summary_text": "", "content_text": ""},
                "provider": {},
            })
            renderer.handle({
                "kind": "reasoning.delta",
                "item_id": "rs-1",
                "payload": {"delta": "checando"},
                "provider": {},
            })
            renderer.handle({
                "kind": "reasoning.completed",
                "item_id": "rs-1",
                "payload": {"summary_text": "", "content_text": ""},
                "provider": {},
            })

        logged = "".join(call.args[1] for call in mock_log.call_args_list)
        assert "sem resumo textual" not in logged
        render.push_file.assert_called_once()
        render.join.assert_called()

    def test_summary_part_added_nao_cria_checkpoint_anonimo(self):
        renderer, render = self._renderer()

        with patch("lib.runner.codex._log") as mock_log:
            renderer.handle({
                "kind": "reasoning.summary.started",
                "item_id": "rs-1",
                "payload": {"summary_index": 0},
                "provider": {},
            })
            renderer.handle({
                "kind": "reasoning.delta",
                "item_id": "rs-1",
                "payload": {"delta": "checando"},
                "provider": {},
            })

        headers = [call.args[1] for call in mock_log.call_args_list if "\033[40m" in call.args[1]]
        assert len(headers) == 1
        render.push_file.assert_called_once()

    def test_reasoning_completed_com_partes_renderiza_checkpoints_separados(self):
        renderer, render = self._renderer()

        with patch("lib.runner.codex._log") as mock_log:
            renderer.handle({
                "kind": "reasoning.completed",
                "item_id": "rs-1",
                "payload": {
                    "summary_parts": ["**Compondo resposta**", "**Resumo final**"],
                    "summary_text": "**Compondo resposta**\n**Resumo final**",
                },
                "provider": {},
            })

        pushed = [call.args[1] for call in render.push_file.call_args_list]
        headers = [call.args[1] for call in mock_log.call_args_list if "\033[40m" in call.args[1]]
        assert pushed == ["**Compondo resposta**", "**Resumo final**"]
        assert len(headers) == 2

    def test_assistant_delta_de_novo_item_insere_quebra(self):
        renderer, render = self._renderer()

        renderer.handle({
            "kind": "message.assistant.delta",
            "item_id": "msg-1",
            "payload": {"delta": "primeiro bloco."},
            "provider": {},
        })
        renderer.handle({
            "kind": "message.assistant.delta",
            "item_id": "msg-2",
            "payload": {"delta": "segundo bloco."},
            "provider": {},
        })

        pushed = [call.args[0] for call in render.push_stdout.call_args_list]
        assert pushed == [
            CHAT_FG + "primeiro bloco." + RESET,
            CHAT_FG + "\n\n" + RESET,
            CHAT_FG + "segundo bloco." + RESET,
        ]
        assert renderer.client._last_response_text == "primeiro bloco.\n\nsegundo bloco."

    def test_assistant_delta_do_mesmo_item_nao_insere_quebra(self):
        renderer, render = self._renderer()

        renderer.handle({
            "kind": "message.assistant.delta",
            "item_id": "msg-1",
            "payload": {"delta": "pri"},
            "provider": {},
        })
        renderer.handle({
            "kind": "message.assistant.delta",
            "item_id": "msg-1",
            "payload": {"delta": "meiro"},
            "provider": {},
        })

        pushed = [call.args[0] for call in render.push_stdout.call_args_list]
        assert pushed == [
            CHAT_FG + "pri" + RESET,
            CHAT_FG + "meiro" + RESET,
        ]
        assert renderer.client._last_response_text == "primeiro"

    def test_assistant_delta_usa_markdown_buffer_compartilhado(self):
        renderer, render = self._renderer()

        renderer.handle({
            "kind": "message.assistant.delta",
            "item_id": "msg-1",
            "payload": {"delta": "isso é **for"},
            "provider": {},
        })
        assert render.push_stdout.call_count == 0

        renderer.handle({
            "kind": "message.assistant.delta",
            "item_id": "msg-1",
            "payload": {"delta": "te**."},
            "provider": {},
        })

        pushed = "".join(call.args[0] for call in render.push_stdout.call_args_list)
        assert "\033[1mforte" in pushed
        assert renderer.client._last_response_text == "isso é **forte**."
