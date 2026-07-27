import base64
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

import lib.runner.codex as codex_mod
from lib.runner.codex import (
    _CODEX_DONE_DRAIN_GRACE,
    _CodexAdapter,
    _CodexRenderer,
    _codex_account_email_from_response,
    _codex_command_output_preview,
    _codex_context_used,
    _codex_live_progress_line,
    _codex_live_progress_lines,
    _codex_normalize_command_output,
    _codex_preferred_command_output,
    _codex_quota_for_stats,
    _codex_done_drain_finished,
    _codex_turn_start_params,
    _write_stats,
)
from lib.runner.protocol import SCHEMA
from lib.theme import CHAT_FG, RESET


def _adapter():
    client = MagicMock()
    client.session_id = None
    client.observed_model = ""
    return _CodexAdapter(client)


class TestCodexAdapter:
    def test_normaliza_output_fragmentado_do_pytest(self):
        text = (
            "tests/test_alpha.py\n\n.\n\n.\n\n[ 50%]\n\n"
            "tests/test_beta.py\n\n.\n\n[100%]\n\n"
            "================ 3 passed in 0.10s ================\n"
        )

        assert _codex_normalize_command_output(text) == (
            "tests/test_alpha.py  2 passed [ 50%]\n"
            "tests/test_beta.py  1 passed [100%]\n"
            "================ 3 passed in 0.10s ================"
        )

    def test_normaliza_carriage_return_mantendo_ultima_linha(self):
        assert _codex_normalize_command_output("baixando 10%\rbaixando 100%\nok\n") == "baixando 100%\nok"

    def test_detecta_linha_de_progresso_ao_vivo(self):
        assert _codex_live_progress_line("baixando 10%\rbaixando 100%") == "baixando 100%"
        assert _codex_live_progress_line("frollo-progress [####------]  40%\n") == (
            "frollo-progress [####------]  40%"
        )
        assert _codex_live_progress_line(".\n[ 50%]\n") is None

    def test_detecta_frames_de_progresso_no_mesmo_delta(self):
        text = (
            "\rfrollo-progress [----------]   0%"
            "\rfrollo-progress [#####-----]  50%"
            "\rfrollo-progress [##########] 100%"
        )

        assert _codex_live_progress_lines(text) == [
            "frollo-progress [----------]   0%",
            "frollo-progress [#####-----]  50%",
            "frollo-progress [##########] 100%",
        ]
        assert _codex_live_progress_line(text) == "frollo-progress [##########] 100%"

    def test_preview_de_output_longo_preserva_fim(self):
        text = "\n".join(f"linha {i}" for i in range(20))

        assert _codex_command_output_preview(text, limit=6) == (
            "linha 0\n"
            "linha 1\n"
            "linha 2\n"
            "↓ 15 linhas\n"
            "linha 18\n"
            "linha 19"
        )

    def test_output_final_preserva_buffer_quando_agregado_perde_prefixo(self):
        buffered = "frollo-progress 10%\nfrollo-progress 25%\nfrollo-progress 50%\n"
        output = "frollo-progress 25%\nfrollo-progress 50%\n"

        assert _codex_preferred_command_output(output, buffered) == buffered

    def test_turn_start_params_forcam_reasoning_alto_e_summary_detailed(self):
        params = _codex_turn_start_params("thread-1", "oi")

        assert params["threadId"] == "thread-1"
        assert params["effort"] == "high"
        assert params["summary"] == "detailed"
        assert params["input"] == [{"type": "text", "text": "oi"}]

    def test_turn_start_params_aceitam_effort_do_cliente(self):
        params = _codex_turn_start_params("thread-1", "oi", effort="max")

        assert params["effort"] == "max"

    def test_quota_codex_formata_reset_epoch_sem_printar_inteiro_cru(self):
        rendered = _codex_quota_for_stats({"used_percent": 2, "resets_at": 1785281318})
        reset = rendered["limits"][0]["reset"]

        assert rendered["limits"][0]["pct"] == 2
        assert reset
        assert reset != "1785281318"

    def test_contexto_codex_nao_soma_cache_duas_vezes(self):
        assert _codex_context_used(134800, 134500) == 134800
        assert _codex_context_used(0, 1200) == 1200

    def test_extrai_email_de_account_read(self):
        result = {"account": {"type": "chatgpt", "email": "arthur@example.com", "planType": "pro"}}
        assert _codex_account_email_from_response(result) == "arthur@example.com"
        assert _codex_account_email_from_response({"account": {"type": "apiKey"}}) is None

    def test_mapeia_rate_limits_read_para_quota_updated(self):
        adapter = _adapter()

        events = adapter.normalize({
            "id": 7,
            "result": {
                "rateLimits": {
                    "limitId": "codex",
                    "limitName": None,
                    "primary": {"usedPercent": 5, "resetsAt": 1785281318},
                    "rateLimitReachedType": None,
                },
                "rateLimitsByLimitId": {},
            },
        })

        assert events[0]["kind"] == "quota.updated"
        quota = events[0]["payload"]["quota"]
        assert quota["label"] == "codex"
        assert quota["used_percent"] == 5
        assert quota["resets_at"] == 1785281318

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
        assert events[0]["schema"] == SCHEMA
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
        assert events[0]["payload"]["notice"]["code"] == "linux_sandbox_userns"

    def test_ignora_notifications_benignas_de_estado(self):
        adapter = _adapter()

        for method in (
            "remoteControl/status/changed",
            "mcpServer/startupStatus/updated",
            "thread/settings/updated",
        ):
            assert adapter.normalize({"method": method, "params": {}}) == []

    def test_ciclo_de_mensagens_conhecidas_nao_vira_unknown_item(self):
        adapter = _adapter()
        adapter.session_id = "thread-1"
        adapter.turn_id = "turn-1"

        assert adapter.normalize({
            "method": "item/started",
            "params": {"turnId": "turn-1", "item": {"type": "agentMessage", "id": "msg-1"}},
        }) == []

        assert adapter.normalize({
            "method": "item/completed",
            "params": {"turnId": "turn-1", "item": {"type": "userMessage", "id": "user-1"}},
        }) == []

    def test_mapeia_web_search_sem_warning_de_item_desconhecido(self):
        adapter = _adapter()
        adapter.session_id = "thread-1"
        adapter.turn_id = "turn-1"

        assert adapter.normalize({
            "method": "item/started",
            "params": {
                "turnId": "turn-1",
                "item": {
                    "type": "webSearch",
                    "id": "ws-1",
                    "query": "",
                    "action": {"type": "other"},
                },
            },
        }) == []

        events = adapter.normalize({
            "method": "item/completed",
            "params": {
                "turnId": "turn-1",
                "item": {
                    "type": "webSearch",
                    "id": "ws-1",
                    "query": "site:example.com frollo",
                    "action": {
                        "type": "search",
                        "query": "site:example.com frollo",
                        "queries": ["site:example.com frollo", "frollo observer"],
                    },
                },
            },
        })

        assert events[0]["kind"] == "web.search.finished"
        assert events[0]["payload"]["web_search"]["query"] == "site:example.com frollo"
        assert events[0]["payload"]["web_search"]["queries"] == [
            "site:example.com frollo",
            "frollo observer",
        ]

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

    def test_turn_completed_drena_eventos_tardios_antes_de_devolver_prompt(self):
        last_event_at = 10.0

        assert not _codex_done_drain_finished(False, last_event_at, last_event_at + 999)
        assert not _codex_done_drain_finished(
            True,
            last_event_at,
            last_event_at + _CODEX_DONE_DRAIN_GRACE - 0.01,
        )
        assert _codex_done_drain_finished(
            True,
            last_event_at,
            last_event_at + _CODEX_DONE_DRAIN_GRACE + 0.01,
        )


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

    def test_quota_updated_repinta_linha_do_stats_pane(self, tmp_path, monkeypatch):
        monkeypatch.setattr(codex_mod, "RUNDIR", tmp_path)
        stats_tty = tmp_path / "stats-output"
        stats_tty.write_text("")
        (tmp_path / "stats_tty").write_text(str(stats_tty))
        renderer, _ = self._renderer()

        renderer.handle({
            "kind": "quota.updated",
            "payload": {"quota": {
                "label": "codex",
                "used_percent": 5,
                "resets_at": 1785281318,
            }},
            "provider": {},
        })

        out = stats_tty.read_text()
        assert "\033[4;1H" in out
        assert "codex" in out
        assert "5%" in out
        assert renderer.client._last_codex_usage["limits"][0]["pct"] == 5

    def test_write_stats_preserva_cota_codex_cacheada(self, tmp_path, monkeypatch):
        monkeypatch.setattr(codex_mod, "RUNDIR", tmp_path)
        stats_tty = tmp_path / "stats-output"
        stats_tty.write_text("")
        (tmp_path / "stats_tty").write_text(str(stats_tty))
        renderer, _ = self._renderer()
        renderer.quota = None
        renderer.client._total_input_tokens = 0
        renderer.client._total_output_tokens = 0
        renderer.client._total_elapsed = 0.0
        renderer.client._total_cost = 0.0
        renderer.client._last_codex_usage = {
            "limits": [{"label": "codex", "pct": 5, "severity": None, "reset": "Jul28"}]
        }

        _write_stats(renderer.client, renderer, elapsed=1.0)

        out = stats_tty.read_text()
        assert "codex" in out
        assert "5%" in out

    def test_write_stats_codex_compacta_custo_zero_e_nao_duplica_cache_no_ctx(self, tmp_path, monkeypatch):
        monkeypatch.setattr(codex_mod, "RUNDIR", tmp_path)
        stats_tty = tmp_path / "stats-output"
        stats_tty.write_text("")
        (tmp_path / "stats_tty").write_text(str(stats_tty))
        renderer, _ = self._renderer()
        renderer.input_tokens = 134800
        renderer.output_tokens = 595
        renderer.cache_read_tokens = 134500
        renderer.context_window = 258400
        renderer.client._total_input_tokens = 0
        renderer.client._total_output_tokens = 0
        renderer.client._total_elapsed = 0.0
        renderer.client._total_cost = 0.0

        _write_stats(renderer.client, renderer, elapsed=435.8)

        out = stats_tty.read_text()
        assert "$0.0000" not in out
        assert "134.8k/258.4k" in out
        assert "269.3k/258.4k" not in out
        assert renderer.client._last_codex_ctx == {"used": 134800, "max": 258400}

    def test_usage_updated_repinta_ctx_line_sem_cache_duplicado(self, tmp_path, monkeypatch):
        monkeypatch.setattr(codex_mod, "RUNDIR", tmp_path)
        stats_tty = tmp_path / "stats-output"
        stats_tty.write_text("")
        (tmp_path / "stats_tty").write_text(str(stats_tty))
        renderer, _ = self._renderer()

        renderer.handle({
            "kind": "usage.updated",
            "payload": {"usage": {
                "input_tokens": 134800,
                "cached_input_tokens": 134500,
                "context_window": 258400,
            }},
            "provider": {},
        })

        out = stats_tty.read_text()
        assert "\033[3;1H" in out
        assert "134.8k/258.4k" in out

    def test_suprime_warning_sandbox_conhecido_no_tools(self):
        renderer, _ = self._renderer()

        with patch("lib.runner.codex._log") as mock_log:
            renderer.handle({
                "kind": "notice",
                "payload": {"notice": {
                    "message": "Codex's Linux sandbox uses bubblewrap and needs access to create user namespaces.",
                    "code": "linux_sandbox_userns",
                }},
                "provider": {},
            })

        mock_log.assert_not_called()

    def test_command_output_delta_e_renderizado_uma_vez_no_fim(self):
        renderer, _ = self._renderer()

        with patch("lib.runner.codex.log_tool_call"), patch("lib.runner.codex.log_tool_result") as mock_result:
            renderer.handle({
                "kind": "command.started",
                "item_id": "cmd-1",
                "payload": {"command": {"command": "pytest"}},
                "provider": {},
            })
            renderer.handle({
                "kind": "command.output.delta",
                "item_id": "cmd-1",
                "payload": {"delta": "tests/test_alpha.py\n\n.\n\n"},
                "provider": {},
            })
            renderer.handle({
                "kind": "command.output.delta",
                "item_id": "cmd-1",
                "payload": {"delta": "[100%]\n"},
                "provider": {},
            })

            mock_result.assert_not_called()

            renderer.handle({
                "kind": "command.finished",
                "item_id": "cmd-1",
                "payload": {"command": {"output": None}},
                "provider": {},
            })

        mock_result.assert_called_once_with({"content": "tests/test_alpha.py  1 passed [100%]"})

    def test_command_output_delta_de_progresso_atualiza_linha_viva(self):
        renderer, _ = self._renderer()

        with patch("lib.runner.codex.log_tool_result") as mock_result, \
                patch("lib.runner.codex._log") as mock_log:
            renderer.handle({
                "kind": "command.output.delta",
                "item_id": "cmd-1",
                "payload": {"delta": "frollo-progress [####------]  40%\n"},
                "provider": {},
            })

            mock_result.assert_not_called()
            assert mock_log.call_count == 1
            assert "frollo-progress [####------]  40%" in mock_log.call_args.args[1]

            renderer.handle({
                "kind": "command.finished",
                "item_id": "cmd-1",
                "payload": {"command": {"output": None}},
                "provider": {},
            })

        assert mock_log.call_count == 2
        assert mock_log.call_args_list[1].args[1] == "\r\033[2K"
        mock_result.assert_not_called()

    def test_command_finished_com_progresso_nao_registra_residuo_final(self):
        renderer, _ = self._renderer()

        with patch("lib.runner.codex.log_tool_result") as mock_result, \
                patch("lib.runner.codex._log"):
            renderer.handle({
                "kind": "command.output.delta",
                "item_id": "cmd-1",
                "payload": {"delta": "frollo-progress [####------]  40%\n"},
                "provider": {},
            })
            renderer.handle({
                "kind": "command.finished",
                "item_id": "cmd-1",
                "payload": {"command": {"output": "frollo-progress [##########] 100%\nfeito\n"}},
                "provider": {},
            })

        mock_result.assert_not_called()

    def test_command_output_delta_reproduz_frames_de_progresso_agrupados(self):
        renderer, _ = self._renderer()

        with patch("lib.runner.codex.log_tool_result") as mock_result, \
                patch("lib.runner.codex.time.sleep") as mock_sleep, \
                patch("lib.runner.codex._log") as mock_log:
            renderer.handle({
                "kind": "command.output.delta",
                "item_id": "cmd-1",
                "payload": {
                    "delta": (
                        "\rfrollo-progress [----------]   0%"
                        "\rfrollo-progress [#####-----]  50%"
                        "\rfrollo-progress [##########] 100%"
                    )
                },
                "provider": {},
            })

            mock_result.assert_not_called()
            assert mock_log.call_count == 3
            assert "frollo-progress [----------]   0%" in mock_log.call_args_list[0].args[1]
            assert "frollo-progress [#####-----]  50%" in mock_log.call_args_list[1].args[1]
            assert "frollo-progress [##########] 100%" in mock_log.call_args_list[2].args[1]
            assert mock_sleep.call_count == 2

    def test_command_output_delta_ignora_frame_repetido_entre_deltas(self):
        renderer, _ = self._renderer()

        with patch("lib.runner.codex.log_tool_result"), \
                patch("lib.runner.codex._log") as mock_log:
            renderer.handle({
                "kind": "command.output.delta",
                "item_id": "cmd-1",
                "payload": {"delta": "\rfrollo-progress [#####-----]  50%"},
                "provider": {},
            })
            renderer.handle({
                "kind": "command.output.delta",
                "item_id": "cmd-1",
                "payload": {"delta": "\rfrollo-progress [#####-----]  50%"},
                "provider": {},
            })

        assert mock_log.call_count == 1

    def test_command_finished_prefere_aggregated_output_sem_duplicar_delta(self):
        renderer, _ = self._renderer()

        with patch("lib.runner.codex.log_tool_result") as mock_result:
            renderer.handle({
                "kind": "command.output.delta",
                "item_id": "cmd-1",
                "payload": {"delta": "parcial\n"},
                "provider": {},
            })
            renderer.handle({
                "kind": "command.finished",
                "item_id": "cmd-1",
                "payload": {"command": {"output": "final\n"}},
                "provider": {},
            })

        mock_result.assert_called_once_with({"content": "final"})

    def test_command_finished_preserva_buffer_quando_aggregated_output_eh_sufixo(self):
        renderer, _ = self._renderer()

        with patch("lib.runner.codex.log_tool_result") as mock_result:
            renderer.handle({
                "kind": "command.output.delta",
                "item_id": "cmd-1",
                "payload": {"delta": "frollo-progress 10%\n"},
                "provider": {},
            })
            renderer.handle({
                "kind": "command.output.delta",
                "item_id": "cmd-1",
                "payload": {"delta": "frollo-progress 25%\n"},
                "provider": {},
            })
            renderer.handle({
                "kind": "command.finished",
                "item_id": "cmd-1",
                "payload": {"command": {"output": "frollo-progress 25%\n"}},
                "provider": {},
            })

        mock_result.assert_called_once_with({"content": "frollo-progress 10%\nfrollo-progress 25%"})

    def test_file_change_add_renderiza_como_write(self):
        renderer, _ = self._renderer()

        with patch("lib.runner.codex.log_tool_call") as mock_call:
            renderer.handle({
                "kind": "file.change.finished",
                "payload": {
                    "file": {
                        "path": "/repo/novo.md",
                        "operation": "add",
                        "diff": "",
                    },
                },
                "provider": {},
            })

        assert mock_call.call_args.args[0]["name"] == "Write"
        assert mock_call.call_args.args[0]["input"]["file_path"] == "/repo/novo.md"

    def test_file_change_modify_renderiza_como_edit(self):
        renderer, _ = self._renderer()

        with patch("lib.runner.codex.log_tool_call") as mock_call:
            renderer.handle({
                "kind": "file.change.finished",
                "payload": {
                    "file": {
                        "path": "/repo/existente.md",
                        "operation": "modify",
                        "diff": "",
                    },
                },
                "provider": {},
            })

        assert mock_call.call_args.args[0]["name"] == "Edit"
        assert mock_call.call_args.args[0]["input"]["file_path"] == "/repo/existente.md"

    def test_web_search_renderiza_no_pane_de_tools(self):
        renderer, _ = self._renderer()

        with patch("lib.runner.codex.log_tool_call") as mock_call:
            renderer.handle({
                "kind": "web.search.finished",
                "item_id": "ws-1",
                "payload": {"web_search": {"query": "site:example.com frollo"}},
                "provider": {},
            })

        assert mock_call.call_args.args[0]["name"] == "WebSearch"
        assert mock_call.call_args.args[0]["input"]["query"] == "site:example.com frollo"

    def test_web_search_started_e_completed_nao_duplicam_linha(self):
        renderer, _ = self._renderer()

        with patch("lib.runner.codex.log_tool_call") as mock_call:
            renderer.handle({
                "kind": "web.search.started",
                "item_id": "ws-1",
                "payload": {"web_search": {"query": "site:example.com frollo"}},
                "provider": {},
            })
            renderer.handle({
                "kind": "web.search.finished",
                "item_id": "ws-1",
                "payload": {"web_search": {"query": "site:example.com frollo"}},
                "provider": {},
            })

        assert mock_call.call_count == 1

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

    def test_spinner_nao_reaparece_depois_do_turn_done(self):
        renderer, _ = self._renderer()
        renderer.turn_done = True

        renderer.show_status()

        assert not renderer.spinner_shown

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

    def test_reasoning_completed_prefere_content_ao_summary(self):
        renderer, render = self._renderer()

        renderer.handle({
            "kind": "reasoning.completed",
            "item_id": "rs-1",
            "payload": {
                "summary_parts": ["**Lendo arquivos**"],
                "summary_text": "**Lendo arquivos**",
                "content_parts": ["linha interna 1", "linha interna 2"],
                "content_text": "linha interna 1\nlinha interna 2",
            },
            "provider": {},
        })

        pushed = [call.args[1] for call in render.push_file.call_args_list]
        assert pushed == ["linha interna 1", "linha interna 2"]

    def test_reasoning_completed_acrescenta_content_apos_summary_streamado(self):
        renderer, render = self._renderer()

        renderer.handle({
            "kind": "reasoning.summary.started",
            "item_id": "rs-1",
            "payload": {"summary_index": 0},
            "provider": {},
        })
        renderer.handle({
            "kind": "reasoning.delta",
            "item_id": "rs-1",
            "payload": {"delta": "**Lendo arquivos**", "visibility": "summary"},
            "provider": {},
        })
        renderer.handle({
            "kind": "reasoning.completed",
            "item_id": "rs-1",
            "payload": {
                "summary_parts": ["**Lendo arquivos**"],
                "summary_text": "**Lendo arquivos**",
                "content_parts": ["raciocínio mais completo"],
                "content_text": "raciocínio mais completo",
            },
            "provider": {},
        })

        pushed = [call.args[1] for call in render.push_file.call_args_list]
        assert pushed == ["**Lendo arquivos**", "raciocínio mais completo"]

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

    def test_assistant_delta_apos_completed_nao_insere_quebra_extra(self):
        renderer, render = self._renderer()

        with patch("lib.runner.assistant_text.col_is_mid_line", return_value=True):
            renderer.handle({
                "kind": "message.assistant.delta",
                "item_id": "msg-1",
                "payload": {"delta": "primeiro bloco."},
                "provider": {},
            })
            renderer.handle({
                "kind": "message.assistant.completed",
                "item_id": "msg-1",
                "payload": {},
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
            RESET,
            "\n",
            CHAT_FG + "\n" + RESET,
            CHAT_FG + "segundo bloco." + RESET,
        ]
        assert renderer.client._last_response_text == "primeiro bloco.\nsegundo bloco."

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
