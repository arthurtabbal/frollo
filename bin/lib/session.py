import json
import os
import select
import sys
import termios
import time
import traceback
import tty
from datetime import datetime
from pathlib import Path

from .theme import BG_USER, DIM, RESET, WHITE
from .tools import RUNDIR


def pick_session(cwd):
    """Picker interativo de sessões anteriores. Retorna session ID ou None."""
    try:
        return _pick_session_impl(cwd)
    except Exception:
        try:
            with open(RUNDIR / "err.log", "a") as f:
                f.write(f"[{time.strftime('%F %T')}] session picker: {traceback.format_exc()}\n")
        except OSError:
            pass
        return None


def _first_user_text(ev):
    """Extrai texto de um evento de sessão, cobrindo dois schemas conhecidos:
    queue-operation/enqueue (schema antigo) e message.content de type=="user"
    (schema atual, string ou lista de blocos). Retorna "" se não reconhecido."""
    if ev.get("type") == "queue-operation" and ev.get("operation") == "enqueue":
        return (ev.get("content") or "").strip()
    if ev.get("type") == "user":
        msg_content = ev.get("message", {}).get("content", "")
        if isinstance(msg_content, list):
            msg_content = next(
                (b.get("text", "") for b in msg_content if b.get("type") == "text"), ""
            )
        return (msg_content or "").strip()
    return ""


def _load_sessions(sessions_dir, limit=20):
    """Varre os .jsonl de um diretório de projeto e monta [(session_id, ts_str, first_msg)],
    mais recentes primeiro. Função pura (sem I/O de terminal) — testável isoladamente."""
    files = sorted(sessions_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)

    sessions = []
    for f in files[:limit]:
        session_id = f.stem
        first_msg = ""
        ts_str = ""
        try:
            ts_str = datetime.fromtimestamp(f.stat().st_mtime).strftime("%d/%m %H:%M")
            with open(f) as fp:
                for line in fp:
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    content = _first_user_text(ev)
                    if content:
                        first_msg = content.replace("\n", " ")
                        break
        except Exception:
            pass
        if first_msg:
            sessions.append((session_id, ts_str, first_msg))
    return sessions


def _pick_session_impl(cwd):
    project_key = cwd.replace('/', '-')
    sessions_dir = Path.home() / ".claude" / "projects" / project_key
    if not sessions_dir.exists():
        return None

    sessions = _load_sessions(sessions_dir)
    if not sessions:
        return None

    selected = 0
    try:
        cols = os.get_terminal_size().columns
    except OSError:
        cols = 80

    def render():
        sys.stdout.write('\033[2J\033[H')
        sys.stdout.write(f"{DIM}Sessões anteriores — ↑↓ navegar · Enter confirmar · Esc cancelar{RESET}\r\n\r\n")
        for i, (sid, ts, msg) in enumerate(sessions):
            preview = msg[:65] + "…" if len(msg) > 65 else msg
            if i == selected:
                pad = max(0, cols - len(f"  {ts}  {preview}  ") - 2)
                sys.stdout.write(f"  {BG_USER}{WHITE}{ts}  {preview}{' ' * pad}{RESET}\r\n")
            else:
                sys.stdout.write(f"  {DIM}{ts}{RESET}  {preview}\r\n")
        sys.stdout.flush()

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    result = None
    try:
        tty.setraw(fd)
        render()
        while True:
            b = os.read(fd, 1)
            if b in (b'\r', b'\n'):
                result = sessions[selected][0]
                break
            elif b in (b'q', b'\x03'):
                break
            elif b == b'\x1b':
                ready, _, _ = select.select([sys.stdin], [], [], 0.05)
                if ready:
                    rest = os.read(fd, 8)
                    if rest.startswith(b'[A') and selected > 0:
                        selected -= 1
                        render()
                    elif rest.startswith(b'[B') and selected < len(sessions) - 1:
                        selected += 1
                        render()
                else:
                    break  # Esc
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write('\033[2J\033[H')
        sys.stdout.flush()

    return result
