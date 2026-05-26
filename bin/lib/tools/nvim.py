import os
import time
from pathlib import Path

_last_nvim_open = 0.0


def _is_vim_editor(editor_bin):
    return editor_bin in ("nvim", "vim") or editor_bin.endswith("/nvim") or editor_bin.endswith("/vim")


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


def _tmux_send(nvim_pane, tmux_srv, cmd):
    srv = f"-L '{tmux_srv}' " if tmux_srv else ""
    os.system(f"tmux {srv}send-keys -t '{nvim_pane}' '{cmd}' Enter 2>/dev/null")


def _nvim_open(fp, loc, nvim_pane, tmux_srv, editor_bin):
    global _last_nvim_open
    if not (nvim_pane and fp and _is_vim_editor(editor_bin)):
        return
    _tmux_send(nvim_pane, tmux_srv, f":e {loc}{fp}")
    gap = 0.3 - (time.time() - _last_nvim_open)
    if gap > 0:
        time.sleep(gap)
    _last_nvim_open = time.time()


def _grep_to_quickfix(text, nvim_pane, tmux_srv, editor_bin):
    if not (nvim_pane and text.strip() and _is_vim_editor(editor_bin)):
        return
    qf = Path("/tmp/frollo-grep.qf")
    qf.write_text(text.strip() + "\n")
    _tmux_send(nvim_pane, tmux_srv, f":cfile {qf} | copen")


def _glob_to_scratch(text, nvim_pane, tmux_srv, editor_bin):
    if not (nvim_pane and text.strip() and _is_vim_editor(editor_bin)):
        return
    scratch = Path("/tmp/frollo-glob.txt")
    scratch.write_text(text.strip() + "\n")
    _tmux_send(nvim_pane, tmux_srv, f":e {scratch}")


def _todos_to_scratch(todos, nvim_pane, tmux_srv, editor_bin):
    if not (nvim_pane and todos and _is_vim_editor(editor_bin)):
        return
    icons  = {"completed": "✓", "in_progress": "→", "pending": "·"}
    lines  = [
        f"{icons.get(t.get('status', ''), '·')}  "
        f"{'[' + t['priority'] + '] ' if t.get('priority') else ''}"
        f"{t.get('content', '')}"
        for t in todos
    ]
    scratch = Path("/tmp/frollo-todos.txt")
    scratch.write_text("\n".join(lines) + "\n")
    _tmux_send(nvim_pane, tmux_srv, f":e {scratch}")
