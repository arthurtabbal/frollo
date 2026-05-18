import os
from datetime import datetime
from pathlib import Path

from .theme import DIM, RESET, TOOLS_BASH, TOOLS_EDIT, TOOLS_READ, TOOLS_AGENT_IC, CLEAR
from .gargulas import _gargula_comment
from .typewriter import log_animated

RUNDIR    = Path("/tmp/claude-client")
TOOLS_LOG = RUNDIR / "tools"


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
        display = desc if desc else cmd[:90]
        _log(TOOLS_LOG, f"{DIM}{_ts()}{RESET}  {TOOLS_BASH}⚡{RESET}  {display}\n")

    elif name in ("Read", "Glob"):
        fp = inp.get("file_path", inp.get("pattern", ""))
        _log(TOOLS_LOG, f"{DIM}{_ts()}{RESET}  {TOOLS_READ}◎{RESET}  {fp}\n")

    elif name in ("Edit", "Write"):
        fp = inp.get("file_path", "")
        _log(TOOLS_LOG, f"{DIM}{_ts()}{RESET}  {TOOLS_EDIT}✎{RESET}  {fp}\n")
        _is_vim = editor_bin in ("nvim", "vim") or editor_bin.endswith("/nvim") or editor_bin.endswith("/vim")
        if nvim_pane and fp and _is_vim:
            srv_flag = f"-L '{tmux_srv}' " if tmux_srv else ""
            line = _find_edit_line(fp, inp.get("old_string", ""))
            loc  = f"+{line} " if line else ""
            os.system(f"tmux {srv_flag}send-keys -t '{nvim_pane}' ':e {loc}{fp}' Enter 2>/dev/null")

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

    LIMIT = 12
    show = lines if len(lines) <= LIMIT else lines[:LIMIT]
    for line in show:
        _log(TOOLS_LOG, f"  {DIM}{line}{RESET}\n")
    if len(lines) > LIMIT:
        _log(TOOLS_LOG, f"  {DIM}↓ {len(lines) - LIMIT} linhas{RESET}\n")
    _log(TOOLS_LOG, "\n")
