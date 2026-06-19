import json
import os
import select
import sys
import termios
import tty
from datetime import datetime
from pathlib import Path

from .theme import BG_USER, DIM, RESET, WHITE


def pick_session(cwd):
    """Picker interativo de sessões anteriores. Retorna session ID ou None."""
    try:
        return _pick_session_impl(cwd)
    except Exception:
        return None


def _pick_session_impl(cwd):
    project_key = cwd.replace('/', '-')
    sessions_dir = Path.home() / ".claude" / "projects" / project_key
    if not sessions_dir.exists():
        return None

    files = sorted(sessions_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return None

    sessions = []
    for f in files[:20]:
        session_id = f.stem
        first_msg = ""
        ts_str = ""
        try:
            ts_str = datetime.fromtimestamp(f.stat().st_mtime).strftime("%d/%m %H:%M")
            with open(f) as fp:
                for line in fp:
                    try:
                        ev = json.loads(line)
                        if ev.get("type") == "queue-operation" and ev.get("operation") == "enqueue":
                            content = ev.get("content", "").strip()
                            if content:
                                first_msg = content.replace("\n", " ")
                                break
                    except Exception:
                        continue
        except Exception:
            pass
        if first_msg:
            sessions.append((session_id, ts_str, first_msg))

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
