import json
import os
import re
import select
import subprocess
import sys
import termios
import threading
import time
from datetime import datetime, timedelta

from ..theme import DIM, RESET, YELLOW
from ..typewriter import SKIP_FLAG
from ..gargulas import _gargula_comment

from .text import _typewrite, reset_col
from .panes import _window_height, _resize_thinking
from .stats import (
    _model_price, _model_ctx_window,
    _render_quota_line, _render_ctx_line, _render_turn_line, _render_total_line,
)
from ..usage import fetch_usage
from .turn import Turn

from ..tools import RUNDIR, _ts
from .. import config


def _parse_rate_limit_line(raw):
    """Função pura: extrai info de rate-limit de uma linha textual de stderr.
    Retorna {'reset_str': str|None, 'msg': str} se a linha indicar rate-limit, senão None."""
    if not raw:
        return None
    rl_match = re.search(r'resets?\s+(\d{1,2}:\d{2}(?:am|pm))', raw, re.IGNORECASE)
    if not (rl_match or "hit your limit" in raw.lower()):
        return None
    reset_str = None
    if rl_match:
        try:
            t = datetime.strptime(rl_match.group(1).lower(), "%I:%M%p")
            now = datetime.now()
            reset = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            if reset <= now:
                reset += timedelta(days=1)
            reset_str = reset.strftime("%H:%M" if reset.date() == now.date() else "%d/%m %H:%M")
        except ValueError:
            pass
    return {"reset_str": reset_str, "msg": raw}


def run_turn(client, message, images=None):
    """Executa um turno completo: subprocess claude, loop de eventos, spinner."""
    reset_col()
    client._last_response_text = ""
    has_images = bool(images)
    clean_text = message.replace('[img]', '').strip() if has_images else message
    cmd = [
        "claude", "--print",
        "--output-format", "stream-json",
        "--input-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
    ]
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
        # --resume com o session_id capturado no evento 'result' do turno anterior —
        # --continue retomaria a sessão mais recente do cwd, que pode não ser a nossa
        # se outro processo claude tocar o mesmo diretório entre turnos.
        if client.session_id:
            cmd += ["--resume", client.session_id]
        else:
            cmd.append("--continue")

    try:
        client.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
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

    content = []
    if has_images:
        content += [
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

    # Desabilita echo e modo canônico durante o turno — evita que teclas apareçam no
    # meio do typewriter e permite que qualquer tecla (não só Enter) acorde o select()
    # em _typewrite (ICANON ligado só libera leitura em linhas completas). ISIG fica
    # ligado — Ctrl+C continua cancelando o turno.
    _fd = sys.stdin.fileno()
    _old_term = termios.tcgetattr(_fd)
    _no_echo = list(_old_term)
    _no_echo[3] &= ~(termios.ECHO | termios.ICANON)
    _no_echo[6] = list(_old_term[6])  # cc é mutável e compartilhado — copia antes de mexer
    _no_echo[6][termios.VMIN]  = 1
    _no_echo[6][termios.VTIME] = 0
    termios.tcsetattr(_fd, termios.TCSADRAIN, _no_echo)

    thinking_autoresize = True
    turn = None
    try:
        cfg = config.load()
        thinking_autoresize = cfg.get("thinking_autoresize", True)

        _rows            = _window_height(client.tmux_srv)
        _idle_lines      = max(8,  int(_rows * 0.16))
        _max_think_lines = max(12, _rows - int(_rows * 0.26) - max(2, int(_rows * 0.08)) - 6)

        turn = Turn(client, proc, cfg, thinking_autoresize, _max_think_lines, _idle_lines)

        _rl_lock = threading.Lock()

        def _stderr_reader():
            """Lê stderr (canal separado do stdout JSON) linha a linha, appenda em
            stderr.log e faz o parsing textual de rate-limit ali — o stdout deixa
            de precisar tratar linhas não-JSON como caminho esperado."""
            stderr_log = RUNDIR / "stderr.log"
            for raw in iter(proc.stderr.readline, ''):
                with open(stderr_log, "a") as f:
                    f.write(raw)
                line = raw.strip()
                if not line:
                    continue
                parsed = _parse_rate_limit_line(line)
                with _rl_lock:
                    if parsed:
                        if not turn.rate_limited:
                            turn.rate_limited = True
                            turn.rate_limit_ts = time.time()
                        if parsed["reset_str"] and not turn.rate_limit_reset_str:
                            turn.rate_limit_reset_str = parsed["reset_str"]
                    elif turn.rate_limited:
                        turn.rate_limit_msg = line

        threading.Thread(target=_stderr_reader, daemon=True).start()

        while True:
            ready, _, _ = select.select([proc.stdout], [], [], 0.15)
            if not ready:
                turn._show_status()
                continue
            _eof = False
            while ready:
                raw = proc.stdout.readline()
                if not raw:
                    _eof = True
                    break
                turn.handle_line(raw)
                ready, _, _ = select.select([proc.stdout], [], [], 0)
            if _eof:
                break

        if turn.text_started and turn.spinner_shown:
            sys.stdout.write("\n")
            sys.stdout.flush()
        turn._clear_status()
        if turn.rate_limited and not turn.text_started:
            msg = f"rate limit — quota retoma às {turn.rate_limit_reset_str}" if turn.rate_limit_reset_str \
                  else turn.rate_limit_msg or "rate limit atingido — tente novamente mais tarde"
            sys.stdout.write(f"\n{YELLOW}⏳  {msg}{RESET}\n")
            sys.stdout.flush()
            if cfg.get("gargoyles", True):
                _g_prefix, _g_fala = _gargula_comment("rate_limit", force=True)
                if _g_prefix:
                    _typewrite(_g_prefix + _g_fala.rstrip('\n'), delay=0.025)
                    sys.stdout.write("\n")
                    sys.stdout.flush()

        elapsed = time.time() - turn.start_time
        # Para exibição/acumulado usa os totais agregados do evento 'result' quando
        # disponíveis (cobrem todos os requests do turno); senão cai no último
        # message_start (subestima turnos com tool calls, mas é o que temos).
        _disp_input  = turn.result_in_tok  if turn.result_in_tok  is not None else turn.input_tokens
        _disp_output = turn.result_out_tok if turn.result_out_tok is not None else turn.output_tokens
        client._total_input_tokens  = getattr(client, '_total_input_tokens',  0) + _disp_input
        client._total_output_tokens = getattr(client, '_total_output_tokens', 0) + _disp_output
        client._total_elapsed       = getattr(client, '_total_elapsed',       0.0) + elapsed
        if turn.result_cost is not None:
            _cost_turn = turn.result_cost
        else:
            _in_price, _out_price = _model_price(turn.model_name)
            _cost_turn = _disp_input / 1e6 * _in_price + _disp_output / 1e6 * _out_price
        client._total_cost = getattr(client, '_total_cost', 0.0) + _cost_turn

        _stats_tty_file = RUNDIR / "stats_tty"
        _stats_tty = _stats_tty_file.read_text().strip() if _stats_tty_file.exists() else ""
        if _stats_tty:
            try:
                turn_line = _render_turn_line(_ts(), _disp_input, _disp_output, elapsed, _cost_turn, turn.cache_read_tokens)
                total_line = _render_total_line(
                    client._total_input_tokens, client._total_output_tokens,
                    client._total_elapsed, client._total_cost,
                )
                _ctx_max  = _model_ctx_window(turn.model_name)
                _ctx_used = turn.input_tokens + turn.cache_read_tokens + turn.cache_creation_tokens
                try:
                    _sess_file = config.CONFIG_PATH.parent / "last_session.json"
                    _sess_file.write_text(json.dumps({
                        "ts": _ts(),
                        "input_tokens": _disp_input,
                        "output_tokens": _disp_output,
                        "cache_read_tokens": turn.cache_read_tokens,
                        "elapsed": round(elapsed, 1),
                        "cost_turn": _cost_turn,
                        "total_input": client._total_input_tokens,
                        "total_output": client._total_output_tokens,
                        "total_elapsed": round(client._total_elapsed, 0),
                        "total_cost": client._total_cost,
                        "ctx_tokens": _ctx_used,
                        "ctx_max": _ctx_max,
                        "model": turn.model_name,
                    }))
                except Exception:
                    pass
                ctx_line = _render_ctx_line(_ctx_used, _ctx_max)
                quota_line = _render_quota_line(None)
                content = "\033[H" + turn_line + "\n" + total_line + "\n" + ctx_line + "\n" + quota_line
                _fd2 = os.open(_stats_tty, os.O_WRONLY | os.O_NOCTTY)
                os.write(_fd2, content.encode())
                os.close(_fd2)
            except OSError:
                pass

        # Contador de geração: turnos rápidos consecutivos disparam _bg_usage threads
        # que podem terminar fora de ordem. Cada uma captura a geração no início e só
        # repinta a linha 4 se ainda for a corrente — evita cota stale por cima da fresca.
        client._usage_gen = getattr(client, '_usage_gen', 0) + 1
        _my_usage_gen = client._usage_gen

        def _bg_usage(_gen):
            result = fetch_usage()
            if not result:
                return
            client._last_usage = result
            client._last_usage_at = time.time()
            try:
                _quota_file = config.CONFIG_PATH.parent / "last_quota.json"
                _quota_file.write_text(json.dumps(result))
            except Exception:
                pass
            if _stats_tty and getattr(client, '_usage_gen', 0) == _gen:
                try:
                    # cota é a 4ª (última) linha do pane; repinta só ela
                    line = "\033[4;1H" + _render_quota_line(result)
                    _fd = os.open(_stats_tty, os.O_WRONLY | os.O_NOCTTY)
                    os.write(_fd, line.encode())
                    os.close(_fd)
                except OSError:
                    pass

        if _stats_tty:
            threading.Thread(target=_bg_usage, args=(_my_usage_gen,), daemon=True).start()

        proc.wait()
    finally:
        if thinking_autoresize and turn is not None and turn.thinking_lines > turn._idle_lines:
            _resize_thinking(client.tmux_srv, "idle")
        termios.tcsetattr(_fd, termios.TCSADRAIN, _old_term)
    client.first_turn = False
    return turn.perm_approved if turn is not None else False
