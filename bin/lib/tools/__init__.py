import os

from ..theme import DIM, RESET, TOOLS_BASH, TOOLS_EDIT, TOOLS_WRITE, TOOLS_READ, TOOLS_AGENT_IC, TOOLS_WEB, TOOLS_TODO
from ..gargulas import _gargula_comment
from .. import config

from .display import RUNDIR, TOOLS_LOG, _ts, _log, _entry, _shorten_path, _clear_tools_pane, _MAX_DISPLAY
from .nvim import _nvim_open, _find_edit_line

def log_tool_call(block, nvim_pane="", tmux_srv="", editor_bin="", render=None):
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
        fp     = inp.get("file_path", "")
        suffix = f"  {DIM}(sobrescreve){RESET}" if os.path.isfile(fp) else ""
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

    elif name == "TodoWrite":
        todos   = inp.get("todos", [])
        done    = sum(1 for t in todos if t.get("status") == "completed")
        active  = sum(1 for t in todos if t.get("status") == "in_progress")
        _entry(TOOLS_TODO, "☑", f"{len(todos)} tasks · {active} active · {done} done")

    else:
        _log(TOOLS_LOG, f"{DIM}{_ts()}{RESET}  →  {name}\n")

    if config.load().get("gargoyles", True):
        tool_key = name if name in ("Bash", "Edit", "Write", "Read") else None
        prefix, fala = _gargula_comment(tool_key)
        if prefix:
            _log(TOOLS_LOG, '\n')
            _log(TOOLS_LOG, prefix)
            if render is not None:
                render.push_file(TOOLS_LOG, fala)
                render.join()
            else:
                _log(TOOLS_LOG, fala)
            _log(TOOLS_LOG, '\n')


def log_tool_result(block):
    content = block.get("content", "")
    text = ""
    if isinstance(content, list):
        for item in content:
            if item.get("type") == "text":
                text = item["text"].strip()
                break
    elif isinstance(content, str):
        text = content.strip()

    lines = text.split("\n") if text else []
    if not lines:
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
