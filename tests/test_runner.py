"""Testes para run_turn — comportamento com dependências ausentes."""
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from lib.runner import run_turn, _parse_rate_limit_line, _terminate_proc
from lib.theme import THINKING_FG


@pytest.fixture()
def fake_client():
    client = MagicMock()
    client.mode.value = "normal"
    client.first_turn = True
    client.resume_id = None
    client.nvim_pane = ""
    client.tmux_srv = ""
    client.editor_bin = ""
    client.cwd = "/tmp"
    client._streaming_text = False
    return client


class TestClaudeNaoEncontrado:
    def test_retorna_false_quando_claude_ausente(self, fake_client):
        with patch("lib.runner.subprocess.Popen", side_effect=FileNotFoundError):
            result = run_turn(fake_client, "oi")
        assert result is False

    def test_exibe_mensagem_amigavel(self, fake_client, capsys):
        with patch("lib.runner.subprocess.Popen", side_effect=FileNotFoundError):
            run_turn(fake_client, "oi")
        out = capsys.readouterr().out
        assert "claude" in out.lower()
        assert "npm" in out or "não encontrado" in out


class _FakeStdout:
    """stdout de subprocesso falso: serve linhas pré-definidas, depois EOF."""
    def __init__(self, lines):
        self._it = iter(lines)

    def readline(self):
        return next(self._it, "")


def _se(event):
    return json.dumps({"type": "stream_event", "event": event})


class _FakeRenderQueue:
    """Substitui RenderQueue nesses testes: aplica os pushes na hora, sem thread
    nem sleep — o que essas asserções verificam é o dispatch feito por Turn
    (o que é enfileirado e quando), não a mecânica interna da fila (que tem
    testes próprios em test_render.py)."""

    def __init__(self, anim_calls):
        self._anim_calls = anim_calls

    def start(self, **kw):
        pass

    def stop(self):
        pass

    def cancel(self):
        pass

    def join(self):
        pass

    def suspend(self):
        pass

    def resume(self):
        pass

    def push_stdout(self, text, delay=0.015):
        sys.stdout.write(text)
        sys.stdout.flush()

    def push_file(self, path, text, delay=0.030, on_newline=None, hesitate=True):
        self._anim_calls.append(text)


def _run_stream(fake_client, lines, config_override=None):
    """Roda run_turn com um stream fixo, capturando o que iria pro pane de thinking.

    Retorna (chamadas_de__log, chamadas_de_push_file, chamadas_de_resize).
    """
    proc = MagicMock()
    proc.stdout = _FakeStdout([l + "\n" for l in lines])
    proc.stderr = _FakeStdout([])  # EOF imediato — thread _stderr_reader não deve rodar solta
    proc.wait.return_value = 0

    # totais numéricos reais (MagicMock ignora o default do getattr)
    fake_client._total_input_tokens = 0
    fake_client._total_output_tokens = 0
    fake_client._total_elapsed = 0.0
    fake_client._total_cost = 0.0
    fake_client._streaming_text = False

    cfg = {"typewriter": False, "gargoyles": False}
    cfg.update(config_override or {})

    log_calls, anim_calls, resize_calls = [], [], []

    rundir = Path(tempfile.mkdtemp())  # sem stats_tty → pula o bloco de stats
    devnull = open(os.devnull, "r")
    try:
        with patch("lib.runner.subprocess.Popen", return_value=proc), \
             patch("lib.runner.RUNDIR", rundir), \
             patch("lib.runner.turn.RUNDIR", rundir), \
             patch("lib.runner.select.select", new=lambda *a, **k: ([proc.stdout], [], [])), \
             patch("lib.runner.termios.tcgetattr", return_value=[0, 0, 0, 0, 0, 0, [0] * 32]), \
             patch("lib.runner.termios.tcsetattr"), \
             patch("lib.runner.config.load", return_value=cfg), \
             patch("lib.runner._resize_thinking", side_effect=lambda srv, size: resize_calls.append(size)), \
             patch("lib.runner.turn._resize_thinking", side_effect=lambda srv, size: resize_calls.append(size)), \
             patch("lib.runner.turn._log", side_effect=lambda path, text: log_calls.append(text)), \
             patch("lib.runner.RenderQueue", side_effect=lambda: _FakeRenderQueue(anim_calls)), \
             patch.object(sys, "stdin", devnull):
            run_turn(fake_client, "oi")
    finally:
        devnull.close()
    return log_calls, anim_calls, resize_calls


# Streams reais capturados de `claude --output-format stream-json` (maio 2026):
# Sonnet expõe o thinking via thinking_delta; Opus 4.8 o redige (só signature_delta).

_OPUS_REDIGIDO = [
    _se({"type": "message_start", "message": {"model": "claude-opus-4-8", "usage": {"input_tokens": 1}}}),
    _se({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
    _se({"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "abc"}}),
    _se({"type": "content_block_stop", "index": 0}),
    _se({"type": "content_block_start", "index": 1, "content_block": {"type": "text"}}),
    _se({"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Nope, 51 = 3*17."}}),
    _se({"type": "content_block_stop", "index": 1}),
    _se({"type": "message_delta", "usage": {"output_tokens": 42, "output_tokens_details": {"thinking_tokens": 10}}}),
    _se({"type": "message_stop"}),
    json.dumps({"type": "result", "session_id": "s1"}),
]

_SONNET_VISIVEL = [
    _se({"type": "message_start", "message": {"model": "claude-sonnet-4-6", "usage": {"input_tokens": 1}}}),
    _se({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
    _se({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "all but 9 die means 9 survive."}}),
    _se({"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "abc"}}),
    _se({"type": "content_block_stop", "index": 0}),
    _se({"type": "content_block_start", "index": 1, "content_block": {"type": "text"}}),
    _se({"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "9 sheep."}}),
    _se({"type": "content_block_stop", "index": 1}),
    _se({"type": "message_stop"}),
    json.dumps({"type": "result", "session_id": "s1"}),
]

_VISIVEL_DEPOIS_VAZIO = [
    _se({"type": "message_start", "message": {"model": "claude-sonnet-5", "usage": {"input_tokens": 1}}}),
    _se({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
    _se({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "primeiro thinking visivel"}}),
    _se({"type": "content_block_stop", "index": 0}),
    _se({"type": "content_block_start", "index": 1, "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
    _se({"type": "content_block_delta", "index": 1, "delta": {"type": "signature_delta", "signature": "abc"}}),
    _se({"type": "content_block_stop", "index": 1}),
    _se({"type": "content_block_start", "index": 2, "content_block": {"type": "text"}}),
    _se({"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": "ok"}}),
    _se({"type": "content_block_stop", "index": 2}),
    _se({"type": "message_delta", "usage": {"output_tokens": 20, "output_tokens_details": {"thinking_tokens": 15}}}),
    _se({"type": "message_stop"}),
    json.dumps({"type": "result", "session_id": "s1"}),
]


class TestThinkingRedigido:
    """Opus 4.8 omite o thinking — não cresce o pane, mas avisa que foi omitido."""

    def test_thinking_vazio_nao_escreve_header(self, fake_client, capsys):
        log_calls, anim_calls, resize_calls = _run_stream(fake_client, _OPUS_REDIGIDO)
        # nenhum texto de thinking foi animado, nenhum header (cor de thinking) escrito
        assert anim_calls == []
        assert not any(THINKING_FG in t for t in log_calls)
        # nem cresceu o pane (sem resize pra _max_think_lines / "summary")
        assert resize_calls == []

    def test_escreve_nota_de_omitido(self, fake_client, capsys):
        log_calls, _, _ = _run_stream(fake_client, _OPUS_REDIGIDO)
        assert any("omitiu o thinking" in t for t in log_calls)

    def test_resposta_ainda_aparece(self, fake_client, capsys):
        _run_stream(fake_client, _OPUS_REDIGIDO)
        assert "51 = 3*17" in capsys.readouterr().out


class TestThinkingVisivel:
    """Sonnet expõe o thinking — header + texto renderizado como antes."""

    def test_thinking_delta_renderiza(self, fake_client, capsys):
        log_calls, anim_calls, resize_calls = _run_stream(fake_client, _SONNET_VISIVEL)
        assert any("9 survive" in t for t in anim_calls)
        assert any(THINKING_FG in t for t in log_calls)  # header foi escrito
        assert resize_calls  # cresceu o pane

    def test_bloco_vazio_depois_de_visivel_nao_sobrescreve_com_omitido(self, fake_client, capsys):
        log_calls, anim_calls, resize_calls = _run_stream(fake_client, _VISIVEL_DEPOIS_VAZIO)
        assert any("primeiro thinking" in t for t in anim_calls)
        assert not any("omitiu o thinking" in t for t in log_calls)
        assert resize_calls


class TestAutoResizeDesligado:
    """Com thinking_autoresize=False o pane nunca é redimensionado."""

    def test_sem_resize_mesmo_com_thinking_visivel(self, fake_client, capsys):
        log_calls, anim_calls, resize_calls = _run_stream(
            fake_client, _SONNET_VISIVEL, config_override={"thinking_autoresize": False}
        )
        assert resize_calls == []           # nenhum resize
        assert any("9 survive" in t for t in anim_calls)  # mas o texto ainda renderiza


def _make_proc(lines):
    """Processo falso que sobrevive ao fim do turno (poll() sempre None) — simula
    o processo persistente da Fase 4, que não sai sozinho depois do 'result'."""
    proc = MagicMock()
    proc.stdout = _FakeStdout([l + "\n" for l in lines])
    proc.stderr = _FakeStdout([])
    proc.poll.return_value = None
    return proc


def _select_any(rlist, wlist, xlist, *a, **k):
    return (rlist, [], [])


class TestPersistentMode:
    """Modo `persistent: true` (Fase 4): reaproveita o processo entre turnos em vez
    de spawnar um novo a cada mensagem. `fake_client.proc` começa None e é mantido
    pelo próprio run_turn entre chamadas, como faria o client real."""

    def _ctx(self, cfg, rundir, popen_mock):
        devnull = open(os.devnull, "r")
        return devnull, [
            patch("lib.runner.subprocess.Popen", popen_mock),
            patch("lib.runner.RUNDIR", rundir),
            patch("lib.runner.turn.RUNDIR", rundir),
            patch("lib.runner.select.select", new=_select_any),
            patch("lib.runner.termios.tcgetattr", return_value=[0, 0, 0, 0, 0, 0, [0] * 32]),
            patch("lib.runner.termios.tcsetattr"),
            patch("lib.runner.config.load", return_value=cfg),
            patch("lib.runner.RenderQueue", side_effect=lambda: _FakeRenderQueue([])),
            patch.object(sys, "stdin", devnull),
        ]

    def _reset_totals(self, client):
        client.proc = None
        client.model = "sonnet"
        client._total_input_tokens = 0
        client._total_output_tokens = 0
        client._total_elapsed = 0.0
        client._total_cost = 0.0
        client._streaming_text = False

    def test_reaproveita_processo_entre_turnos(self, fake_client):
        self._reset_totals(fake_client)
        cfg = {"typewriter": False, "gargoyles": False, "persistent": True}
        proc1 = _make_proc([
            json.dumps({"type": "result", "session_id": "s1"}),
            json.dumps({"type": "result", "session_id": "s2"}),
        ])
        popen_mock = MagicMock(side_effect=[proc1])
        rundir = Path(tempfile.mkdtemp())
        devnull, patches = self._ctx(cfg, rundir, popen_mock)
        try:
            from contextlib import ExitStack
            with ExitStack() as stack:
                for p in patches:
                    stack.enter_context(p)
                run_turn(fake_client, "oi")
                assert fake_client.session_id == "s1"
                run_turn(fake_client, "de novo")
                assert fake_client.session_id == "s2"
        finally:
            devnull.close()
        assert popen_mock.call_count == 1
        assert fake_client.proc is proc1
        proc1.wait.assert_not_called()  # persistente: não espera o processo sair

    def test_respawna_quando_modo_muda(self, fake_client):
        self._reset_totals(fake_client)
        cfg = {"typewriter": False, "gargoyles": False, "persistent": True}
        proc1 = _make_proc([json.dumps({"type": "result", "session_id": "s1"})])
        proc2 = _make_proc([json.dumps({"type": "result", "session_id": "s2"})])
        popen_mock = MagicMock(side_effect=[proc1, proc2])
        rundir = Path(tempfile.mkdtemp())
        devnull, patches = self._ctx(cfg, rundir, popen_mock)
        try:
            from contextlib import ExitStack
            with ExitStack() as stack:
                for p in patches:
                    stack.enter_context(p)
                run_turn(fake_client, "oi")
                fake_client.mode.value = "auto"  # equivalente a Shift+Tab entre turnos
                run_turn(fake_client, "de novo")
        finally:
            devnull.close()
        assert popen_mock.call_count == 2
        assert fake_client.proc is proc2
        proc1.terminate.assert_called_once()  # processo antigo foi encerrado, não abandonado

    def test_nao_le_alem_do_result_evento(self, fake_client):
        # 'result' delimita o turno -- o que vem depois no stream não deveria ser
        # consumido antes do próximo turno (senão um processo persistente travaria
        # esperando o turno N+1 enquanto ainda lê o N).
        self._reset_totals(fake_client)
        cfg = {"typewriter": False, "gargoyles": False, "persistent": True}
        proc1 = _make_proc([
            json.dumps({"type": "result", "session_id": "s1"}),
            "isto-nao-e-json-e-nao-deveria-ser-lido-neste-turno",
        ])
        popen_mock = MagicMock(side_effect=[proc1])
        rundir = Path(tempfile.mkdtemp())
        devnull, patches = self._ctx(cfg, rundir, popen_mock)
        try:
            from contextlib import ExitStack
            with ExitStack() as stack:
                for p in patches:
                    stack.enter_context(p)
                run_turn(fake_client, "oi")
        finally:
            devnull.close()
        anomalies = rundir / "stdout-anomalies.log"
        assert not anomalies.exists() or "isto-nao-e-json" not in anomalies.read_text()


class TestTerminateProc:
    """_terminate_proc — encerramento do processo persistente (achado do spike:
    stdin fechado não basta, precisa de SIGTERM + timeout + SIGKILL de reserva)."""

    def test_noop_se_processo_ja_morreu(self):
        proc = MagicMock()
        proc.poll.return_value = 0
        _terminate_proc(proc)
        proc.terminate.assert_not_called()
        proc.kill.assert_not_called()

    def test_noop_se_processo_none(self):
        _terminate_proc(None)  # não deve levantar

    def test_sigterm_basta(self):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.stdin.closed = False
        _terminate_proc(proc)
        proc.stdin.close.assert_called_once()
        proc.terminate.assert_called_once()
        proc.wait.assert_called_once()
        proc.kill.assert_not_called()

    def test_sigkill_de_reserva_se_terminate_nao_basta(self):
        import subprocess as _sp
        proc = MagicMock()
        proc.poll.return_value = None
        proc.stdin.closed = False
        proc.wait.side_effect = [_sp.TimeoutExpired(cmd="claude", timeout=3.0), None]
        _terminate_proc(proc)
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()
        assert proc.wait.call_count == 2


class TestParseRateLimitLine:
    """_parse_rate_limit_line — função pura extraída do parsing textual de stderr."""

    def test_linha_sem_rate_limit_retorna_none(self):
        assert _parse_rate_limit_line("some regular stderr noise") is None

    def test_linha_vazia_retorna_none(self):
        assert _parse_rate_limit_line("") is None

    def test_hit_your_limit_sem_horario(self):
        result = _parse_rate_limit_line("Claude AI usage limit reached, you hit your limit")
        assert result is not None
        assert result["reset_str"] is None
        assert "hit your limit" in result["msg"]

    def test_extrai_horario_de_reset_am(self):
        result = _parse_rate_limit_line("5-hour limit reached ∙ resets 3:00am")
        assert result is not None
        assert result["reset_str"] is not None

    def test_extrai_horario_de_reset_pm(self):
        result = _parse_rate_limit_line("Your limit resets 11:45pm")
        assert result is not None
        assert result["reset_str"] is not None
