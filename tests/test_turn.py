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

from lib.runner.turn import Turn


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
        """Opus 4.8 redige o thinking (só signature_delta) — sem texto, sem header."""
        turn = _turn(fake_client)
        turn.handle_event(_se({"type": "content_block_start", "content_block": {"type": "thinking"}}))
        with patch("lib.runner.turn._log") as mock_log, \
             patch("lib.runner.turn._resize_thinking") as mock_resize:
            turn.handle_event(_se({
                "type": "content_block_delta",
                "delta": {"type": "signature_delta", "signature": "abc"},
            }))
        assert turn.thinking_header_written is False
        mock_log.assert_not_called()
        mock_resize.assert_not_called()


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
    def test_registra_tool_use_e_chama_log_tool_call(self, fake_client):
        turn = _turn(fake_client)
        with patch("lib.runner.turn.log_tool_call") as mock_log_call:
            turn.handle_event({
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}}]},
            })
        mock_log_call.assert_called_once()
        assert turn._tool_names["tu1"] == "Bash"


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
