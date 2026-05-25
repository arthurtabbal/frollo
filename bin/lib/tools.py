import os
import time
from datetime import datetime
from pathlib import Path

from .theme import DIM, RESET, TOOLS_BASH, TOOLS_EDIT, TOOLS_WRITE, TOOLS_READ, TOOLS_AGENT_IC, TOOLS_WEB, CLEAR
from .gargulas import _gargula_comment
from .typewriter import log_animated

RUNDIR    = Path(os.environ.get("CLAUDE_RUNDIR", "/tmp/claude-client"))
TOOLS_LOG = RUNDIR / "tools"

_MAX_DISPLAY    = 72   # chars de conteúdo antes do wrap no pane (~88 cols - prefixo)
_last_nvim_open = 0.0  # timestamp do último :e enviado ao nvim


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


def _is_vim_editor(editor_bin):
    return editor_bin in ("nvim", "vim") or editor_bin.endswith("/nvim") or editor_bin.endswith("/vim")


def _entry(color, icon, display):
    _log(TOOLS_LOG, f"{DIM}{_ts()}{RESET}  {color}{icon}{RESET}  {display}\n")


def _nvim_open(fp, loc, nvim_pane, tmux_srv, editor_bin):
    global _last_nvim_open
    if not (nvim_pane and fp and _is_vim_editor(editor_bin)):
        return
    srv_flag = f"-L '{tmux_srv}' " if tmux_srv else ""
    os.system(f"tmux {srv_flag}send-keys -t '{nvim_pane}' ':e {loc}{fp}' Enter 2>/dev/null")
    gap = 0.3 - (time.time() - _last_nvim_open)
    if gap > 0:
        time.sleep(gap)
    _last_nvim_open = time.time()


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
        raw = inp.get("description") or inp.get("command", "").replace("\n", " ")
        _entry(TOOLS_BASH, "⚡", raw if len(raw) <= _MAX_DISPLAY else raw[:_MAX_DISPLAY - 1] + "…")

    elif name in ("Read", "Glob"):
        fp     = inp.get("file_path", inp.get("pattern", ""))
        offset = inp.get("offset")
        _entry(TOOLS_READ, "◎", _shorten_path(fp) + (f":{offset}" if offset else ""))
        if name == "Read" and os.path.isfile(fp):
            _nvim_open(fp, f"+{offset} " if offset else "", nvim_pane, tmux_srv, editor_bin)

    elif name == "Edit":
        fp  = inp.get("file_path", "")
        old = inp.get("old_string", "").strip().replace("\n", " ")
        preview = f"  {DIM}{old[:40]}{'…' if len(old) > 40 else ''}{RESET}" if old else ""
        _entry(TOOLS_EDIT, "✎", _shorten_path(fp) + preview)
        line = _find_edit_line(fp, inp.get("old_string", ""))
        _nvim_open(fp, f"+{line} " if line else "", nvim_pane, tmux_srv, editor_bin)

    elif name == "Write":
        fp      = inp.get("file_path", "")
        suffix  = f"  {DIM}(sobrescreve){RESET}" if os.path.isfile(fp) else ""
        _entry(TOOLS_WRITE, "◆", _shorten_path(fp) + suffix)
        _nvim_open(fp, "", nvim_pane, tmux_srv, editor_bin)

    elif name == "Grep":
        fp      = inp.get("path", "")
        pattern = inp.get("pattern", "")
        _entry(TOOLS_READ, "⊕", pattern + (f"  {DIM}{_shorten_path(fp)}{RESET}" if fp else ""))

    elif name in ("WebFetch", "WebSearch"):
        query = inp.get("url", inp.get("query", name))
        _entry(TOOLS_WEB, "↓", query if len(query) <= _MAX_DISPLAY else query[:_MAX_DISPLAY - 1] + "…")

    elif name == "Agent":
        _entry(TOOLS_AGENT_IC, "◈", inp.get("description", name))

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
