import os
import subprocess

from ..tools import RUNDIR

THINKING_LOG  = RUNDIR / "thinking"
THINKING_PANE = RUNDIR / "thinking_pane"
CHAT_PANE     = RUNDIR / "chat_pane"
TOOLS_PANE    = RUNDIR / "tools_pane"
STATS_PANE    = RUNDIR / "stats_pane"


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


def _pane_height(tmux_srv, pane_file):
    try:
        pane_id = pane_file.read_text().strip()
    except OSError:
        return 0
    if not pane_id:
        return 0
    try:
        r = subprocess.run(
            ["tmux", "-L", tmux_srv, "display-message", "-p", "-t", pane_id, "#{pane_height}"],
            capture_output=True, text=True,
        )
        return int(r.stdout.strip())
    except Exception:
        return 0


def _grow_tools(tmux_srv, lines):
    """Cresce o pane de tools até caber `lines` — nunca encolhe.

    Usado quando um erro precisa ser lido inteiro (ver lib/errors.py). O teto é
    metade da janela: erro é importante, mas não come o chat."""
    if not tmux_srv or not TOOLS_PANE.exists():
        return
    target = min(lines, max(6, int(_window_height(tmux_srv) * 0.5)))
    if target > _pane_height(tmux_srv, TOOLS_PANE):
        _pane_resize(tmux_srv, TOOLS_PANE, target)


_STATS_LINES = 4  # keep in sync with frollo.sh _STATS_LINES


def _resize_thinking(tmux_srv, size):
    """Redimensiona o pane thinking. size: 'idle'|'summary' ou int linhas."""
    if not tmux_srv or not THINKING_PANE.exists():
        return
    rows        = _window_height(tmux_srv)
    has_stats   = STATS_PANE.exists()
    tools_lines = max(6, int(rows * 0.26) - (_STATS_LINES if has_stats else 0))
    if isinstance(size, int):
        if has_stats:
            _pane_resize(tmux_srv, STATS_PANE, _STATS_LINES)
        _pane_resize(tmux_srv, THINKING_PANE, size)
    else:
        lines = {"idle": max(8, int(rows * 0.16)), "summary": max(5, int(rows * 0.10))}[size]
        _pane_resize(tmux_srv, TOOLS_PANE, tools_lines)
        if has_stats:
            _pane_resize(tmux_srv, STATS_PANE, _STATS_LINES)
        _pane_resize(tmux_srv, THINKING_PANE, lines)
