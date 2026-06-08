import json
import os
import re
import select
import subprocess
import sys
import termios
import time
from datetime import datetime, timedelta

from ..theme import (
    DIM, RESET, YELLOW,
    _F, _GLOW, MdBuffer,
    CHAT_FG, THINKING_FG, THINKING_TS,
    CLEAR,
)
from ..tools import log_tool_call, log_tool_result, TOOLS_LOG, _log, _ts
from ..typewriter import log_animated, SKIP_FLAG
from ..gargulas import _gargula_comment

from .text import _typewrite, reset_col, col_is_mid_line
from .panes import (
    THINKING_LOG, THINKING_PANE, STATS_PANE, TOOLS_PANE,
    _window_height, _resize_thinking,
)
from .permissions import _handle_permission, _handle_permission_ask, _handle_control_request
from .stats import _model_price, _fmt_cost

from ..tools import RUNDIR
from .. import config


def run_turn(client, message, images=None):
    """Executa um turno completo: subprocess claude, loop de eventos, spinner."""
    reset_col()
    client._last_response_text = ""
    has_images = bool(images)
    clean_text = message.replace('[img]', '').strip() if has_images else message
    cmd = [
        "claude", "--print",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
    ]
    if has_images:
        cmd += ["--input-format", "stream-json"]
    else:
        cmd += ["-p", message]
    if client.mode.value == "auto":
        cmd.append("--dangerously-skip-permissions")
    if getattr(client, "model", None):
        cmd += ["--model", client.model]
    if client.first_turn and client.resume_id is not None:
        if client.resume_id:
            cmd += ["--resume", client.resume_id]
        else:
            cmd.append("--continue")
    elif not client.first_turn:
        cmd.append("--continue")

    try:
        client.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stdin=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
            cwd=client.cwd,
        )
    except FileNotFoundError:
        sys.stdout.write(
            f"\n{YELLOW}claude CLI não encontrado.{RESET}"
            f" {DIM}Instale com: npm i -g @anthropic-ai/claude-code{RESET}\n"
        )
        sys.stdout.flush()
        return False
    proc = client.proc

    if has_images:
        content = [
            {'type': 'image', 'source': {'type': 'base64', 'media_type': img['media_type'], 'data': img['data']}}
            for img in images
        ]
        if clean_text:
            content.append({'type': 'text', 'text': clean_text})
        proc.stdin.write(json.dumps({'type': 'user', 'message': {'role': 'user', 'content': content}}) + '\n')
        proc.stdin.flush()
        proc.stdin.close()

    try:
        SKIP_FLAG.unlink()
    except FileNotFoundError:
        pass

    # Desabilita echo durante o turno — evita que teclas apareçam no meio do typewriter.
    _fd = sys.stdin.fileno()
    _old_term = termios.tcgetattr(_fd)
    _no_echo = list(_old_term)
    _no_echo[3] &= ~termios.ECHO
    termios.tcsetattr(_fd, termios.TCSADRAIN, _no_echo)

    cfg = config.load()
    thinking_autoresize = cfg.get("thinking_autoresize", True)

    _tool_names      = {}
    start_time       = time.time()
    input_tokens     = 0
    output_tokens    = 0
    current_block    = None
    text_started     = False
    client._streaming_text = False
    text_block_count = 0
    fire_frame       = 0
    in_code_block    = False
    md_buf           = MdBuffer()
    _rows            = _window_height(client.tmux_srv)
    _idle_lines      = max(8,  int(_rows * 0.16))
    _max_think_lines = max(12, _rows - int(_rows * 0.26) - max(2, int(_rows * 0.08)) - 6)
    thinking_lines   = _idle_lines
    thinking_count   = 0
    thinking_col     = 0
    thinking_header_written = False
    spinner_shown    = False
    _suppress_perm_text = False
    _perm_approved   = False
    rate_limited     = False
    rate_limit_ts    = 0.0
    rate_limit_retry = 0
    rate_limit_msg   = ""
    rate_limit_reset_str = ""
    model_name       = ""

    def _fmt_tok(n):
        return f"{n/1000:.1f}k" if n >= 1000 else str(n)

    def _show_status():
        nonlocal fire_frame, spinner_shown
        if client._streaming_text:
            return
        elapsed = time.time() - start_time
        tok     = input_tokens + output_tokens
        flame   = _F[fire_frame % len(_F)]
        glow    = _GLOW[fire_frame % len(_GLOW)]
        fire_frame += 1
        tok_part = f"· {_fmt_tok(tok)} tok " if tok else ""
        if not spinner_shown:
            sys.stdout.write('\n')
            spinner_shown = True
        if rate_limited:
            waiting = time.time() - rate_limit_ts
            if rate_limit_retry:
                remaining  = max(0, rate_limit_retry - waiting)
                reset_info = f"{rate_limit_reset_str}  {DIM}({remaining:.0f}s){RESET}" if rate_limit_reset_str else f"{remaining:.0f}s"
                wait_part  = f"retoma às {reset_info}"
            else:
                wait_part = f"aguardando {waiting:.0f}s"
            sys.stdout.write(f"\r\033[2K{YELLOW}⏳{RESET}  {YELLOW}rate limit{RESET}  {wait_part}")
        else:
            sys.stdout.write(
                f"\r\033[2K{flame}{RESET}  {glow}pensando…{RESET}  {DIM}{elapsed:.0f}s {tok_part}{RESET}"
            )
        sys.stdout.flush()

    def _clear_status():
        nonlocal spinner_shown
        if spinner_shown:
            sys.stdout.write("\r\033[2K\033[1A\r\033[2K")
        else:
            sys.stdout.write("\r\033[2K")
        spinner_shown = False
        sys.stdout.flush()

    while True:
        ready, _, _ = select.select([proc.stdout], [], [], 0.15)
        if not ready:
            _show_status()
            continue
        raw = proc.stdout.readline()
        if not raw:
            break
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            with open("/tmp/claude-rate-limit.log", "a") as _rl:
                _rl.write(f"non-json: {raw}\n")
            _rl_match = re.search(r'resets?\s+(\d{1,2}:\d{2}(?:am|pm))', raw, re.IGNORECASE)
            if _rl_match or "hit your limit" in raw.lower():
                if not rate_limited:
                    rate_limited = True
                    rate_limit_ts = time.time()
                if _rl_match and not rate_limit_reset_str:
                    try:
                        _t    = datetime.strptime(_rl_match.group(1).lower(), "%I:%M%p")
                        _now  = datetime.now()
                        _reset = _now.replace(hour=_t.hour, minute=_t.minute, second=0, microsecond=0)
                        if _reset <= _now:
                            _reset += timedelta(days=1)
                        rate_limit_reset_str = _reset.strftime("%H:%M" if _reset.date() == _now.date() else "%d/%m %H:%M")
                    except ValueError:
                        pass
            elif rate_limited:
                rate_limit_msg = raw
            continue

        etype = event.get("type")

        if etype == "stream_event":
            e  = event.get("event", {})
            et = e.get("type")

            if et == "message_start":
                rate_limited = False
                msg          = e.get("message", {})
                input_tokens = msg.get("usage", {}).get("input_tokens", 0)
                model_name   = msg.get("model", model_name)
                if model_name:
                    client.observed_model = model_name
                _show_status()

            elif et == "message_delta":
                output_tokens = e.get("usage", {}).get("output_tokens", 0)

            elif et == "content_block_start":
                block         = e.get("content_block", {})
                current_block = block.get("type")
                if current_block == "thinking":
                    # Header e resize são preguiçosos: só ocorrem quando chega texto
                    # de thinking de fato (ver content_block_delta). Opus 4.8 redige o
                    # thinking — só signature_delta, texto vazio — e nesse caso o pane
                    # não cresce nem polui com timestamp à toa.
                    thinking_header_written = False
                elif current_block == "text":
                    _clear_status()
                    if thinking_header_written and thinking_autoresize:
                        _resize_thinking(client.tmux_srv, "summary")
                    if text_block_count > 0:
                        sys.stdout.write("\n")
                    sys.stdout.flush()
                    text_block_count += 1
                    text_started = True
                    client._streaming_text = True
                    sys.stdout.write(CHAT_FG)
                    sys.stdout.flush()

            elif et == "content_block_delta":
                delta = e.get("delta", {})
                dtype = delta.get("type")

                # Texto do thinking: Sonnet manda thinking_delta; outros modelos podem
                # mandar como text_delta dentro do bloco thinking. Opus 4.8 redige o
                # thinking (só signature_delta) — chunk_t fica vazio e nada é exibido.
                if dtype == "thinking_delta" or (dtype == "text_delta" and current_block == "thinking"):
                    chunk_t = delta.get("thinking") or delta.get("text") or ""

                    if chunk_t:
                        if not thinking_header_written:
                            _log(THINKING_LOG, f"{CLEAR}{THINKING_TS}[{_ts()}]{RESET}\n\033[40m{THINKING_FG}")
                            if thinking_autoresize:
                                _resize_thinking(client.tmux_srv, _max_think_lines)
                                thinking_lines = _max_think_lines
                            thinking_header_written = True

                        def _on_newline():
                            nonlocal thinking_count, thinking_col
                            thinking_count += 1
                            thinking_col    = 0

                        for ch in chunk_t:
                            if ch == "\n":
                                thinking_col = 0
                            else:
                                thinking_col += 1
                                if thinking_col % 80 == 0:
                                    _on_newline()

                        log_animated(THINKING_LOG, chunk_t, delay=0.001, on_newline=_on_newline, hesitate=False)
                        _show_status()

                elif dtype == "text_delta":
                    chunk = delta.get("text", "")
                    client._last_response_text += chunk
                    if _suppress_perm_text:
                        pass
                    else:
                        if chunk.count("```") % 2 == 1:
                            remainder = md_buf.flush()
                            if remainder:
                                if cfg.get("typewriter", True):
                                    _typewrite(CHAT_FG + remainder + RESET)
                                else:
                                    sys.stdout.write(CHAT_FG + remainder + RESET)
                                    sys.stdout.flush()
                            in_code_block = not in_code_block
                        rendered = chunk if in_code_block else md_buf.feed(chunk)
                        if rendered:
                            if cfg.get("typewriter", True):
                                _typewrite(CHAT_FG + rendered + RESET)
                            else:
                                sys.stdout.write(CHAT_FG + rendered + RESET)
                                sys.stdout.flush()

            elif et == "content_block_stop":
                if current_block == "thinking":
                    if thinking_header_written:
                        _log(THINKING_LOG, f"{RESET}\n")
                    else:
                        # Modelo omitiu o thinking (Opus 4.8/4.7 usam display:"omitted":
                        # só signature, texto vazio). Mostra uma nota em vez de pane mudo.
                        _log(THINKING_LOG, f"{CLEAR}{THINKING_TS}[{_ts()}]{RESET}  {DIM}— o modelo omitiu o thinking (display:omitted){RESET}\n")
                elif current_block == "text":
                    client._streaming_text = False
                    remainder = md_buf.flush()
                    if remainder:
                        if cfg.get("typewriter", True):
                            _typewrite(CHAT_FG + remainder + RESET)
                        else:
                            sys.stdout.write(CHAT_FG + remainder + RESET)
                            sys.stdout.flush()
                    sys.stdout.write(RESET)
                    if col_is_mid_line():
                        sys.stdout.write("\n")
                        reset_col()
                    sys.stdout.flush()
                current_block = None

        elif etype == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    _show_status()
                    log_tool_call(block, client.nvim_pane, client.tmux_srv, client.editor_bin)
                    _tool_names[block.get("id", "")] = block.get("name", "?")

        elif etype == "user":
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
                            tool_name = _tool_names.get(block.get("tool_use_id", ""), "?")
                            _clear_status()
                            if _handle_permission_ask(tool_name, client.cwd):
                                _perm_approved = True
                                client._retry_context = f"({tool_name} aprovado — prossiga)"
                            _suppress_perm_text = True
                    log_tool_result(block)
                    _blk_tool = _tool_names.get(block.get("tool_use_id", ""), "")
                    if _blk_tool == "Bash" and not block.get("is_error"):
                        _blk_content = block.get("content", "")
                        _blk_text    = (
                            next((i.get("text", "") for i in _blk_content if i.get("type") == "text"), "")
                            if isinstance(_blk_content, list) else str(_blk_content)
                        )
                        if re.search(r'exit code[:\s]+([1-9]\d*)', _blk_text, re.IGNORECASE):
                            if cfg.get("gargoyles", True):
                                _g_prefix, _g_fala = _gargula_comment("bash_error", force=True)
                                if _g_prefix:
                                    _log(TOOLS_LOG, '\n')
                                    _log(TOOLS_LOG, _g_prefix)
                                    log_animated(TOOLS_LOG, _g_fala)
                                    _log(TOOLS_LOG, '\n')

        elif etype == "control_request":
            _clear_status()
            _handle_control_request(event, proc, client.cwd)

        elif etype == "permission_request":
            _clear_status()
            approved = _handle_permission(event, proc)
            if not approved:
                proc.stdin.write("n\n")
                proc.stdin.flush()

        elif etype == "result":
            sid = event.get("session_id")
            if sid:
                client.session_id = sid

        elif etype == "rate_limit_event":
            rate_limited  = True
            rate_limit_ts = time.time()
            with open("/tmp/claude-rate-limit.log", "a") as _rl:
                _rl.write(json.dumps(event) + "\n")
            rate_limit_retry = event.get("retryAfter", event.get("retry_after", 0))
            if rate_limit_retry:
                reset_dt = datetime.fromtimestamp(rate_limit_ts + rate_limit_retry)
                today    = datetime.now().date()
                rate_limit_reset_str = reset_dt.strftime("%H:%M:%S" if reset_dt.date() == today else "%d/%m %H:%M:%S")
            _show_status()

        elif etype not in ("system", None):
            with open("/tmp/claude-client-events.log", "a") as f:
                f.write(json.dumps(event) + "\n")

    if text_started and spinner_shown:
        sys.stdout.write("\n")
        sys.stdout.flush()
    _clear_status()
    if rate_limited and not text_started:
        msg = f"rate limit — quota retoma às {rate_limit_reset_str}" if rate_limit_reset_str \
              else rate_limit_msg or "rate limit atingido — tente novamente mais tarde"
        sys.stdout.write(f"\n{YELLOW}⏳  {msg}{RESET}\n")
        sys.stdout.flush()
        if cfg.get("gargoyles", True):
            _g_prefix, _g_fala = _gargula_comment("rate_limit", force=True)
            if _g_prefix:
                _typewrite(_g_prefix + _g_fala.rstrip('\n'), delay=0.025)
                sys.stdout.write("\n")
                sys.stdout.flush()

    elapsed = time.time() - start_time
    client._total_input_tokens  = getattr(client, '_total_input_tokens',  0) + input_tokens
    client._total_output_tokens = getattr(client, '_total_output_tokens', 0) + output_tokens
    client._total_elapsed       = getattr(client, '_total_elapsed',       0.0) + elapsed
    _in_price, _out_price = _model_price(model_name)
    _cost_turn  = input_tokens / 1e6 * _in_price + output_tokens / 1e6 * _out_price
    client._total_cost = getattr(client, '_total_cost', 0.0) + _cost_turn

    _stats_tty_file = RUNDIR / "stats_tty"
    _stats_tty = _stats_tty_file.read_text().strip() if _stats_tty_file.exists() else ""
    if _stats_tty:
        try:
            turn_line = (
                f"\r\033[2K{DIM}{_ts()}{RESET}  🔢  "
                f"{_fmt_tok(input_tokens)} in · {_fmt_tok(output_tokens)} out · "
                f"{elapsed:.1f}s · {_fmt_cost(_cost_turn)}"
            )
            total_line = (
                f"\r\033[2K{DIM}{'sessão':>8}{RESET}  ∑   "
                f"{_fmt_tok(client._total_input_tokens)} in · "
                f"{_fmt_tok(client._total_output_tokens)} out · "
                f"{client._total_elapsed:.0f}s · {_fmt_cost(client._total_cost)}"
            )
            content = "\033[H" + turn_line + "\n" + total_line
            _fd2 = os.open(_stats_tty, os.O_WRONLY | os.O_NOCTTY)
            os.write(_fd2, content.encode())
            os.close(_fd2)
        except OSError:
            pass

    proc.wait()
    if thinking_autoresize and thinking_lines > _idle_lines:
        _resize_thinking(client.tmux_srv, "idle")
    termios.tcsetattr(_fd, termios.TCSADRAIN, _old_term)
    client.first_turn = False
    return _perm_approved
