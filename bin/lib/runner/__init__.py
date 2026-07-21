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
from ..gargulas import _gargula_comment

from .text import _typewrite, reset_col
from .panes import _window_height, _resize_thinking
from .render import RenderQueue
from .stats import (
    _model_price, _model_ctx_window,
    _render_quota_line, _render_ctx_line, _render_turn_line, _render_total_line,
)
from ..usage import fetch_usage
from .turn import Turn

from ..tools import RUNDIR, _ts
from .. import config
from .. import errors

# Teto de ociosidade, não de duração — mesmo valor e mesmo conceito do backend
# Codex (ver _CODEX_IDLE_TIMEOUT em runner/codex.py): um turno longo que segue
# emitindo linhas nunca é cortado; só o silêncio absoluto vira falha explícita.
_CLAUDE_IDLE_TIMEOUT = 120


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


def _stderr_reader(proc, client, rl_lock):
    """Lê stderr (canal separado do stdout JSON) linha a linha, appenda em
    stderr.log e faz o parsing textual de rate-limit ali — o stdout deixa de
    precisar tratar linhas não-JSON como caminho esperado.

    Roda pela vida inteira do processo `claude` (não só de um turno): em modo
    persistente o mesmo processo atende vários turnos, então lê `client._current_turn`
    a cada linha em vez de fechar sobre um Turn fixo."""
    stderr_log = RUNDIR / "stderr.log"
    for raw in iter(proc.stderr.readline, ''):
        with open(stderr_log, "a") as f:
            f.write(raw)
        line = raw.strip()
        if not line:
            continue
        turn = client._current_turn
        if turn is None:
            continue
        parsed = _parse_rate_limit_line(line)
        with rl_lock:
            if parsed:
                if not turn.rate_limited:
                    turn.rate_limited = True
                    turn.rate_limit_ts = time.time()
                if parsed["reset_str"] and not turn.rate_limit_reset_str:
                    turn.rate_limit_reset_str = parsed["reset_str"]
            elif turn.rate_limited:
                turn.rate_limit_msg = line


def _terminate_proc(proc, timeout=3.0):
    """Encerra um processo claude (usado sobretudo no modo persistente — Fase 4).

    Achado do spike de protocolo (PLANO_MELHORIAS.md 4.1): um processo que ficou vivo
    entre vários turnos não sai sozinho só porque o stdin fechou (bug conhecido,
    issue #25629) — precisa de SIGTERM com timeout e SIGKILL de reserva."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
    except (OSError, ValueError):
        pass
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _ensure_proc(client, persistent):
    """Devolve um processo claude pronto pra receber a mensagem do turno.

    Modo per-turn (default): sempre spawna um processo novo — comportamento
    inalterado desde antes da Fase 4.
    Modo persistente (`persistent: true`): reaproveita `client.proc` entre turnos
    enquanto ele seguir vivo e tiver sido spawnado com o mesmo modo/modelo; troca de
    `/model` ou Shift+Tab mata o processo atual e respawna com `--resume
    <session_id>` — mesmo custo do modo per-turn ao trocar, nunca pior."""
    proc_desc = (client.mode.value, getattr(client, "model", None))
    if (persistent and client.proc is not None and client.proc.poll() is None
            and getattr(client, "_proc_desc", None) == proc_desc):
        return client.proc, True

    if client.proc is not None and client.proc.poll() is None:
        _terminate_proc(client.proc)

    cmd = [
        "claude", "--print",
        "--output-format", "stream-json",
        "--input-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--thinking-display", "summarized",
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

    proc = subprocess.Popen(
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
    client.proc = proc
    client._proc_desc = proc_desc
    return proc, False


def run_turn(client, message, images=None):
    """Executa um turno completo: subprocess claude, loop de eventos, spinner."""
    reset_col()
    client._last_response_text = ""
    has_images = bool(images)
    clean_text = message.replace('[img]', '').strip() if has_images else message
    cfg = config.load()
    persistent = cfg.get("persistent", False)

    try:
        proc, _reused = _ensure_proc(client, persistent)
    except (FileNotFoundError, PermissionError) as exc:
        errors.report(
            "claude", "não foi possível iniciar o CLI `claude`",
            severity="fatal", code="spawn_failed", detail=str(exc),
            tmux_srv=client.tmux_srv,
        )
        sys.stdout.write(
            f"{DIM}Instale com: npm i -g @anthropic-ai/claude-code{RESET}\n"
        )
        sys.stdout.flush()
        return False

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
    if not persistent:
        proc.stdin.close()

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
    render = None
    try:
        thinking_autoresize = cfg.get("thinking_autoresize", True)

        _rows            = _window_height(client.tmux_srv)
        _idle_lines      = max(8,  int(_rows * 0.16))
        _max_think_lines = max(12, _rows - int(_rows * 0.26) - max(2, int(_rows * 0.08)) - 6)

        render = RenderQueue()
        turn = Turn(client, proc, cfg, thinking_autoresize, _max_think_lines, _idle_lines, render)
        render.start(
            status_cb=turn._show_status,
            clear_status_cb=turn._clear_status,
            is_streaming_cb=lambda: client._streaming_text,
        )

        # Roteia pro turn corrente por indireção (client._current_turn), não por
        # closure direta sobre `turn` — em modo persistente o processo (e portanto
        # sua thread de stderr) sobrevive a vários turnos, cada um com seu próprio
        # objeto Turn; sem isso, rate-limit detectado no turno N+1 atualizaria o
        # Turn descartado do turno N.
        client._current_turn = turn
        if not _reused:
            client._rl_lock = threading.Lock()
            threading.Thread(target=_stderr_reader, args=(proc, client, client._rl_lock), daemon=True).start()

        _idle_hit = False
        _eof = False
        last_line_at = time.monotonic()
        while True:
            ready, _, _ = select.select([proc.stdout], [], [], 0.15)
            if not ready:
                if errors.idle_timed_out(last_line_at, time.monotonic(), _CLAUDE_IDLE_TIMEOUT):
                    # Silêncio absoluto no stdout, processo ainda vivo — o mesmo
                    # sintoma que motivou o teto de ociosidade do Codex (ver
                    # runner/codex.py): sem isso o turno gira pra sempre.
                    _idle_hit = True
                    break
                continue
            _eof = False
            while ready:
                raw = proc.stdout.readline()
                if not raw:
                    _eof = True
                    break
                last_line_at = time.monotonic()
                turn.handle_line(raw)
                if turn.turn_done:
                    # 'result' delimita o turno no protocolo — em modo persistente
                    # (Fase 4) o processo continua vivo e não fecha stdout sozinho,
                    # então esperar EOF aqui travaria o turno pra sempre.
                    break
                ready, _, _ = select.select([proc.stdout], [], [], 0)
            if _eof or turn.turn_done:
                break

        # Deixa a animação pendente (ex.: cauda do último bloco de texto) terminar
        # em ritmo normal e para a thread — só depois disso é seguro o main thread
        # voltar a escrever no stdout sozinho (checks abaixo).
        render.stop()

        if _idle_hit:
            errors.report(
                "claude", f"nenhuma linha do claude por {_CLAUDE_IDLE_TIMEOUT}s — turno abortado",
                severity="fatal", code="idle_timeout",
                detail=errors.tail_file(RUNDIR / "stderr.log"),
                tmux_srv=client.tmux_srv,
            )
        elif _eof and not turn.turn_done:
            # stdout fechou sem o evento 'result': o processo morreu no meio do
            # turno. Sem isso o turno só voltava ao prompt, mudo.
            errors.report(
                "claude", "o processo `claude` terminou no meio do turno",
                severity="fatal", code="process_died",
                detail=errors.process_diagnostics(proc.poll(), RUNDIR / "stderr.log"),
                tmux_srv=client.tmux_srv,
            )

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

        if not persistent:
            proc.wait()
        # persistente: o processo continua vivo pro próximo turno — não fecha
        # stdin nem espera saída (achado do spike: não sairia sozinho mesmo assim).
    finally:
        # Rede de segurança para saída anormal (exceção, Ctrl+C): se `render.stop()`
        # já rodou no caminho normal acima, a thread já está morta e isto é um
        # no-op seguro (join em thread já terminada retorna na hora). Se a
        # exceção interrompeu o turno antes disso, força o esvaziamento imediato
        # da fila em vez de esperar a animação terminar em ritmo normal.
        if render is not None:
            render.cancel()
        if thinking_autoresize and turn is not None and turn.thinking_lines > turn._idle_lines:
            _resize_thinking(client.tmux_srv, "idle")
        termios.tcsetattr(_fd, termios.TCSADRAIN, _old_term)
    client.first_turn = False
    return turn.perm_approved if turn is not None else False
