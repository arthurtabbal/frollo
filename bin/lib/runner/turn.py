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
from ..typewriter import log_animated
from ..gargulas import _gargula_comment

from .text import _typewrite, reset_col, col_is_mid_line
from .panes import THINKING_LOG, _resize_thinking
from .permissions import _handle_permission, _handle_permission_ask, _handle_control_request, _write_stdin
from .stats import _fmt_tok


class Turn:
    """Estado de um turno em andamento + dispatch de eventos do stream-json."""

    def __init__(self, client, proc, cfg, thinking_autoresize, max_think_lines, idle_lines):
        self.client = client
        self.proc = proc
        self.cfg = cfg
        self.thinking_autoresize = thinking_autoresize
        self._max_think_lines = max_think_lines
        self._idle_lines = idle_lines

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
        self.spinner_shown = False
        self._suppress_perm_text = False
        self.perm_approved = False
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
        if self.spinner_shown:
            sys.stdout.write("\r\033[2K\033[1A\r\033[2K")
        else:
            sys.stdout.write("\r\033[2K")
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
            self._clear_status()
            _handle_control_request(event, self.proc, self.client.cwd)
        elif etype == "permission_request":
            self._clear_status()
            approved = _handle_permission(event, self.proc)
            if not approved:
                _write_stdin(self.proc, "n\n")
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
            self._show_status()

        elif et == "message_delta":
            self.output_tokens = e.get("usage", {}).get("output_tokens", 0)

        elif et == "content_block_start":
            block = e.get("content_block", {})
            self.current_block = block.get("type")
            if self.current_block == "thinking":
                # Header e resize são preguiçosos: só ocorrem quando chega texto
                # de thinking de fato (ver content_block_delta). Opus 4.8 redige o
                # thinking — só signature_delta, texto vazio — e nesse caso o pane
                # não cresce nem polui com timestamp à toa.
                self.thinking_header_written = False
            elif self.current_block == "text":
                self._clear_status()
                if self.thinking_header_written and self.thinking_autoresize:
                    _resize_thinking(self.client.tmux_srv, "summary")
                if self.text_block_count > 0:
                    sys.stdout.write("\n")
                sys.stdout.flush()
                self.text_block_count += 1
                self.text_started = True
                self.client._streaming_text = True
                sys.stdout.write(CHAT_FG)
                sys.stdout.flush()

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

                log_animated(THINKING_LOG, chunk_t, delay=0.001, on_newline=_on_newline, hesitate=False)
                self._show_status()

        elif dtype == "text_delta":
            chunk = delta.get("text", "")
            self.client._last_response_text += chunk
            if self._suppress_perm_text:
                pass
            else:
                rendered = self.md_buf.feed(chunk)
                if rendered:
                    if self.cfg.get("typewriter", True):
                        _typewrite(CHAT_FG + rendered + RESET)
                    else:
                        sys.stdout.write(CHAT_FG + rendered + RESET)
                        sys.stdout.flush()

    def _handle_content_block_stop(self):
        if self.current_block == "thinking":
            if self.thinking_header_written:
                _log(THINKING_LOG, f"{RESET}\n")
            else:
                # Modelo omitiu o thinking (Opus 4.8/4.7 usam display:"omitted":
                # só signature, texto vazio). Mostra uma nota em vez de pane mudo.
                _log(THINKING_LOG, f"{CLEAR}{THINKING_TS}[{_ts()}]{RESET}  {DIM}— o modelo omitiu o thinking (display:omitted){RESET}\n")
        elif self.current_block == "text":
            self.client._streaming_text = False
            remainder = self.md_buf.flush()
            if remainder:
                if self.cfg.get("typewriter", True):
                    _typewrite(CHAT_FG + remainder + RESET)
                else:
                    sys.stdout.write(CHAT_FG + remainder + RESET)
                    sys.stdout.flush()
            sys.stdout.write(RESET)
            if col_is_mid_line():
                sys.stdout.write("\n")
                reset_col()
            sys.stdout.flush()
        self.current_block = None

    # -- assistant / user -------------------------------------------------

    def _handle_assistant(self, event):
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                self._show_status()
                log_tool_call(block, self.client.nvim_pane, self.client.tmux_srv, self.client.editor_bin)
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
                        self._clear_status()
                        if _handle_permission_ask(tool_name, self.client.cwd):
                            self.perm_approved = True
                            self.client._retry_context = f"({tool_name} aprovado — prossiga)"
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
                                log_animated(TOOLS_LOG, _g_fala)
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
        self._show_status()
