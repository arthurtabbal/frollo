import os
import re
import shlex
import time
from pathlib import Path

_last_nvim_open = 0.0
_READ_COMMANDS = {"cat", "head", "tail", "less", "more", "nl", "sed"}
_COMMAND_SEPARATORS = {"|", "||", "&&", ";"}
_SED_LINE_RE = re.compile(r"^\s*(\d+)(?:\s*,\s*(?:\d+|\$))?\s*p?\s*$")
_DIFF_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


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


def _shell_words(command):
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return []


def _unwrap_shell(words):
    if not words:
        return words

    cmd = os.path.basename(words[0])
    if cmd not in ("bash", "sh", "zsh"):
        return words

    for index, word in enumerate(words[1:], start=1):
        if word == "-c" or (word.startswith("-") and "c" in word[1:]):
            if index + 1 < len(words):
                return _shell_words(words[index + 1])
            return []
    return words


def _command_segments(words):
    segment = []
    separator = None
    for word in words:
        if word in _COMMAND_SEPARATORS:
            if segment:
                yield separator, segment
                segment = []
            separator = word
            continue
        segment.append(word)
    if segment:
        yield separator, segment


def _sed_line(script):
    match = _SED_LINE_RE.match(script or "")
    if match:
        return int(match.group(1))
    return None


def _is_file_arg(word):
    return bool(word and word != "-" and os.path.isfile(word))


def _first_existing_file(words):
    return next((word for word in words if _is_file_arg(word)), None)


def _sed_parts(segment):
    scripts = []
    files = []
    implicit_script_seen = False
    index = 1
    while index < len(segment):
        word = segment[index]
        if word in ("-n", "--quiet", "--silent"):
            index += 1
        elif word in ("-e", "--expression"):
            if index + 1 < len(segment):
                scripts.append(segment[index + 1])
            index += 2
        elif word in ("-f", "--file"):
            index += 2
        elif word.startswith("-"):
            index += 1
        elif not implicit_script_seen:
            scripts.append(word)
            implicit_script_seen = True
            index += 1
        else:
            files.append(word)
            index += 1
    line = next((line for line in (_sed_line(script) for script in scripts) if line), None)
    return files, line


def _diff_first_new_line(diff):
    for line in (diff or "").splitlines():
        match = _DIFF_HUNK_RE.match(line)
        if match:
            return max(1, int(match.group(1)))
    return None


def _bash_read_target(command):
    words = _unwrap_shell(_shell_words(command or ""))
    target_fp = None
    target_line = None

    for separator, segment in _command_segments(words):
        if target_fp and separator != "|":
            return target_fp, target_line
        if not segment:
            continue

        cmd = os.path.basename(segment[0])
        if cmd not in _READ_COMMANDS:
            continue

        if cmd == "sed":
            files, line = _sed_parts(segment)
            fp = _first_existing_file(files)
            if fp:
                target_fp, target_line = fp, line
            elif target_fp and separator == "|" and line:
                target_line = line
            continue

        fp = _first_existing_file(segment[1:])
        if fp:
            target_fp, target_line = fp, None

    return target_fp, target_line


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

