import os
import time
from datetime import datetime
from pathlib import Path

from .theme import DIM, RESET, TOOLS_BASH, TOOLS_EDIT, TOOLS_READ, TOOLS_AGENT_IC, TOOLS_WEB, CLEAR
from .gargulas import _gargula_comment
from .typewriter import log_animated

RUNDIR    = Path(os.environ.get("CLAUDE_RUNDIR", "/tmp/claude-client"))
TOOLS_LOG = RUNDIR / "tools"

_MAX_DISPLAY = 72          # chars de conteúdo antes do wrap no pane (~88 cols - prefixo)
_last_nvim_open = [0.0]   # timestamp do último :e enviado ao nvim


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


def _find_edit_line(file_path, old_string):
    if not old_string:
        return None
    try:
        content = Path(file_path).read_text()
        idx = content.find(old_string)
        if idx == -1:
            return None
        return content[:idx].count('\n') + 1
    except Exception:
        return None


def log_tool_call(block, nvim_pane="", tmux_srv="", editor_bin=""):
    _clear_tools_pane()
    name = block.get("name", "")
    inp  = block.get("input", {})

    if name == "Bash":
        desc = inp.get("description", "")
        cmd  = inp.get("command", "").replace("\n", " ")
        raw  = desc if desc else cmd
        display = raw if len(raw) <= _MAX_DISPLAY else raw[:_MAX_DISPLAY - 1] + "…"
        _log(TOOLS_LOG, f"{DIM}{_ts()}{RESET}  {TOOLS_BASH}⚡{RESET}  {display}\n")

    elif name in ("Read", "Glob"):
        fp = inp.get("file_path", inp.get("pattern", ""))
        _log(TOOLS_LOG, f"{DIM}{_ts()}{RESET}  {TOOLS_READ}◎{RESET}  {_shorten_path(fp)}\n")
        _is_vim = editor_bin in ("nvim", "vim") or editor_bin.endswith("/nvim") or editor_bin.endswith("/vim")
        if name == "Read" and nvim_pane and fp and _is_vim and os.path.isfile(fp):
            srv_flag = f"-L '{tmux_srv}' " if tmux_srv else ""
            offset = inp.get("offset")
            loc = f"+{offset} " if offset else ""
            os.system(f"tmux {srv_flag}send-keys -t '{nvim_pane}' ':e {loc}{fp}' Enter 2>/dev/null")
            gap = 0.3 - (time.time() - _last_nvim_open[0])
            if gap > 0:
                time.sleep(gap)
            _last_nvim_open[0] = time.time()

    elif name in ("Edit", "Write"):
        fp = inp.get("file_path", "")
        _log(TOOLS_LOG, f"{DIM}{_ts()}{RESET}  {TOOLS_EDIT}✎{RESET}  {_shorten_path(fp)}\n")
        _is_vim = editor_bin in ("nvim", "vim") or editor_bin.endswith("/nvim") or editor_bin.endswith("/vim")
        if nvim_pane and fp and _is_vim:
            srv_flag = f"-L '{tmux_srv}' " if tmux_srv else ""
            line = _find_edit_line(fp, inp.get("old_string", ""))
            loc  = f"+{line} " if line else ""
            os.system(f"tmux {srv_flag}send-keys -t '{nvim_pane}' ':e {loc}{fp}' Enter 2>/dev/null")

    elif name == "Grep":
        pattern = inp.get("pattern", "")
        path    = inp.get("path", "")
        display = pattern + (f"  {DIM}{_shorten_path(path)}{RESET}" if path else "")
        _log(TOOLS_LOG, f"{DIM}{_ts()}{RESET}  {TOOLS_READ}⊕{RESET}  {display}\n")

    elif name in ("WebFetch", "WebSearch"):
        query = inp.get("url", inp.get("query", name))
        display = query[:90] if len(query) > 90 else query
        _log(TOOLS_LOG, f"{DIM}{_ts()}{RESET}  {TOOLS_WEB}↓{RESET}  {display}\n")

    elif name == "Agent":
        desc = inp.get("description", name)
        _log(TOOLS_LOG, f"{DIM}{_ts()}{RESET}  {TOOLS_AGENT_IC}◈{RESET}  {desc}\n")

    else:
        _log(TOOLS_LOG, f"{DIM}{_ts()}{RESET}  →  {name}\n")

    tool_key = name if name in ("Bash", "Edit", "Write", "Read") else None
    prefix, fala = _gargula_comment(tool_key)
    if prefix:
        _log(TOOLS_LOG, '\n')
        _log(TOOLS_LOG, prefix)
        log_animated(TOOLS_LOG, fala)
        _log(TOOLS_LOG, '\n')


def log_tool_result(block):
    content = block.get("content", "")
    lines = []

    if isinstance(content, list):
        for item in content:
            if item.get("type") == "text":
                lines = item["text"].strip().split("\n")
                break
    elif isinstance(content, str):
        lines = content.strip().split("\n")

    if not lines or lines == [""]:
        return

    LIMIT    = 12
    MAX_COLS = 68
    show = lines if len(lines) <= LIMIT else lines[:LIMIT]
    for line in show:
        line = line.rstrip()
        if len(line) > MAX_COLS:
            line = line[:MAX_COLS - 1] + "…"
        _log(TOOLS_LOG, f"  {DIM}{line}{RESET}\n")
    if len(lines) > LIMIT:
        _log(TOOLS_LOG, f"  {DIM}↓ {len(lines) - LIMIT} linhas{RESET}\n")
    _log(TOOLS_LOG, "\n")
