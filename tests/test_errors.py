"""Erro nenhum pode passar em silêncio — este arquivo é a garantia disso.

Cobre o sink (`lib/errors.py`) e a invariante do adapter Codex: qualquer mensagem
que o Frollo não entenda vira evento visível, nunca lista vazia.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from lib import errors
from lib.runner.codex import (
    _CODEX_IDLE_TIMEOUT,
    _CodexAdapter,
    _CodexProcess,
    _CodexRenderer,
    _codex_await,
    _codex_idle_timed_out,
)
from lib.runner.turn import Turn


@pytest.fixture
def sink(tmp_path, monkeypatch):
    """Redireciona errors.jsonl e o pane de tools para tmp_path."""
    log = tmp_path / "errors.jsonl"
    tools = tmp_path / "tools"
    monkeypatch.setattr(errors, "ERROR_LOG", log)
    monkeypatch.setattr(errors, "TOOLS_LOG", tools)
    return log, tools


def _records(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


class TestRecord:
    def test_registro_tem_campos_canonicos(self):
        rec = errors.record("codex", "explodiu", code="boom", detail="linha 1")

        assert rec["source"] == "codex"
        assert rec["message"] == "explodiu"
        assert rec["code"] == "boom"
        assert rec["severity"] == "error"
        assert rec["ts"]

    def test_severidade_desconhecida_vira_error(self):
        assert errors.record("x", "y", severity="catástrofe")["severity"] == "error"

    def test_raw_nao_serializavel_nao_derruba_o_registro(self):
        rec = errors.record("x", "y", raw={"obj": object()})

        assert rec["raw"]

    def test_raw_longo_e_truncado(self):
        rec = errors.record("x", "y", raw="z" * 9000)

        assert len(rec["raw"]) == errors._MAX_RAW_CHARS


class TestLog:
    def test_appenda_uma_linha_json_por_erro(self, sink):
        log, _ = sink

        errors.report("codex", "primeiro", chat=False, tools=False)
        errors.report("claude", "segundo", chat=False, tools=False)

        recs = _records(log)
        assert [r["message"] for r in recs] == ["primeiro", "segundo"]

    def test_rotaciona_uma_geracao_quando_passa_do_teto(self, sink, monkeypatch):
        log, _ = sink
        monkeypatch.setattr(errors, "_MAX_LOG_BYTES", 10)

        errors.report("codex", "antigo", chat=False, tools=False)
        errors.report("codex", "novo", chat=False, tools=False)

        assert [r["message"] for r in _records(log)] == ["novo"]
        assert "antigo" in (log.parent / (log.name + ".1")).read_text()

    def test_log_indisponivel_nao_levanta(self, tmp_path, monkeypatch):
        monkeypatch.setattr(errors, "ERROR_LOG", tmp_path / "arquivo" / "que" / "nao" / "abre")
        monkeypatch.setattr(errors, "_MAX_LOG_BYTES", 0)
        with patch("lib.errors.open", side_effect=OSError("read-only")):
            assert errors.report("codex", "explodiu", chat=False, tools=False)["message"] == "explodiu"


class TestSaida:
    def test_erro_aparece_no_chat_com_mensagem_e_codigo(self, sink, capsys):
        errors.report("codex/transport", "processo morreu", code="codex_process_died", tools=False)

        out = capsys.readouterr().out
        assert "processo morreu" in out
        assert "codex_process_died" in out
        assert "codex/transport" in out

    def test_chat_desligado_nao_escreve_no_stdout_mas_loga(self, sink, capsys):
        log, _ = sink

        errors.report("frollo/cota", "cota indisponível", severity="warning", chat=False, tools=False)

        assert capsys.readouterr().out == ""
        assert _records(log)[0]["message"] == "cota indisponível"

    def test_erro_aparece_no_pane_de_tools_com_detalhe(self, sink, capsys):
        _, tools = sink

        errors.report("codex", "falhou", detail="linha 1\nlinha 2", chat=False)

        conteudo = tools.read_text()
        assert "falhou" in conteudo
        assert "linha 1" in conteudo and "linha 2" in conteudo

    def test_detalhe_longo_e_cortado_com_ponteiro_pro_log(self, sink):
        detail = "\n".join(f"linha {i}" for i in range(50))

        linhas = errors.chat_lines(errors.record("codex", "falhou", detail=detail))

        assert len(linhas) == errors._CHAT_DETAIL_LINES + 2  # cabeçalho + corte
        assert str(errors.ERROR_LOG) in linhas[-1]

    def test_pane_de_tools_cresce_para_caber_o_erro(self, sink):
        with patch("lib.runner.panes._grow_tools") as grow:
            errors.report("codex", "falhou", detail="a\nb\nc", chat=False, tmux_srv="frollo")

        grow.assert_called_once()
        assert grow.call_args[0][1] >= 9

    def test_typewriter_e_drenado_antes_de_escrever_o_erro(self, sink, capsys):
        render = MagicMock()

        errors.report("codex", "falhou", tools=False, render=render)

        render.join.assert_called_once()

    def test_report_exception_guarda_o_traceback(self, sink):
        log, _ = sink

        try:
            raise ValueError("valor ruim")
        except ValueError as exc:
            errors.report_exception("frollo", exc, chat=False, tools=False)

        rec = _records(log)[0]
        assert rec["message"] == "ValueError: valor ruim"
        assert "Traceback" in rec["detail"]


class TestAdapterNuncaEngoleMensagem:
    """Invariante: nenhuma forma de mensagem sai do adapter sem evento."""

    def _adapter(self):
        client = MagicMock()
        client.session_id = None
        client.observed_model = ""
        return _CodexAdapter(client)

    def test_resposta_de_erro_do_app_server_vira_evento_de_erro(self):
        eventos = self._adapter().normalize({"id": 3, "error": {"code": -32000, "message": "thread não existe"}})

        assert [e["kind"] for e in eventos] == ["error"]
        assert eventos[0]["payload"]["error"]["message"] == "thread não existe"
        assert eventos[0]["payload"]["error"]["code"] == -32000

    def test_linha_nao_json_vira_evento_de_erro(self):
        eventos = self._adapter().normalize({"_raw": "thread 'main' panicked at src/main.rs:42"})

        assert eventos[0]["kind"] == "error"
        assert eventos[0]["payload"]["error"]["code"] == "codex_non_json_line"

    def test_request_sem_handler_vira_erro_com_request_para_recusa(self):
        eventos = self._adapter().normalize({"id": 9, "method": "session/askSomething", "params": {}})

        assert eventos[0]["kind"] == "error"
        assert eventos[0]["payload"]["request"] == {"id": 9, "method": "session/askSomething"}

    def test_notificacao_desconhecida_vira_notice_uma_vez_so(self):
        adapter = self._adapter()

        primeira = adapter.normalize({"method": "thread/novidade", "params": {}})
        segunda = adapter.normalize({"method": "thread/novidade", "params": {}})

        assert primeira[0]["kind"] == "notice"
        assert primeira[0]["payload"]["notice"]["level"] == "warning"
        assert segunda == []

    def test_item_de_tipo_desconhecido_vira_notice(self):
        eventos = self._adapter().normalize({
            "method": "item/completed",
            "params": {"item": {"id": "i1", "type": "webSearch"}},
        })

        assert eventos[0]["payload"]["notice"]["code"] == "codex_unknown_item"

    def test_item_de_erro_vira_evento_de_erro(self):
        eventos = self._adapter().normalize({
            "method": "item/completed",
            "params": {"item": {"id": "i1", "type": "error", "message": "modelo indisponível"}},
        })

        assert eventos[0]["kind"] == "error"
        assert eventos[0]["payload"]["error"]["message"] == "modelo indisponível"

    @pytest.mark.parametrize("msg", [
        {},
        {"id": 1},
        {"params": {"x": 1}},
        {"_raw": "boom"},
        {"id": 2, "error": "falhou"},
        {"id": 3, "method": "algo/desconhecido"},
        {"method": "algo/novo"},
        {"method": "item/started", "params": {"item": {"type": "desconhecido"}}},
        {"method": "item/completed", "params": {"item": {"type": "desconhecido"}}},
        "não sou objeto",
    ])
    def test_toda_forma_estranha_produz_algum_evento(self, msg):
        assert self._adapter().normalize(msg), f"mensagem engolida em silêncio: {msg!r}"


class TestTurnoNuncaTravaEmSilencio:
    def test_turno_longo_com_eventos_nao_expira(self):
        # 10 min de turno, último evento há 5s: segue vivo.
        assert not _codex_idle_timed_out(last_event_at=600.0, now=605.0)

    def test_silencio_prolongado_expira(self):
        assert _codex_idle_timed_out(last_event_at=0.0, now=_CODEX_IDLE_TIMEOUT + 0.1)

    def test_request_sem_handler_e_recusado_no_protocolo(self, tmp_path):
        proc = _CodexProcess(str(tmp_path))
        proc.proc = MagicMock()
        proc.client_log = tmp_path / "client.jsonl"
        escrito = []
        proc.proc.stdin.write.side_effect = escrito.append

        proc.respond_error(7, "frollo: método não suportado (x/y)")

        enviado = json.loads(escrito[0])
        assert enviado["id"] == 7
        assert enviado["error"]["code"] == -32601

    def test_handshake_com_erro_aborta_o_turno_e_reporta(self, sink):
        log, _ = sink
        proc = MagicMock()
        proc.wait_for.return_value = {"id": 1, "error": {"message": "sem credencial"}}
        adapter = _CodexAdapter(MagicMock())
        renderer = MagicMock()
        renderer._report_error.side_effect = lambda payload, event=None: errors.report(
            "codex", (payload.get("error") or {}).get("message"), chat=False, tools=False)
        renderer.handle.side_effect = lambda ev: errors.report(
            "codex", ev["payload"]["error"]["message"], chat=False, tools=False)

        assert _codex_await(proc, adapter, renderer, 1, "initialize") is None
        assert _records(log)[0]["message"] == "sem credencial"

    def test_handshake_sem_resposta_reporta_timeout(self, sink):
        proc = MagicMock()
        proc.wait_for.side_effect = TimeoutError()
        proc.exit_detail.return_value = "exit code: 1"
        renderer = MagicMock()

        assert _codex_await(proc, _CodexAdapter(MagicMock()), renderer, 1, "initialize") is None

        payload = renderer._report_error.call_args[0][0]
        assert payload["error"]["code"] == "codex_boot_timeout"
        assert payload["detail"] == "exit code: 1"


class TestBackendClaudeNaoEngoleErro:
    def _turn(self):
        client = MagicMock()
        client.tmux_srv = ""
        client._streaming_text = False
        return Turn(client, MagicMock(), {"typewriter": False, "gargoyles": False},
                    True, max_think_lines=20, idle_lines=8, render=MagicMock())

    def test_result_com_is_error_reporta_o_motivo(self, sink):
        log, _ = sink

        self._turn().handle_event({
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "result": "API error: overloaded",
        })

        rec = _records(log)[0]
        assert rec["message"] == "API error: overloaded"
        assert rec["code"] == "error_during_execution"

    def test_result_de_sucesso_nao_reporta_nada(self, sink):
        log, _ = sink

        self._turn().handle_event({"type": "result", "subtype": "success", "is_error": False})

        assert not log.exists()

    def test_linha_nao_json_no_stdout_vira_aviso(self, sink):
        log, _ = sink

        self._turn().handle_line("isto não é json\n")

        rec = _records(log)[0]
        assert rec["severity"] == "warning"
        assert rec["code"] == "non_json_stdout"

    def test_tipo_de_evento_desconhecido_e_registrado_uma_vez(self, sink, tmp_path, monkeypatch):
        log, _ = sink
        monkeypatch.setattr("lib.runner.turn.RUNDIR", tmp_path)
        turn = self._turn()

        turn.handle_event({"type": "coisa_nova"})
        turn.handle_event({"type": "coisa_nova"})

        recs = _records(log)
        assert len(recs) == 1
        assert recs[0]["code"] == "unknown_event_type"


class TestRendererReportaErro:
    def _renderer(self):
        client = MagicMock()
        client.tmux_srv = ""
        client._streaming_text = False
        renderer = _CodexRenderer(client, {"typewriter": False, "thinking_autoresize": False}, MagicMock(), 0)
        renderer.render = MagicMock()
        return renderer

    def test_evento_de_erro_chega_no_sink(self, sink):
        log, _ = sink
        renderer = self._renderer()

        renderer.handle({
            "kind": "error",
            "payload": {"error": {"message": "turno falhou", "code": "x", "source": "provider"}},
            "raw": {"id": 1},
            "provider": {},
        })

        rec = _records(log)[0]
        assert rec["message"] == "turno falhou"
        assert rec["source"] == "codex/provider"

    def test_notice_de_warning_vai_pro_log_mesmo_escondido_da_tela(self, sink):
        log, _ = sink
        renderer = self._renderer()

        renderer.handle({
            "kind": "notice",
            "payload": {"notice": {
                "level": "warning",
                "message": "sandbox sem user namespaces",
                "code": "codex_linux_sandbox_userns",
            }},
            "provider": {},
        })

        assert _records(log)[0]["severity"] == "warning"
