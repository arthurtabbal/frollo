import contextlib
import json
import os
import re
import select
import subprocess
import sys
import termios
import time
import tty
from datetime import datetime, timedelta
from pathlib import Path

from .theme import (
    DIM, RESET, YELLOW, WHITE,
    BG_PERM, BG_USER,
    _F, _GLOW, _md, MdBuffer,
    CHAT_FG, THINKING_FG, THINKING_TS,
    CLEAR,
)
from .tools import log_tool_call, log_tool_result, RUNDIR, TOOLS_LOG, _log, _clear_tools_pane, _ts
from .typewriter import log_animated, SKIP_FLAG, _char_delay

THINKING_LOG  = RUNDIR / "thinking"
THINKING_PANE = RUNDIR / "thinking_pane"
CHAT_PANE     = RUNDIR / "chat_pane"
TOOLS_PANE    = RUNDIR / "tools_pane"
STATS_PANE    = RUNDIR / "stats_pane"

_ANSI_SEQ = re.compile(r'(\033\[[0-9;]*[mKJH])')


@contextlib.contextmanager
def _raw_stdin():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _window_height(tmux_srv):
    """Altura real da janela tmux; fallback para terminal size do processo atual."""
    if tmux_srv:
        try:
            r = subprocess.run(
                ["tmux", "-L", tmux_srv, "display-message", "-p", "#{window_height}"],
                capture_output=True, text=True,
            )
            return int(r.stdout.strip())
        except Exception:
            pass
    try:
        return os.get_terminal_size().lines
    except OSError:
        return 50


def _pane_resize(tmux_srv, pane_file, lines):
    try:
        pane_id = pane_file.read_text().strip()
    except OSError:
        return
    if not pane_id:
        return
    try:
        subprocess.run(
            ["tmux", "-L", tmux_srv, "resize-pane", "-t", pane_id, "-y", str(lines)],
            capture_output=True,
        )
    except Exception:
        pass


def _resize_thinking(tmux_srv, size):
    """Redimensiona o pane thinking. size: 'idle'|'summary' ou int linhas."""
    if not tmux_srv or not THINKING_PANE.exists():
        return
    rows       = _window_height(tmux_srv)
    tools_lines = max(6, int(rows * 0.26))
    stats_lines = max(2, int(rows * 0.08))
    if isinstance(size, int):
        lines = size
        # durante crescimento: pina stats no tamanho natural, chat absorve
        _pane_resize(tmux_srv, STATS_PANE, stats_lines)
        _pane_resize(tmux_srv, THINKING_PANE, lines)
    else:
        lines = {"idle": max(8, int(rows * 0.16)), "summary": max(5, int(rows * 0.10))}[size]
        # resize de baixo pra cima: tools e stats fixos, thinking encolhe/cresce,
        # tmux distribui o restante pro chat automaticamente
        _pane_resize(tmux_srv, TOOLS_PANE, tools_lines)
        _pane_resize(tmux_srv, STATS_PANE, stats_lines)
        _pane_resize(tmux_srv, THINKING_PANE, lines)
_col = [0]  # coluna atual no terminal — persiste entre chunks do mesmo bloco de texto



def _wrap_text(text, width):
    """Insere quebras de linha em fronteiras de palavras. Atualiza _col como side effect."""
    result = []
    col = _col[0]
    for token in re.split(r'(\s+)', text):
        if not token:
            continue
        if '\n' in token:
            result.append(token)
            col = len(token) - token.rfind('\n') - 1
        elif token.isspace():
            if col + len(token) > width:
                result.append('\n')
                col = 0
            else:
                result.append(token)
                col += len(token)
        else:
            if col > 0 and col + len(token) > width:
                result.append('\n')
                col = 0
            result.append(token)
            col += len(token)
    _col[0] = col
    return ''.join(result)


def _typewrite(text, delay=0.015, wrap=True):
    try:
        width = os.get_terminal_size().columns - 1
    except OSError:
        width = 89
    parts = _ANSI_SEQ.split(text)
    for i, part in enumerate(parts):
        if _ANSI_SEQ.match(part):
            sys.stdout.write(part)
            sys.stdout.flush()
        else:
            body = _wrap_text(part, width) if wrap else part
            final_col = _col[0]  # _wrap_text already computed the correct final column
            for j, char in enumerate(body):
                if char == '\n':
                    _col[0] = 0
                sys.stdout.write(char)
                sys.stdout.flush()
                ready, _, _ = select.select([sys.stdin], [], [], _char_delay(char, delay))
                if ready:
                    sys.stdin.readline()
                    sys.stdout.write(body[j+1:])
                    sys.stdout.write(''.join(parts[i+1:]))
                    sys.stdout.flush()
                    _col[0] = final_col
                    return
            _col[0] = final_col  # \n no meio do body zera _col; restaura o valor correto




def run_turn(client, message, images=None):
    """Executa um turno completo: subprocess claude, loop de eventos, spinner."""
    _col[0] = 0
    client._last_response_text = ""
    # clear nos panes de thinking, tools e stats
    _clear_tools_pane()
    THINKING_LOG.write_text("")
    for _tty_name in ("thinking_tty", "stats_tty"):
        _tty_file = RUNDIR / _tty_name
        _tty = _tty_file.read_text().strip() if _tty_file.exists() else ""
        if _tty:
            try:
                _fd = os.open(_tty, os.O_WRONLY | os.O_NOCTTY)
                os.write(_fd, CLEAR.encode())
                os.close(_fd)
            except OSError:
                pass
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
    if client.first_turn and client.resume_id is not None:
        if client.resume_id:
            cmd += ["--resume", client.resume_id]
        else:
            cmd.append("--continue")
    elif not client.first_turn:
        cmd.append("--continue")

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

    # limpa flag de skip de turno anterior
    try:
        SKIP_FLAG.unlink()
    except FileNotFoundError:
        pass

    # desabilita echo durante o turno — evita que teclas pressionadas
    # apareçam no meio do typewriter
    _fd = sys.stdin.fileno()
    _old_term = termios.tcgetattr(_fd)
    _no_echo = list(_old_term)
    _no_echo[3] &= ~termios.ECHO
    termios.tcsetattr(_fd, termios.TCSADRAIN, _no_echo)

    _tool_names = {}   # tool_use_id → name, para resolver nomes em erros de permissão
    _retry_needed = [False]
    start_time = time.time()
    input_tokens = 0
    output_tokens = 0
    current_block = None
    text_started = False
    client._streaming_text = False
    text_block_count = [0]
    fire_frame = [0]
    in_code_block = [False]
    md_buf = MdBuffer()
    _rows = _window_height(client.tmux_srv)
    _idle_lines     = max(8,  int(_rows * 0.16))
    _max_think_lines = max(12, _rows - int(_rows * 0.26) - max(2, int(_rows * 0.08)) - 6)
    thinking_lines  = [_idle_lines]   # tamanho atual do pane (rastreado localmente)
    thinking_count  = [0]             # newlines acumulados no bloco thinking
    _resize_thinking(client.tmux_srv, "idle")
    spinner_shown = [False]
    rate_limited = [False]
    rate_limit_ts = [0.0]
    rate_limit_retry = [0]
    rate_limit_msg = [""]
    rate_limit_reset_str = [""]

    def _fmt_tok(n):
        return f"{n/1000:.1f}k" if n >= 1000 else str(n)

    def _show_status():
        if client._streaming_text:
            return
        elapsed = time.time() - start_time
        tok = input_tokens + output_tokens
        flame = _F[fire_frame[0] % len(_F)]
        glow  = _GLOW[fire_frame[0] % len(_GLOW)]
        fire_frame[0] += 1
        tok_part = f"· {_fmt_tok(tok)} tok " if tok else ""
        if not spinner_shown[0]:
            sys.stdout.write('\n')
            spinner_shown[0] = True
        if rate_limited[0]:
            waiting = time.time() - rate_limit_ts[0]
            if rate_limit_retry[0]:
                remaining = max(0, rate_limit_retry[0] - waiting)
                reset_info = f"{rate_limit_reset_str[0]}  {DIM}({remaining:.0f}s){RESET}" if rate_limit_reset_str[0] else f"{remaining:.0f}s"
                wait_part = f"retoma às {reset_info}"
            else:
                wait_part = f"aguardando {waiting:.0f}s"
            sys.stdout.write(
                f"\r\033[2K{YELLOW}⏳{RESET}  {YELLOW}rate limit{RESET}  {wait_part}"
            )
        else:
            sys.stdout.write(
                f"\r\033[2K{flame}{RESET}  {glow}pensando…{RESET}  {DIM}{elapsed:.0f}s {tok_part}{RESET}"
            )
        sys.stdout.flush()

    def _clear_status():
        if spinner_shown[0]:
            sys.stdout.write("\r\033[2K\033[1A\r\033[2K")  # limpa spinner + linha em branco acima
        else:
            sys.stdout.write("\r\033[2K")
        spinner_shown[0] = False
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
                if not rate_limited[0]:
                    rate_limited[0] = True
                    rate_limit_ts[0] = time.time()
                if _rl_match and not rate_limit_reset_str[0]:
                    try:
                        _t = datetime.strptime(_rl_match.group(1).lower(), "%I:%M%p")
                        _now = datetime.now()
                        _reset = _now.replace(hour=_t.hour, minute=_t.minute, second=0, microsecond=0)
                        if _reset <= _now:
                            _reset += timedelta(days=1)
                        if _reset.date() == _now.date():
                            rate_limit_reset_str[0] = _reset.strftime("%H:%M")
                        else:
                            rate_limit_reset_str[0] = _reset.strftime("%d/%m %H:%M")
                    except ValueError:
                        pass
            elif rate_limited[0]:
                rate_limit_msg[0] = raw
            continue

        etype = event.get("type")

        if etype == "stream_event":
            e = event.get("event", {})
            et = e.get("type")

            if et == "message_start":
                rate_limited[0] = False
                input_tokens = e.get("message", {}).get("usage", {}).get("input_tokens", 0)
                _show_status()

            elif et == "message_delta":
                output_tokens = e.get("usage", {}).get("output_tokens", 0)

            elif et == "content_block_start":
                block = e.get("content_block", {})
                current_block = block.get("type")
                if current_block == "thinking":
                    _log(THINKING_LOG, f"{CLEAR}{THINKING_TS}[{_ts()}]{RESET}\n\033[40m{THINKING_FG}")
                elif current_block == "text":
                    _clear_status()
                    if thinking_count[0] > 0:
                        _resize_thinking(client.tmux_srv, "summary")
                    if text_block_count[0] > 0:
                        sys.stdout.write("\n")
                    sys.stdout.flush()
                    text_block_count[0] += 1
                    text_started = True
                    client._streaming_text = True
                    sys.stdout.write(CHAT_FG)
                    sys.stdout.flush()

            elif et == "content_block_delta":
                delta = e.get("delta", {})
                dtype = delta.get("type")

                if dtype == "thinking_delta":
                    chunk_t = delta.get("thinking", "")

                    def _on_newline():
                        thinking_count[0] += 1
                        desired = min(thinking_count[0] + 3, _max_think_lines)
                        if desired > thinking_lines[0]:
                            _resize_thinking(client.tmux_srv, desired)
                            thinking_lines[0] = desired

                    log_animated(THINKING_LOG, chunk_t, delay=0.001, on_newline=_on_newline, hesitate=False)
                    _show_status()

                elif dtype == "text_delta":
                    chunk = delta.get("text", "")
                    client._last_response_text += chunk
                    if chunk.count("```") % 2 == 1:
                        in_code_block[0] = not in_code_block[0]
                    rendered = chunk if in_code_block[0] else md_buf.feed(chunk)
                    if rendered:
                        _typewrite(CHAT_FG + rendered + RESET)

            elif et == "content_block_stop":
                if current_block == "thinking":
                    _log(THINKING_LOG, f"{RESET}\n")
                elif current_block == "text":
                    client._streaming_text = False
                    remainder = md_buf.flush()
                    if remainder:
                        _typewrite(CHAT_FG + remainder + RESET)
                    sys.stdout.write(RESET)
                    if _col[0] != 0:
                        sys.stdout.write("\n")
                        _col[0] = 0
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
                        content = block.get("content", "")
                        err_text = (
                            next((i.get("text", "") for i in content if i.get("type") == "text"), "")
                            if isinstance(content, list) else str(content)
                        )
                        if ("requested permissions" in err_text or "haven't granted" in err_text
                            or "requires approval" in err_text):
                            tool_name = _tool_names.get(block.get("tool_use_id", ""), "?")
                            _clear_status()
                            _handle_permission_ask(tool_name, client.cwd, _retry_needed)
                    log_tool_result(block)

        elif etype == "permission_request":
            approved = _handle_permission(event, proc)
            if not approved:
                proc.stdin.write("n\n")
                proc.stdin.flush()

        elif etype == "result":
            sid = event.get("session_id")
            if sid:
                client.session_id = sid

        elif etype == "rate_limit_event":
            rate_limited[0] = True
            rate_limit_ts[0] = time.time()
            with open("/tmp/claude-rate-limit.log", "a") as _rl:
                _rl.write(json.dumps(event) + "\n")
            rate_limit_retry[0] = event.get("retryAfter", event.get("retry_after", 0))
            if rate_limit_retry[0]:
                reset_dt = datetime.fromtimestamp(rate_limit_ts[0] + rate_limit_retry[0])
                today = datetime.now().date()
                if reset_dt.date() == today:
                    rate_limit_reset_str[0] = reset_dt.strftime("%H:%M:%S")
                else:
                    rate_limit_reset_str[0] = reset_dt.strftime("%d/%m %H:%M:%S")
            _show_status()

        elif etype not in ("system", None):
            with open("/tmp/claude-client-events.log", "a") as f:
                f.write(json.dumps(event) + "\n")

    if text_started and spinner_shown[0]:
        sys.stdout.write("\n")
        sys.stdout.flush()
    _clear_status()
    if rate_limited[0] and not text_started:
        if rate_limit_reset_str[0]:
            msg = f"rate limit — quota retoma às {rate_limit_reset_str[0]}"
        else:
            msg = rate_limit_msg[0] or "rate limit atingido — tente novamente mais tarde"
        sys.stdout.write(f"\n{YELLOW}⏳  {msg}{RESET}\n")
        sys.stdout.flush()
    elapsed = time.time() - start_time
    client._total_input_tokens  = getattr(client, '_total_input_tokens',  0) + input_tokens
    client._total_output_tokens = getattr(client, '_total_output_tokens', 0) + output_tokens
    client._total_elapsed       = getattr(client, '_total_elapsed',       0.0) + elapsed
    _stats_tty_file = RUNDIR / "stats_tty"
    _stats_tty = _stats_tty_file.read_text().strip() if _stats_tty_file.exists() else ""
    if _stats_tty:
        try:
            turn_line = (
                f"\r\033[2K{DIM}{_ts()}{RESET}  🔢  "
                f"{_fmt_tok(input_tokens)} in · {_fmt_tok(output_tokens)} out · "
                f"{elapsed:.1f}s"
            )
            total_line = (
                f"\r\033[2K{DIM}{'sessão':>8}{RESET}  ∑   "
                f"{_fmt_tok(client._total_input_tokens)} in · "
                f"{_fmt_tok(client._total_output_tokens)} out · "
                f"{client._total_elapsed:.0f}s"
            )
            content = turn_line + "\n" + total_line + "\033[1A\r"
            _fd2 = os.open(_stats_tty, os.O_WRONLY | os.O_NOCTTY)
            os.write(_fd2, content.encode())
            os.close(_fd2)
        except OSError:
            pass
    proc.wait()
    _resize_thinking(client.tmux_srv, "idle")
    termios.tcsetattr(_fd, termios.TCSADRAIN, _old_term)
    client.first_turn = False
    return _retry_needed[0]


def _handle_permission_ask(tool_name, cwd, retry_flag):
    """Trata o caso de permissions.ask: tool falhou por falta de aprovação no projeto."""
    settings_path = Path(cwd) / ".claude" / "settings.local.json"

    sys.stdout.write(f"\n{BG_PERM}{WHITE}  permissão bloqueada  {RESET}  {YELLOW}{tool_name}{RESET}\n")
    sys.stdout.write(f"{DIM}O projeto requer aprovação explícita para {tool_name}.\n")
    sys.stdout.write(f"Adicionar {tool_name} ao allow do projeto (.claude/settings.local.json)? [y/n]{RESET}  ")
    sys.stdout.flush()

    with _raw_stdin():
        ch = os.read(sys.stdin.fileno(), 1).decode('utf-8', errors='replace').lower()

    if ch == 'y':
        sys.stdout.write(f"y\n")
        sys.stdout.flush()
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
            allow = settings.setdefault("permissions", {}).setdefault("allow", [])
            if tool_name not in allow:
                allow.append(tool_name)
                settings_path.write_text(json.dumps(settings, indent=2) + "\n")
            sys.stdout.write(f"{DIM}✓ {tool_name} adicionado — retentando automaticamente…{RESET}\n\n")
            retry_flag[0] = True
        except Exception as e:
            sys.stdout.write(f"{DIM}erro ao atualizar settings: {e}{RESET}\n\n")
        sys.stdout.flush()
    else:
        sys.stdout.write(f"n\n\n")
        sys.stdout.flush()


def _handle_permission(event, proc):
    tool = event.get("tool_name", event.get("tool", "?"))
    inp  = event.get("input", {})

    sys.stdout.write(f"\n{BG_PERM}{WHITE}  permissão  {RESET}  {YELLOW}{tool}{RESET}")
    if inp:
        detail = json.dumps(inp, ensure_ascii=False)
        detail = detail if len(detail) <= 120 else detail[:120] + "…"
        sys.stdout.write(f"  {DIM}{detail}{RESET}")
    sys.stdout.write(f"\n{DIM}[y] permitir  [n] negar  [a] permitir sempre{RESET}  ")
    sys.stdout.flush()

    with _raw_stdin():
        ch = os.read(sys.stdin.fileno(), 1).decode('utf-8', errors='replace').lower()

    if ch == 'a':
        sys.stdout.write(f"a  {DIM}(permitir sempre){RESET}\n\n")
        sys.stdout.flush()
        proc.stdin.write("a\n")
        proc.stdin.flush()
        return True
    elif ch == 'y':
        sys.stdout.write(f"y  {DIM}(permitido){RESET}\n\n")
        sys.stdout.flush()
        proc.stdin.write("y\n")
        proc.stdin.flush()
        return True
    else:
        sys.stdout.write(f"n  {DIM}(negado){RESET}\n\n")
        sys.stdout.flush()
        return False
