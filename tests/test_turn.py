"""Testes do dispatcher de eventos do turno — lib/runner/turn.py (Fase 2 do PLANO_MELHORIAS.md).

Instancia `Turn` diretamente (sem subir subprocess/loop de `run_turn`) e alimenta eventos
sintéticos via `handle_event`, mockando as saídas (tools, thinking log, permissões).
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from lib.runner.turn import Turn, _background_job_from_result, _background_job_notification


@pytest.fixture()
def fake_client():
    client = MagicMock()
    client.cwd = "/tmp"
    client.tmux_srv = "srv"
    client.nvim_pane = ""
    client.editor_bin = ""
    client._streaming_text = False
    client._last_response_text = ""
    client._retry_context = None
    client.session_id = None
    client.observed_model = None
    return client


def _turn(fake_client, cfg=None, thinking_autoresize=True, render=None):
    proc = MagicMock()
    cfg = cfg if cfg is not None else {"typewriter": False, "gargoyles": False}
    render = render if render is not None else MagicMock()
    return Turn(fake_client, proc, cfg, thinking_autoresize, max_think_lines=20, idle_lines=8, render=render)


def _se(event):
    return {"type": "stream_event", "event": event}


class TestFrolloEvents:
    def test_cria_evento_de_inicio_do_turno(self, fake_client):
        turn = _turn(fake_client)

        event = turn.frollo_events[0]

        assert event["schema"] == "frollo.event.v0"
        assert event["kind"] == "turn.started"
        assert event["provider"]["name"] == "claude"
        assert event["provider"]["surface"] == "stream-json"
        assert event["turn_id"].startswith("claude-turn-")
        assert event["payload"] == {"status": "in_progress"}

    def test_emite_texto_assistente_em_paralelo_ao_renderer(self, fake_client):
        turn = _turn(fake_client)

        turn.handle_event(_se({"type": "content_block_start", "index": 1, "content_block": {"type": "text"}}))
        turn.handle_event(_se({"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "olá"}}))
        turn.handle_event(_se({"type": "content_block_stop", "index": 1}))

        kinds = [event["kind"] for event in turn.frollo_events]
        assert "message.assistant.delta" in kinds
        assert "message.assistant.completed" in kinds
        completed = turn.frollo_events[-1]
        assert completed["item_id"] == "claude:block:1"
        assert completed["payload"]["text"] == "olá"

    def test_emite_bash_com_exit_code_como_command_failed(self, fake_client):
        turn = _turn(fake_client)

        with patch("lib.runner.turn.log_tool_call"), patch("lib.runner.turn.log_tool_result"):
            turn.handle_event({
                "type": "assistant",
                "message": {"content": [{
                    "type": "tool_use",
                    "id": "tu1",
                    "name": "Bash",
                    "input": {"command": "exit 7"},
                }]},
            })
            turn.handle_event({
                "type": "user",
                "message": {"content": [{
                    "type": "tool_result",
                    "tool_use_id": "tu1",
                    "is_error": False,
                    "content": [{"type": "text", "text": "Command failed with exit code: 7"}],
                }]},
            })

        command_events = [event for event in turn.frollo_events if event["item_id"] == "tu1"]
        assert [event["kind"] for event in command_events] == ["command.started", "command.failed"]
        assert command_events[-1]["payload"]["command"]["command"] == "exit 7"
        assert command_events[-1]["payload"]["command"]["exit_code"] == 7

    def test_result_fecha_turno_com_evento_v0(self, fake_client):
        turn = _turn(fake_client)

        turn.handle_event({
            "type": "result",
            "session_id": "sess-123",
            "total_cost_usd": 0.042,
            "usage": {"input_tokens": 100, "output_tokens": 50},
        })

        assert turn.frollo_events[-2]["kind"] == "usage.updated"
        assert turn.frollo_events[-2]["payload"]["usage"]["total_cost_usd"] == 0.042
        assert turn.frollo_events[-1]["kind"] == "turn.finished"
        assert turn.frollo_events[-1]["session_id"] == "sess-123"
        assert turn.frollo_events[-1]["payload"]["status"] == "completed"

    def test_mapeia_compactacao_do_claude_no_protocolo(self, fake_client, capsys):
        turn = _turn(fake_client)

        turn.handle_event({"type": "system", "subtype": "status", "status": "compacting"})
        turn.handle_event({
            "type": "system",
            "subtype": "compact_boundary",
            "compact_metadata": {"trigger": "manual", "preTokens": 42000, "postTokens": 12000},
        })
        turn.handle_event({
            "type": "result", "subtype": "success", "is_error": False,
            "local_command": "compact", "session_id": "sess-123",
        })

        kinds = [event["kind"] for event in turn.frollo_events]
        assert "context.compaction.started" in kinds
        assert "context.compaction.finished" in kinds
        assert "context.compaction.failed" not in kinds
        assert turn.compaction_metadata["preTokens"] == 42000
        assert "contexto compactado" in capsys.readouterr().out

    def test_falha_de_compactacao_do_claude_e_visivel(self, fake_client):
        turn = _turn(fake_client)

        turn.handle_event({"type": "system", "subtype": "status", "status": "compacting"})
        turn.handle_event({
            "type": "system", "subtype": "status", "status": None,
            "compact_result": "failed", "compact_error": "Not enough messages to compact.",
        })
        with patch("lib.runner.turn.errors.report") as report:
            turn.handle_event({
                "type": "result", "subtype": "success", "is_error": False,
                "local_command": "compact", "result": "Not enough messages to compact.",
            })

        assert "context.compaction.failed" in [event["kind"] for event in turn.frollo_events]
        assert report.call_args.args[1] == "Not enough messages to compact."
        assert report.call_args.kwargs["code"] == "compact_failed"

    def test_compact_sem_confirmacao_reporta_falha_de_protocolo(self, fake_client):
        turn = _turn(fake_client)

        with patch("lib.runner.turn.errors.report") as report:
            turn.handle_event({
                "type": "result", "subtype": "success", "is_error": False,
                "local_command": "compact", "result": "",
            })

        assert report.call_args.kwargs["code"] == "compact_missing_completion"


class TestMessageStart:
    def test_atualiza_tokens_e_modelo(self, fake_client):
        turn = _turn(fake_client)
        turn.rate_limited = True
        turn.handle_event(_se({
            "type": "message_start",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {"input_tokens": 42, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 1},
            },
        }))
        assert turn.input_tokens == 42
        assert turn.cache_read_tokens == 3
        assert turn.cache_creation_tokens == 1
        assert turn.model_name == "claude-sonnet-4-6"
        assert fake_client.observed_model == "claude-sonnet-4-6"
        # message_start sempre reseta rate_limited — turno começou, o limite anterior já era stale
        assert turn.rate_limited is False


class TestThinkingDelta:
    def test_thinking_delta_escreve_header_e_anima(self, fake_client):
        turn = _turn(fake_client, thinking_autoresize=True)
        turn.handle_event(_se({"type": "content_block_start", "content_block": {"type": "thinking"}}))
        with patch("lib.runner.turn._log") as mock_log, \
             patch("lib.runner.turn._resize_thinking") as mock_resize:
            turn.handle_event(_se({
                "type": "content_block_delta",
                "delta": {"type": "thinking_delta", "thinking": "9 sheep survive."},
            }))
        assert turn.thinking_header_written is True
        turn.render.push_file.assert_called_once()
        assert turn.render.push_file.call_args.args[1] == "9 sheep survive."
        mock_resize.assert_called_once_with("srv", 20)
        assert turn.thinking_lines == 20

    def test_thinking_vazio_nao_escreve_header(self, fake_client):
        """Bloco de thinking só com signature_delta fica sem texto e sem header."""
        turn = _turn(fake_client)
        turn.handle_event(_se({"type": "content_block_start", "content_block": {"type": "thinking"}}))
        with patch("lib.runner.turn._log") as mock_log, \
             patch("lib.runner.turn._resize_thinking") as mock_resize:
            turn.handle_event(_se({
                "type": "content_block_delta",
                "delta": {"type": "signature_delta", "signature": "abc"},
            }))
            turn.handle_event(_se({"type": "content_block_stop"}))
        assert turn.thinking_header_written is False
        mock_log.assert_not_called()
        mock_resize.assert_not_called()

    def test_thinking_vazio_escreve_nota_so_no_result(self, fake_client):
        turn = _turn(fake_client)
        turn.handle_event(_se({"type": "content_block_start", "content_block": {"type": "thinking"}}))
        with patch("lib.runner.turn._log") as mock_log:
            turn.handle_event(_se({
                "type": "content_block_delta",
                "delta": {"type": "signature_delta", "signature": "abc"},
            }))
            turn.handle_event(_se({"type": "content_block_stop"}))
            mock_log.assert_not_called()
            turn.handle_event(_se({
                "type": "message_delta",
                "usage": {"output_tokens_details": {"thinking_tokens": 12}},
            }))
            turn.handle_event({"type": "result", "session_id": "sess-123"})
        assert any("omitiu o thinking" in call.args[1] for call in mock_log.call_args_list)

    def test_thinking_vazio_sem_tokens_tem_nota_diferente(self, fake_client):
        turn = _turn(fake_client)
        turn.handle_event(_se({"type": "content_block_start", "content_block": {"type": "thinking"}}))
        with patch("lib.runner.turn._log") as mock_log:
            turn.handle_event(_se({"type": "content_block_stop"}))
            turn.handle_event(_se({
                "type": "message_delta",
                "usage": {"output_tokens_details": {"thinking_tokens": 0}},
            }))
            turn.handle_event({"type": "result", "session_id": "sess-123"})
        assert any("thinking_tokens=0" in call.args[1] for call in mock_log.call_args_list)


class TestStdoutJsonFragments:
    def test_input_json_delta_com_newline_literal_e_recomposto(self, fake_client):
        turn = _turn(fake_client)
        raw = (
            '{"type":"stream_event","event":{"type":"content_block_delta","index":0,'
            '"delta":{"type":"input_json_delta","partial_json":"\\\\\\", \\\\\\"description\\\\\\"'
            '\n'
            ': \\\\\\"linha 1\\\\nlinha 2\\\\\\""}}}'
        )

        with patch("lib.runner.turn.errors.report") as report:
            for line in raw.splitlines(keepends=True):
                turn.handle_line(line)

        report.assert_not_called()
        assert turn._stdout_json_buf == ""

    def test_flush_pending_json_reporta_fragmento_incompleto(self, fake_client):
        turn = _turn(fake_client)

        with patch("lib.runner.turn.errors.report") as report:
            turn.handle_line(
                '{"type":"stream_event","event":{"type":"content_block_delta","index":0,'
                '"delta":{"type":"input_json_delta","partial_json":"abc'
            )
            report.assert_not_called()

            assert turn.flush_pending_json() is True

        assert report.call_args.kwargs["code"] == "non_json_line"
        assert "JSON incompleto" in report.call_args.args[1]


class TestTextDelta:
    def test_acumula_last_response_text(self, fake_client):
        turn = _turn(fake_client)
        turn.current_block = "text"
        turn.handle_event(_se({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "olá "}}))
        turn.handle_event(_se({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "mundo"}}))
        assert fake_client._last_response_text == "olá mundo"

    def test_suprimido_apos_pedido_de_permissao(self, fake_client):
        turn = _turn(fake_client)
        turn.current_block = "text"
        turn._suppress_perm_text = True
        turn.handle_event(_se({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ignorado"}}))
        turn.render.push_stdout.assert_not_called()
        # ainda assim acumula o texto bruto do turno
        assert fake_client._last_response_text == "ignorado"


class TestToolUse:
    def test_evento_user_textual_do_compact_nao_e_interpretado_como_bloco(self, fake_client):
        turn = _turn(fake_client)

        with patch("lib.runner.turn.log_tool_result") as mock_log_result:
            turn.handle_event({
                "type": "user",
                "message": {"role": "user", "content": "/compact"},
            })

        mock_log_result.assert_not_called()

    def test_registra_tool_use_e_chama_log_tool_call(self, fake_client):
        turn = _turn(fake_client)
        with patch("lib.runner.turn.log_tool_call") as mock_log_call:
            turn.handle_event({
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}}]},
            })
        mock_log_call.assert_called_once()
        assert turn._tool_names["tu1"] == "Bash"


class TestBackgroundJobs:
    def test_ignora_tool_use_result_textual_de_bash_foreground(self):
        event = {
            "toolUseResult": (
                "Error: Exit code 1\n"
                "expected an object but got: number (1)"
            ),
        }

        job = _background_job_from_result(
            event,
            {"content": "Error: Exit code 1"},
            {"command": "comando que falhou"},
        )

        assert job is None

    def test_extrai_id_e_output_do_resultado_textual(self):
        block = {
            "content": (
                "Command running in background with ID: bbjk1yww9. "
                "Output is being written to: /tmp/session/tasks/bbjk1yww9.output. "
                "You will be notified when it completes."
            ),
        }

        assert _background_job_from_result(
            {}, block, {"command": "pytest", "description": "Run tests"},
        ) == {
            "id": "bbjk1yww9",
            "description": "Run tests",
            "output_path": "/tmp/session/tasks/bbjk1yww9.output",
            "session_id": None,
            "status": "running",
        }

    def test_prefere_id_estruturado_e_aceita_output_ausente(self):
        event = {"toolUseResult": {"backgroundTaskId": "bg-42"}}

        job = _background_job_from_result(event, {"content": "started"}, {"command": "make"})

        assert job["id"] == "bg-42"
        assert job["output_path"] is None

    def test_registra_emite_e_exibe_job_uma_vez(self, fake_client):
        turn = _turn(fake_client)
        turn._tool_names["tu1"] = "Bash"
        turn._tool_inputs["tu1"] = {"command": "pytest", "description": "Run tests"}
        event = {
            "type": "user",
            "message": {"content": [{
                "type": "tool_result",
                "tool_use_id": "tu1",
                "content": "Command running in background with ID: bg123. Output is being written to: /tmp/bg123.output.",
            }]},
        }

        with patch("lib.runner.turn.log_tool_result"), \
             patch("lib.runner.turn.log_background_job") as log_job:
            turn.handle_event(event)
            turn.handle_event(event)

        assert fake_client._background_jobs["bg123"]["status"] == "running"
        assert fake_client._background_jobs["bg123"]["description"] == "Run tests"
        log_job.assert_called_once()
        assert [e["kind"] for e in turn.frollo_events].count("job.started") == 1

    def test_result_preenche_session_id_dos_jobs(self, fake_client):
        turn = _turn(fake_client)
        fake_client._background_jobs = {
            "bg123": {"id": "bg123", "session_id": None, "status": "running"},
        }

        turn.handle_event({"type": "result", "session_id": "sess-123"})

        assert fake_client._background_jobs["bg123"]["session_id"] == "sess-123"

    def test_extrai_notificacao_de_conclusao(self):
        event = {
            "type": "user",
            "session_id": "sess-123",
            "message": {"content": (
                "<task-notification>\n"
                "<task-id>bg123</task-id>\n"
                "<output-file>/tmp/bg123.output</output-file>\n"
                "<status>completed</status>\n"
                "<summary>testes passaram</summary>\n"
                "</task-notification>"
            )},
        }

        assert _background_job_notification(event) == {
            "id": "bg123",
            "status": "completed",
            "summary": "testes passaram",
            "output_path": "/tmp/bg123.output",
            "session_id": "sess-123",
        }

    def test_normaliza_notificacao_de_job_cancelado(self):
        event = {
            "type": "user",
            "message": {"content": (
                "<task-notification><task-id>bg123</task-id>"
                "<status>killed</status></task-notification>"
            )},
        }

        assert _background_job_notification(event)["status"] == "cancelled"

    def test_notificacao_atualiza_job_e_emite_conclusao(self, fake_client):
        turn = _turn(fake_client)
        fake_client._background_jobs = {
            "bg123": {"id": "bg123", "description": "Run tests", "status": "running"},
        }
        event = {
            "type": "user",
            "message": {"content": (
                "<task-notification><task-id>bg123</task-id>"
                "<status>completed</status><summary>exit 0</summary></task-notification>"
            )},
        }

        with patch("lib.runner.turn.log_background_job_status") as log_status:
            turn.handle_event(event)
            turn.handle_event(event)

        assert fake_client._background_jobs["bg123"]["status"] == "completed"
        assert [e["kind"] for e in turn.frollo_events].count("job.completed") == 1
        log_status.assert_called_once()


class TestToolResultPermissao:
    def test_erro_de_permissao_aciona_ask_e_marca_aprovado(self, fake_client):
        turn = _turn(fake_client)
        turn._tool_names["tu1"] = "Bash"
        with patch("lib.runner.turn.log_tool_result") as mock_log_result, \
             patch("lib.runner.turn._handle_permission_ask", return_value=True) as mock_ask:
            turn.handle_event({
                "type": "user",
                "message": {"content": [{
                    "type": "tool_result",
                    "tool_use_id": "tu1",
                    "is_error": True,
                    "content": [{"type": "text", "text": "requested permissions to run Bash"}],
                }]},
            })
        mock_ask.assert_called_once_with("Bash", "/tmp")
        mock_log_result.assert_called_once()
        assert turn.perm_approved is True
        assert fake_client._retry_context == "(Bash aprovado — prossiga)"
        assert turn._suppress_perm_text is True

    def test_negado_nao_marca_aprovado(self, fake_client):
        turn = _turn(fake_client)
        turn._tool_names["tu1"] = "Bash"
        with patch("lib.runner.turn.log_tool_result"), \
             patch("lib.runner.turn._handle_permission_ask", return_value=False):
            turn.handle_event({
                "type": "user",
                "message": {"content": [{
                    "type": "tool_result",
                    "tool_use_id": "tu1",
                    "is_error": True,
                    "content": [{"type": "text", "text": "requested permissions to run Bash"}],
                }]},
            })
        assert turn.perm_approved is False
        assert fake_client._retry_context is None


class TestControlRequest:
    def test_can_use_tool_vai_pro_prompt_de_permissao(self, fake_client):
        turn = _turn(fake_client)
        event = {"type": "control_request", "request_id": "r1",
                 "request": {"subtype": "can_use_tool", "tool_name": "Write", "input": {}}}
        with patch("lib.runner.turn._handle_control_request", return_value=True) as handler:
            turn.handle_event(event)
        handler.assert_called_once_with(event, turn.proc, "/tmp")
        turn.render.suspend.assert_called_once()
        turn.render.resume.assert_called_once()

    def test_pergunta_e_respondida_no_mesmo_turno_sem_retry(self, fake_client):
        turn = _turn(fake_client)
        response = {"behavior": "allow", "updatedInput": {"answers": {"Qual?": "A"}}}
        with patch("lib.runner.turn._answer_question_request",
                   return_value=({"q": ["A"]}, response)), \
             patch("lib.runner.turn._send_control_response") as send:
            turn.handle_event({"type": "control_request", "request_id": "r1", "request": {
                "subtype": "can_use_tool", "tool_name": "AskUserQuestion",
                "tool_use_id": "tu1", "input": {"question": "Qual?", "options": ["A", "B"]},
            }})

        send.assert_called_once_with(turn.proc, "r1", response)
        assert turn.retry_requested is False
        kinds = [e["kind"] for e in turn.frollo_events if e["kind"].startswith("question.")]
        assert kinds == ["question.requested", "question.answered"]

    def test_subtype_desconhecido_responde_erro_e_reporta(self, fake_client):
        # Sem resposta o CLI bloqueia esperando — o turno virava spinner infinito.
        turn = _turn(fake_client)
        with patch("lib.runner.turn._send_control_response") as send, \
             patch("lib.runner.turn.errors.report") as report:
            turn.handle_event({"type": "control_request", "request_id": "r9",
                               "request": {"subtype": "hook_callback"}})
        assert send.call_args.args[:2] == (turn.proc, "r9")
        assert "error" in send.call_args.kwargs
        assert report.call_args.kwargs["code"] == "unhandled_request"


class TestToolResultPerguntaUsuario:
    def test_tool_de_pergunta_falha_e_vira_retry_com_resposta(self, fake_client):
        turn = _turn(fake_client)
        turn._tool_names["tu1"] = "AskUserQuestion"
        turn._tool_inputs["tu1"] = {"question": "Qual caminho prefere?"}
        with patch("lib.runner.turn.log_tool_result") as mock_log_result, \
             patch("lib.runner.turn._handle_user_question", return_value="o caminho curto") as mock_question:
            turn.handle_event({
                "type": "user",
                "message": {"content": [{
                    "type": "tool_result",
                    "tool_use_id": "tu1",
                    "is_error": True,
                    "content": [{
                        "type": "text",
                        "text": "AskUserQuestion is not available in this environment",
                    }],
                }]},
            })

        mock_question.assert_called_once()
        mock_log_result.assert_called_once()
        assert turn.retry_requested is True
        assert "o caminho curto" in fake_client._retry_context
        assert turn._suppress_perm_text is True
        question_events = [event for event in turn.frollo_events if event["kind"].startswith("question.")]
        assert [event["kind"] for event in question_events] == [
            "question.requested", "question.answered",
        ]
        assert question_events[0]["payload"]["questions"][0]["question"] == "Qual caminho prefere?"
        assert question_events[1]["payload"]["answer"] == "o caminho curto"

    def test_cancelamento_emite_evento_sem_pedir_retry(self, fake_client):
        turn = _turn(fake_client)
        turn._tool_names["tu1"] = "AskUserQuestion"
        turn._tool_inputs["tu1"] = {"question": "Continuar?", "options": ["Sim", "Não"]}
        with patch("lib.runner.turn.log_tool_result"), \
             patch("lib.runner.turn._handle_user_question", return_value=None):
            turn.handle_event({
                "type": "user",
                "message": {"content": [{
                    "type": "tool_result",
                    "tool_use_id": "tu1",
                    "is_error": True,
                    "content": [{"type": "text", "text": "not available in this environment"}],
                }]},
            })

        assert turn.retry_requested is False
        assert fake_client._retry_context is None
        assert [event["kind"] for event in turn.frollo_events if event["kind"].startswith("question.")] == [
            "question.requested", "question.cancelled",
        ]

    def test_task_prompt_com_erro_nao_e_confundido_com_pergunta(self, fake_client):
        turn = _turn(fake_client)
        turn._tool_names["tu1"] = "Task"
        turn._tool_inputs["tu1"] = {"prompt": "investigue"}
        with patch("lib.runner.turn.log_tool_result"), \
             patch("lib.runner.turn._handle_user_question") as mock_question:
            turn.handle_event({
                "type": "user",
                "message": {"content": [{
                    "type": "tool_result",
                    "tool_use_id": "tu1",
                    "is_error": True,
                    "content": [{"type": "text", "text": "subagent failed"}],
                }]},
            })

        mock_question.assert_not_called()
        assert turn.retry_requested is False


class TestResult:
    def test_captura_session_id_custo_e_tokens(self, fake_client):
        turn = _turn(fake_client)
        turn.handle_event({
            "type": "result",
            "session_id": "sess-123",
            "total_cost_usd": 0.042,
            "usage": {"input_tokens": 100, "output_tokens": 50},
        })
        assert fake_client.session_id == "sess-123"
        assert turn.result_cost == 0.042
        assert turn.result_in_tok == 100
        assert turn.result_out_tok == 50

    def test_marca_turn_done(self, fake_client):
        # 'result' delimita o fim do turno (Fase 4) -- em processo persistente o
        # processo não fecha stdout sozinho, run_turn depende desse flag pra parar
        # de ler em vez de esperar EOF.
        turn = _turn(fake_client)
        assert turn.turn_done is False
        turn.handle_event({"type": "result", "session_id": "sess-123"})
        assert turn.turn_done is True


class TestRateLimitEvent:
    def test_marca_rate_limited_e_calcula_retry(self, fake_client, tmp_path):
        turn = _turn(fake_client)
        with patch("lib.runner.turn.RUNDIR", tmp_path):
            turn.handle_event({"type": "rate_limit_event", "retryAfter": 30})
        assert turn.rate_limited is True
        assert turn.rate_limit_retry == 30
        assert turn.rate_limit_reset_str  # calculado a partir do retryAfter
        logged = json.loads((tmp_path / "rate-limit.log").read_text().strip())
        assert logged["retryAfter"] == 30


class TestClearStatus:
    """_clear_status é chamado antes de cada chunk de stdout (render._dispatch).
    Sem spinner visível ele deve ser no-op — senão apagaria a linha de resposta
    já digitada pelo chunk anterior (regressão: 'texto some enquanto digita')."""

    def test_noop_sem_spinner(self, fake_client, capsys):
        turn = _turn(fake_client)
        turn.spinner_shown = False
        turn._clear_status()
        assert capsys.readouterr().out == ""  # nada escrito, nada apagado

    def test_apaga_spinner_visivel(self, fake_client, capsys):
        turn = _turn(fake_client)
        turn.spinner_shown = True
        turn._clear_status()
        out = capsys.readouterr().out
        assert "\033[2K" in out and "\033[1A" in out
        assert turn.spinner_shown is False
