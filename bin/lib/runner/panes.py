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


def _resize_thinking(tmux_srv, size):
    """Redimensiona o pane thinking. size: 'idle'|'summary' ou int linhas."""
    if not tmux_srv or not THINKING_PANE.exists():
        return
    rows        = _window_height(tmux_srv)
    tools_lines = max(6, int(rows * 0.26))
    stats_lines = 2
    if isinstance(size, int):
        _pane_resize(tmux_srv, STATS_PANE, stats_lines)
        _pane_resize(tmux_srv, THINKING_PANE, size)
    else:
        lines = {"idle": max(8, int(rows * 0.16)), "summary": max(5, int(rows * 0.10))}[size]
        _pane_resize(tmux_srv, TOOLS_PANE, tools_lines)
        _pane_resize(tmux_srv, STATS_PANE, stats_lines)
        _pane_resize(tmux_srv, THINKING_PANE, lines)
