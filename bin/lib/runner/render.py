"""Fila de render desacoplada do loop de eventos (Fase 3 do PLANO_MELHORIAS.md).

Antes, `_typewrite`/`log_animated` dormiam no thread principal — enquanto animavam,
nada era drenado de `proc.stdout`; se o modelo produzisse mais rápido que a
animação consumia, o pipe de 64KB enchia e o CLI bloqueava no write (a animação
virava backpressure sobre o turno). `RenderQueue` move a apresentação (typewriter
no stdout do chat, no pane de thinking, no pane de tools) para uma thread própria,
consumida a partir de uma fila única — fila única preserva a ordem relativa entre
chat/thinking/gárgulas (duas filas dessincronizariam a narrativa). O loop de
ingestão (`run_turn`) nunca mais dorme por animação.

`skip()` é implícito: uma tecla detectada durante a animação liga um flag
compartilhado que faz a fila inteira (item atual + o que já estiver
enfileirado) despejar sem delay, até a fila esvaziar — aí o flag reseta sozinho.

O spinner é responsabilidade desta fila, não do loop principal: quando ociosa
(sem itens) ela chama `status_cb` periodicamente; e mesmo ocupada (ex.: um
thinking longo), `_maybe_tick` intercala atualizações do spinner entre
caracteres, para o spinner nunca congelar durante uma animação longa.
"""
import os
import queue
import select
import sys
import threading
import time

from ..typewriter import _char_delay
from .text import _ANSI_SEQ, _advance_col

_SENTINEL = object()
_TICK_INTERVAL = 0.15


class RenderQueue:
    """Consumidor único de itens de render, numa thread separada do loop de eventos."""

    def __init__(self):
        self._q = queue.Queue()
        self._skip = threading.Event()
        # RLock, não Lock: _write_stdout roda dentro do lock (ver _dispatch) e
        # chama _maybe_tick a cada char — se um tick disparar nesse meio-tempo,
        # _tick tenta readquirir o mesmo lock, na mesma thread. Com Lock comum
        # isso é autodeadlock (thread trava esperando o próprio lock).
        self.stdout_lock = threading.RLock()
        self.active = False
        self._suspended = False
        self._thread = None
        self._status_cb = None
        self._clear_status_cb = None
        self._is_streaming_cb = None
        self._last_tick = 0.0

    # -- ciclo de vida ------------------------------------------------------

    def start(self, status_cb=None, clear_status_cb=None, is_streaming_cb=None):
        self._status_cb = status_cb
        self._clear_status_cb = clear_status_cb
        self._is_streaming_cb = is_streaming_cb
        self.active = True
        self._last_tick = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Fim normal do turno: deixa o que já foi enfileirado terminar de animar
        (sem pressa — não há mais dados chegando do subprocess), então para a
        thread. Chamar antes de qualquer verificação final de estado (ex.:
        `turn.spinner_shown`), pra garantir que nada mais escreve no stdout."""
        self.active = False
        self._q.join()
        self._q.put(_SENTINEL)
        if self._thread:
            self._thread.join()

    def cancel(self):
        """Ctrl+C: aborta a animação em andamento e esvazia a fila na hora — não
        faz sentido esperar o typewriter acabar quando o usuário já pediu pra
        parar. Ordem: matar proc (responsabilidade do chamador) + drenar fila
        (via skip forçado) + parar thread."""
        self.active = False
        self._skip.set()
        self._q.put(_SENTINEL)
        if self._thread:
            self._thread.join()

    # -- enfileiramento -------------------------------------------------------

    def push_stdout(self, text, delay=0.015):
        self._q.put(("stdout", text, delay))

    def push_file(self, path, text, delay=0.030, on_newline=None, hesitate=True):
        self._q.put(("file", path, text, delay, on_newline, hesitate))

    def join(self):
        """Bloqueia até a fila esvaziar — ponto de sincronização usado quando o
        chamador precisa ler estado que só é válido após o render terminar
        (ex.: posição da coluna) ou antes de escrever algo fora da fila."""
        self._q.join()

    # -- pontos de sincronização ---------------------------------------------

    def suspend(self):
        """Flush determinístico antes de algo que escreve direto no stdout fora
        da fila (banner de permissão): espera a fila esvaziar, apaga o spinner
        se estiver visível, e segura o lock — o spinner não desenha de novo até
        `resume()`, então não há como a mensagem aparecer no meio de uma frase
        nem intercalada com um frame do spinner."""
        self._suspended = True
        self._q.join()
        self.stdout_lock.acquire()
        if self._clear_status_cb:
            self._clear_status_cb()

    def resume(self):
        self._suspended = False
        self.stdout_lock.release()

    # -- thread consumidora ---------------------------------------------------

    def _run(self):
        self._last_tick = time.monotonic()
        while True:
            try:
                item = self._q.get(timeout=_TICK_INTERVAL)
            except queue.Empty:
                self._skip.clear()
                self._maybe_tick()
                continue
            if item is _SENTINEL:
                self._q.task_done()
                return
            try:
                self._dispatch(item)
            except Exception:
                # Um erro de render (ex.: disco cheio, stdin em estado
                # inesperado) não pode travar a fila pra sempre — sem o
                # task_done() do finally, join()/stop() ficariam bloqueados
                # esperando um item que nunca termina.
                pass
            finally:
                self._q.task_done()

    def _dispatch(self, item):
        if item[0] == "stdout":
            _, text, delay = item
            with self.stdout_lock:
                if self._clear_status_cb:
                    self._clear_status_cb()
                self._write_stdout(text, delay)
        else:
            _, path, text, delay, on_newline, hesitate = item
            self._write_file(path, text, delay, on_newline, hesitate)

    def _maybe_tick(self):
        now = time.monotonic()
        if now - self._last_tick < _TICK_INTERVAL:
            return
        self._last_tick = now
        self._tick()

    def _tick(self):
        if not self.active or self._suspended:
            return
        if self._is_streaming_cb and self._is_streaming_cb():
            return
        if self._status_cb:
            with self.stdout_lock:
                self._status_cb()

    def _wait_or_skip(self, delay):
        """Espera `delay`; retorna True se uma tecla chegou nesse meio-tempo —
        nesse caso liga o skip compartilhado, que persiste até a fila esvaziar
        (o consumidor então despeja o resto sem delay, não só o item atual)."""
        if self._skip.is_set():
            return True
        ready, _, _ = select.select([sys.stdin], [], [], delay)
        if ready:
            os.read(sys.stdin.fileno(), 1024)
            self._skip.set()
            return True
        return False

    # -- escrita ----------------------------------------------------------

    def _write_stdout(self, text, delay):
        if delay <= 0 or self._skip.is_set():
            sys.stdout.write(text)
            sys.stdout.flush()
            _advance_col(_ANSI_SEQ.sub('', text))
            return
        parts = _ANSI_SEQ.split(text)
        for i, part in enumerate(parts):
            if _ANSI_SEQ.match(part):
                sys.stdout.write(part)
                sys.stdout.flush()
                continue
            for j, ch in enumerate(part):
                sys.stdout.write(ch)
                sys.stdout.flush()
                _advance_col(ch)
                self._maybe_tick()
                if self._wait_or_skip(_char_delay(ch, delay)):
                    rest = part[j + 1:] + ''.join(parts[i + 1:])
                    sys.stdout.write(rest)
                    sys.stdout.flush()
                    _advance_col(_ANSI_SEQ.sub('', rest))
                    return

    def _write_file(self, path, text, delay, on_newline, hesitate):
        if delay <= 0 or self._skip.is_set():
            with open(path, "a", buffering=1) as f:
                f.write(text)
            return
        with open(path, "a", buffering=1) as f:
            for i, ch in enumerate(text):
                if ch == '\n' and on_newline:
                    on_newline()
                f.write(ch)
                f.flush()
                self._maybe_tick()
                if self._wait_or_skip(_char_delay(ch, delay, hesitate=hesitate)):
                    f.write(text[i + 1:])
                    f.flush()
                    return
