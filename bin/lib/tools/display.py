import os
from datetime import datetime
from pathlib import Path

from ..theme import DIM, RESET, CLEAR

RUNDIR    = Path(os.environ.get("CLAUDE_RUNDIR", "/tmp/claude-client"))
TOOLS_LOG = RUNDIR / "tools"

_MAX_DISPLAY = 72  # chars de conteúdo antes do wrap no pane (~88 cols - prefixo)


def _ts():
    return datetime.now().strftime("%H:%M:%S")


def _log(path, text):
    with open(path, "a", buffering=1) as f:
        f.write(text)


def _clear_tools_pane():
    tty_file = RUNDIR / "tools_tty"
    if not tty_file.exists():
        return
    tty = tty_file.read_text().strip()
    if not tty:
        return
    TOOLS_LOG.write_text("")
    try:
        fd = os.open(tty, os.O_WRONLY | os.O_NOCTTY)
        os.write(fd, CLEAR.encode())
        os.close(fd)
    except OSError:
        pass


def _shorten_path(fp, maxlen=55):
    try:
        rel = os.path.relpath(fp)
        if len(rel) <= len(fp):
            fp = rel
    except ValueError:
        pass
    if len(fp) > maxlen:
        fp = "…" + fp[-(maxlen - 1):]
    return fp


def _entry(color, icon, display):
    _log(TOOLS_LOG, f"{DIM}{_ts()}{RESET}  {color}{icon}{RESET}  {display}\n")
