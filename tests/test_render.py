"""Testes para lib/runner/render.py — RenderQueue (Fase 3 do PLANO_MELHORIAS.md).

Cobre a mecânica da fila (ordenação, skip, suspend/resume, stop/cancel) — o
dispatch de eventos do turno (o que é enfileirado e quando) é coberto em
test_turn.py, que usa um MagicMock no lugar de RenderQueue.
"""
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from lib.runner.render import RenderQueue


@pytest.fixture()
def no_skip():
    """Neutraliza a leitura de stdin usada pra detectar skip — sem isso, testes
    que exercitam o loop de delay>0 ficariam reféns do select() real em stdin."""
    with patch("lib.runner.render.select.select", return_value=([], [], [])):
        yield


class TestPushStdoutInstant:
    def test_delay_zero_escreve_direto(self, capsys):
        rq = RenderQueue()
        rq.start()
        rq.push_stdout("oi", delay=0)
        rq.stop()
        assert capsys.readouterr().out == "oi"


class TestPushFileOrdenacao:
    def test_ordem_fifo_preservada(self, tmp_path, no_skip):
        path = tmp_path / "out.log"
        rq = RenderQueue()
        rq.start()
        rq.push_file(path, "um ", delay=0)
        rq.push_file(path, "dois ", delay=0)
        rq.push_file(path, "tres", delay=0)
        rq.stop()
        assert path.read_text() == "um dois tres"

    def test_join_espera_fila_esvaziar(self, tmp_path, no_skip):
        path = tmp_path / "out.log"
        rq = RenderQueue()
        rq.start()
        rq.push_file(path, "conteudo", delay=0.001)
        rq.join()
        assert path.read_text() == "conteudo"
        rq.stop()


class TestStdoutEFileMisturados:
    def test_ordem_relativa_entre_stdout_e_arquivo(self, tmp_path, capsys, no_skip):
        """Fila única: um push_file entre dois push_stdout não pode vazar antes
        do primeiro nem atrasar o segundo além do necessário — mas o que importa
        pro teste é que cada destino recebe seu conteúdo completo e correto."""
        path = tmp_path / "out.log"
        rq = RenderQueue()
        rq.start()
        rq.push_stdout("a", delay=0)
        rq.push_file(path, "meio", delay=0)
        rq.push_stdout("b", delay=0)
        rq.stop()
        assert capsys.readouterr().out == "ab"
        assert path.read_text() == "meio"


class TestSkip:
    def test_tecla_despeja_resto_da_fila_sem_delay(self, tmp_path):
        """Uma tecla detectada durante a animação liga o skip compartilhado —
        não só o item corrente esvazia na hora, o que já estiver enfileirado
        também (persiste até a fila ficar vazia de novo)."""
        path = tmp_path / "out.log"
        rq = RenderQueue()
        fake_stdin = MagicMock()
        fake_stdin.fileno.return_value = 0

        # primeira leitura de stdin "acha" uma tecla (dispara o skip); chamadas
        # seguintes não importam porque _skip já vai estar setado.
        with patch("lib.runner.render.sys.stdin", fake_stdin), \
             patch("lib.runner.render.select.select", return_value=([fake_stdin], [], [])), \
             patch("lib.runner.render.os.read", return_value=b"x"):
            rq.start()
            rq.push_file(path, "um pouco de texto bem longo", delay=0.05)
            rq.push_file(path, " mais um pedaco", delay=0.05)
            start = time.monotonic()
            rq.stop()
            elapsed = time.monotonic() - start

        assert path.read_text() == "um pouco de texto bem longo mais um pedaco"
        # sem o skip, só o primeiro item já levaria ~1.4s (27 chars * 0.05s)
        assert elapsed < 0.5


class TestSuspendResume:
    def test_suspend_espera_fila_e_chama_clear(self, tmp_path, no_skip):
        path = tmp_path / "out.log"
        calls = []
        rq = RenderQueue()
        rq.start(clear_status_cb=lambda: calls.append("clear"))
        rq.push_file(path, "x", delay=0)
        rq.suspend()
        assert path.read_text() == "x"
        assert calls == ["clear"]
        rq.resume()
        rq.stop()

    def test_nada_desenha_enquanto_suspenso(self):
        """Enquanto suspenso, o tick ocioso não deve chamar status_cb — mesmo
        que o turno esteja marcado como ativo."""
        calls = []
        rq = RenderQueue()
        rq.start(status_cb=lambda: calls.append("status"))
        rq.suspend()
        rq._tick()  # simula o que o tick ocioso faria
        assert calls == []
        rq.resume()

    def test_resume_libera_lock_para_o_tick(self):
        calls = []
        rq = RenderQueue()
        rq.start(status_cb=lambda: calls.append("status"))
        rq.suspend()
        rq.resume()
        rq._tick()
        assert calls == ["status"]
        rq.stop()


class TestTick:
    def test_tick_pulado_durante_streaming_text(self):
        calls = []
        rq = RenderQueue()
        rq.start(status_cb=lambda: calls.append("status"), is_streaming_cb=lambda: True)
        rq._tick()
        assert calls == []
        rq.stop()

    def test_tick_chama_status_quando_ocioso(self):
        calls = []
        rq = RenderQueue()
        rq.start(status_cb=lambda: calls.append("status"), is_streaming_cb=lambda: False)
        rq._tick()
        assert calls == ["status"]
        rq.stop()

    def test_tick_durante_escrita_de_stdout_nao_faz_autodeadlock(self, capsys):
        """Regressão: _write_stdout roda com stdout_lock preso (ver _dispatch) e
        chama _maybe_tick a cada char — se o intervalo de tick vencer no meio da
        escrita, _tick tenta pegar o mesmo lock, na mesma thread. Com um Lock
        comum isso trava pra sempre; precisa ser RLock.

        Usa a ponta de leitura de um pipe real como stdin — sem escrever nela,
        select() espera o timeout de verdade (ao contrário de um mock que
        retorna na hora), então o tempo decorrido durante a escrita é real."""
        calls = []
        rq = RenderQueue()
        read_fd, write_fd = os.pipe()
        try:
            with patch("lib.runner.render.sys.stdin", os.fdopen(read_fd, "rb")), \
                 patch("lib.runner.render._TICK_INTERVAL", 0.01):
                rq.start(status_cb=lambda: calls.append("status"), is_streaming_cb=lambda: False)
                rq.push_stdout("x" * 60, delay=0.003)
                # join() (não stop()) — precisa continuar "active" enquanto a
                # escrita roda de verdade, senão _tick() nem chega a tentar o
                # lock (early return por `active=False`) e o teste não prova nada.
                rq.join()
                rq.stop()  # trava aqui (para sempre) se stdout_lock não for reentrante
        finally:
            os.close(write_fd)
        assert "x" * 60 in capsys.readouterr().out
        assert calls  # pelo menos um tick disparou no meio da escrita


class TestStopECancel:
    def test_stop_para_a_thread(self):
        rq = RenderQueue()
        rq.start()
        rq.stop()
        assert not rq._thread.is_alive()

    def test_cancel_apos_stop_e_seguro(self):
        """finally do run_turn chama cancel() incondicionalmente, mesmo quando
        stop() já rodou no caminho feliz — precisa ser um no-op seguro."""
        rq = RenderQueue()
        rq.start()
        rq.stop()
        rq.cancel()  # não deve levantar nem travar

    def test_cancel_forca_esvaziamento_rapido(self, tmp_path):
        path = tmp_path / "out.log"
        rq = RenderQueue()
        rq.start()
        rq.push_file(path, "conteudo inteiro", delay=0.05)
        start = time.monotonic()
        rq.cancel()
        elapsed = time.monotonic() - start
        assert elapsed < 0.5
        assert path.read_text() == "conteudo inteiro"
