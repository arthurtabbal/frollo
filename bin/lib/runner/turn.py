"""Máquina de estados de um turno: consome linhas do stream-json e despacha por tipo.

Extraído de `run_turn` (Fase 2 do PLANO_MELHORIAS.md) — mesmo comportamento, estado que
antes eram ~30 variáveis locais/closures agora são atributos de `Turn`. `run_turn`
(`runner/__init__.py`) fica responsável por spawnar o subprocess, alimentar
`Turn.handle_line` a partir do loop `select` e finalizar (stats/cota/restore).
"""
import json
import re
import sys
import time
from datetime import datetime

from ..theme import (
    DIM, RESET, YELLOW,
    _F, _GLOW, MdBuffer,
    CHAT_FG, THINKING_FG, THINKING_TS,
    CLEAR,
)
from ..tools import log_tool_call, log_tool_result, TOOLS_LOG, RUNDIR, _log, _ts
from ..gargulas import _gargula_comment

from .text import col_is_mid_line
from .panes import THINKING_LOG, _resize_thinking
from .permissions import _handle_permission, _handle_permission_ask, _handle_control_request, _write_stdin
from .stats import _fmt_tok


class Turn:
    """Estado de um turno em andamento + dispatch de eventos do stream-json."""

    def __init__(self, client, proc, cfg, thinking_autoresize, max_think_lines, idle_lines, render):
        self.client = client
        self.proc = proc
        self.cfg = cfg
        self.thinking_autoresize = thinking_autoresize
        self._max_think_lines = max_think_lines
        self._idle_lines = idle_lines
        self.render = render

        self._tool_names = {}
        self.start_time = time.time()
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_creation_tokens = 0
        self.current_block = None
        self.text_started = False
        self.text_block_count = 0
        self.fire_frame = 0
        self.md_buf = MdBuffer()
        self.thinking_lines = idle_lines
        self.thinking_count = 0
        self.thinking_col = 0
        self.thinking_header_written = False
        self.thinking_visible = False
        self._empty_thinking_blocks = 0
        self._thinking_notice_written = False
        self.thinking_tokens_seen = 0
        self.spinner_shown = False
        self._suppress_perm_text = False
        self.perm_approved = False
        self.turn_done = False
        self.rate_limited = False
        self.rate_limit_ts = 0.0
        self.rate_limit_retry = 0
        self.rate_limit_msg = ""
        self.rate_limit_reset_str = ""
        self.model_name = ""
        # Agregados do evento 'result' (fim do turno) — cobrem todos os requests à API
        # feitos durante o turno, ao contrário de input_tokens/output_tokens acima, que
        # são sobrescritos a cada message_start/delta e refletem só o último request
        # (um turno com N tool calls faz N+1 requests).
        self.result_cost = None
        self.result_in_tok = None
        self.result_out_tok = None

    # -- spinner ----------------------------------------------------------

    def _show_status(self):
        if self.client._streaming_text:
            return
        elapsed = time.time() - self.start_time
        tok     = self.input_tokens + self.output_tokens
        flame   = _F[self.fire_frame % len(_F)]
        glow    = _GLOW[self.fire_frame % len(_GLOW)]
        self.fire_frame += 1
        tok_part = f"· {_fmt_tok(tok)} tok " if tok else ""
        if not self.spinner_shown:
            sys.stdout.write('\n')
            self.spinner_shown = True
        if self.rate_limited:
            waiting = time.time() - self.rate_limit_ts
            if self.rate_limit_retry:
                remaining  = max(0, self.rate_limit_retry - waiting)
                reset_info = f"{self.rate_limit_reset_str}  {DIM}({remaining:.0f}s){RESET}" if self.rate_limit_reset_str else f"{remaining:.0f}s"
                wait_part  = f"retoma às {reset_info}"
            else:
                wait_part = f"aguardando {waiting:.0f}s"
            sys.stdout.write(f"\r\033[2K{YELLOW}⏳{RESET}  {YELLOW}rate limit{RESET}  {wait_part}")
        else:
            sys.stdout.write(
                f"\r\033[2K{flame}{RESET}  {glow}pensando…{RESET}  {DIM}{elapsed:.0f}s {tok_part}{RESET}"
            )
        sys.stdout.flush()

    def _clear_status(self):
        # Só apaga se o spinner está de fato visível. `_clear_status` é chamado
        # antes de CADA chunk de stdout (render._dispatch); com spinner já
        # apagado, um `\r\033[2K` aqui limparia a linha de resposta já digitada
        # pelo chunk anterior — daí texto "some" enquanto o typewriter avança.
        if self.spinner_shown:
            sys.stdout.write("\r\033[2K\033[1A\r\033[2K")
            self.spinner_shown = False
            sys.stdout.flush()

    # -- dispatch -----------------------------------------------------------

    def handle_line(self, raw):
        raw = raw.strip()
        if not raw:
            return
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            # stderr é um canal separado (_stderr_reader em run_turn) — uma linha
            # não-JSON no stdout é anomalia, não caminho esperado.
            with open(RUNDIR / "stdout-anomalies.log", "a") as _an:
                _an.write(f"non-json: {raw}\n")
            return
        self.handle_event(event)

    def handle_event(self, event):
        etype = event.get("type")

        if etype == "stream_event":
            self._handle_stream_event(event.get("event", {}))
        elif etype == "assistant":
            self._handle_assistant(event)
        elif etype == "user":
            self._handle_user(event)
        elif etype == "control_request":
            self.render.suspend()
            try:
                _handle_control_request(event, self.proc, self.client.cwd)
            finally:
                self.render.resume()
        elif etype == "permission_request":
            self.render.suspend()
            try:
                approved = _handle_permission(event, self.proc)
                if not approved:
                    _write_stdin(self.proc, "n\n")
            finally:
                self.render.resume()
        elif etype == "result":
            self._handle_result(event)
        elif etype == "rate_limit_event":
            self._handle_rate_limit_event(event)
        elif etype not in ("system", None):
            with open(RUNDIR / "events.log", "a") as f:
                f.write(json.dumps(event) + "\n")

    # -- stream_event ---------------------------------------------------

    def _handle_stream_event(self, e):
        et = e.get("type")

        if et == "message_start":
            self.rate_limited = False
            msg   = e.get("message", {})
            usage = msg.get("usage", {})
            self.input_tokens          = usage.get("input_tokens", 0)
            self.cache_read_tokens     = usage.get("cache_read_input_tokens", 0)
            self.cache_creation_tokens = usage.get("cache_creation_input_tokens", 0)
            self.model_name = msg.get("model", self.model_name)
            if self.model_name:
                self.client.observed_model = self.model_name

        elif et == "message_delta":
            usage = e.get("usage", {})
            self.output_tokens = usage.get("output_tokens", 0)
            details = usage.get("output_tokens_details") or {}
            thinking_tokens = details.get("thinking_tokens")
            if isinstance(thinking_tokens, int):
                self.thinking_tokens_seen = max(self.thinking_tokens_seen, thinking_tokens)

        elif et == "content_block_start":
            block = e.get("content_block", {})
            self.current_block = block.get("type")
            if self.current_block == "thinking":
                # Header e resize são preguiçosos: só ocorrem quando chega texto
                # de thinking de fato (ver content_block_delta). Alguns requests
                # chegam só com signature_delta; a nota fica deferida para o fim do
                # turno, para um bloco vazio não apagar thinking visível anterior.
                self.thinking_header_written = False
            elif self.current_block == "text":
                if self.thinking_visible and self.thinking_autoresize:
                    _resize_thinking(self.client.tmux_srv, "summary")
                # Seta o flag antes de enfileirar — o render thread só desenha o
                # spinner quando isso é False, então essa ordem evita que ele
                # tente desenhar bem no instante em que o texto começa a sair.
                self.client._streaming_text = True
                self.text_started = True
                prefix = ("\n" if self.text_block_count > 0 else "") + CHAT_FG
                self.text_block_count += 1
                self.render.push_stdout(prefix, delay=0)

        elif et == "content_block_delta":
            self._handle_content_block_delta(e.get("delta", {}))

        elif et == "content_block_stop":
            self._handle_content_block_stop()

    def _handle_content_block_delta(self, delta):
        dtype = delta.get("type")

        # Texto do thinking: Sonnet manda thinking_delta; outros modelos podem
        # mandar como text_delta dentro do bloco thinking. Opus 4.8 redige o
        # thinking (só signature_delta) — chunk_t fica vazio e nada é exibido.
        if dtype == "thinking_delta" or (dtype == "text_delta" and self.current_block == "thinking"):
            chunk_t = delta.get("thinking") or delta.get("text") or ""

            if chunk_t:
                self.thinking_visible = True
                if not self.thinking_header_written:
                    _log(THINKING_LOG, f"{CLEAR}{THINKING_TS}[{_ts()}]{RESET}\n\033[40m{THINKING_FG}")
                    if self.thinking_autoresize:
                        _resize_thinking(self.client.tmux_srv, self._max_think_lines)
                        self.thinking_lines = self._max_think_lines
                    self.thinking_header_written = True

                def _on_newline():
                    self.thinking_count += 1
                    self.thinking_col    = 0

                for ch in chunk_t:
                    if ch == "\n":
                        self.thinking_col = 0
                    else:
                        self.thinking_col += 1
                        if self.thinking_col % 80 == 0:
                            _on_newline()

                self.render.push_file(THINKING_LOG, chunk_t, delay=0.001, on_newline=_on_newline, hesitate=False)

        elif dtype == "text_delta":
            chunk = delta.get("text", "")
            self.client._last_response_text += chunk
            if self._suppress_perm_text:
                pass
            else:
                rendered = self.md_buf.feed(chunk)
                if rendered:
                    delay = 0.015 if self.cfg.get("typewriter", True) else 0
                    self.render.push_stdout(CHAT_FG + rendered + RESET, delay=delay)

    def _handle_content_block_stop(self):
        if self.current_block == "thinking":
            # Flush: sem isso, o fechamento (RESET/nota) escreveria no arquivo
            # antes do render thread terminar de animar o último chunk enfileirado.
            self.render.join()
            if self.thinking_header_written:
                _log(THINKING_LOG, f"{RESET}\n")
            else:
                self._empty_thinking_blocks += 1
        elif self.current_block == "text":
            remainder = self.md_buf.flush()
            text = CHAT_FG + remainder + RESET if remainder else RESET
            delay = 0.015 if (remainder and self.cfg.get("typewriter", True)) else 0
            self.render.push_stdout(text, delay=delay)
            # Flush antes de ler col_is_mid_line() — senão a coluna refletiria
            # o estado de antes desse texto ainda ser escrito de fato.
            self.render.join()
            self.client._streaming_text = False
            if col_is_mid_line():
                self.render.push_stdout("\n", delay=0)
                self.render.join()
        self.current_block = None

    # -- assistant / user -------------------------------------------------

    def _handle_assistant(self, event):
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                log_tool_call(block, self.client.nvim_pane, self.client.tmux_srv, self.client.editor_bin, render=self.render)
                self._tool_names[block.get("id", "")] = block.get("name", "?")

    def _handle_user(self, event):
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_result":
                if block.get("is_error"):
                    content  = block.get("content", "")
                    err_text = (
                        next((i.get("text", "") for i in content if i.get("type") == "text"), "")
                        if isinstance(content, list) else str(content)
                    )
                    if ("requested permissions" in err_text or "haven't granted" in err_text
                            or "requires approval" in err_text):
                        tool_name = self._tool_names.get(block.get("tool_use_id", ""), "?")
                        self.render.suspend()
                        try:
                            if _handle_permission_ask(tool_name, self.client.cwd):
                                self.perm_approved = True
                                self.client._retry_context = f"({tool_name} aprovado — prossiga)"
                        finally:
                            self.render.resume()
                        self._suppress_perm_text = True
                log_tool_result(block)
                _blk_tool = self._tool_names.get(block.get("tool_use_id", ""), "")
                if _blk_tool == "Bash" and not block.get("is_error"):
                    _blk_content = block.get("content", "")
                    _blk_text    = (
                        next((i.get("text", "") for i in _blk_content if i.get("type") == "text"), "")
                        if isinstance(_blk_content, list) else str(_blk_content)
                    )
                    if re.search(r'exit code[:\s]+([1-9]\d*)', _blk_text, re.IGNORECASE):
                        if self.cfg.get("gargoyles", True):
                            _g_prefix, _g_fala = _gargula_comment("bash_error", force=True)
                            if _g_prefix:
                                _log(TOOLS_LOG, '\n')
                                _log(TOOLS_LOG, _g_prefix)
                                self.render.push_file(TOOLS_LOG, _g_fala)
                                self.render.join()
                                _log(TOOLS_LOG, '\n')

    # -- result / rate limit -------------------------------------------------

    def _handle_result(self, event):
        sid = event.get("session_id")
        if sid:
            self.client.session_id = sid
        if "total_cost_usd" in event:
            self.result_cost = event["total_cost_usd"]
        _r_usage = event.get("usage")
        if _r_usage:
            self.result_in_tok  = _r_usage.get("input_tokens")
            self.result_out_tok = _r_usage.get("output_tokens")
        self._emit_deferred_thinking_notice()
        # 'result' delimita o fim do turno no protocolo -- em processo persistente
        # (Fase 4) o processo continua vivo e não fecha stdout sozinho depois disso,
        # então o loop de ingestão em run_turn não pode esperar EOF.
        self.turn_done = True

    def _emit_deferred_thinking_notice(self):
        if self._thinking_notice_written or self.thinking_visible or not self._empty_thinking_blocks:
            return
        if self.thinking_tokens_seen == 0:
            note = "— sem thinking visível nesse turno (thinking_tokens=0)"
        else:
            note = "— o modelo omitiu o thinking (display:omitted)"
        _log(THINKING_LOG, f"{CLEAR}{THINKING_TS}[{_ts()}]{RESET}  {DIM}{note}{RESET}\n")
        self._thinking_notice_written = True

    def _handle_rate_limit_event(self, event):
        self.rate_limited  = True
        self.rate_limit_ts = time.time()
        with open(RUNDIR / "rate-limit.log", "a") as _rl:
            _rl.write(json.dumps(event) + "\n")
        self.rate_limit_retry = event.get("retryAfter", event.get("retry_after", 0))
        if self.rate_limit_retry:
            reset_dt = datetime.fromtimestamp(self.rate_limit_ts + self.rate_limit_retry)
            today    = datetime.now().date()
            self.rate_limit_reset_str = reset_dt.strftime("%H:%M:%S" if reset_dt.date() == today else "%d/%m %H:%M:%S")
