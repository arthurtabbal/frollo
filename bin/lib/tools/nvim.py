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
