import base64
import json
import os
import re
import select
import subprocess
import sys
import termios
import tty
from pathlib import Path

from .theme import DIM, RESET, GREEN, WHITE, BG_USER, CLEAR

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b[a-zA-Z]')

_PASTE_START = b'[200~'
_PASTE_END = b'\x1b[201~'


def _parse_paste(read_more, prefix=b''):
    """Acumula bytes (via read_more(), que retorna b'' no EOF) até achar o terminador
    do bracketed paste. Extraído do I/O pra ser testável com bytes sintéticos."""
    buf = prefix
    while _PASTE_END not in buf:
        chunk = read_more()
        if not chunk:
            break
        buf += chunk
    end = buf.find(_PASTE_END)
    content = buf if end == -1 else buf[:end]
    # Normaliza quebras de linha: o paste do tmux (paste-buffer) usa `\r`, o
    # terminal nativo usa `\n`. `line`/`_visual_pos`/`_redraw` só entendem `\n` —
    # um `\r` cru sobrescreveria a linha em raw mode e bagunçaria o cálculo de cursor.
    text = content.decode('utf-8', errors='replace')
    return text.replace('\r\n', '\n').replace('\r', '\n')

HISTORY_PATH = Path(os.environ.get(
    "FROLLO_HISTORY",
    str(Path.home() / ".config" / "frollo" / "history.json"),
))
_HISTORY_MAX = 500


def _load_history():
    try:
        return json.loads(HISTORY_PATH.read_text())
    except Exception:
        return []


def _save_history(history):
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_PATH.write_text(json.dumps(history[-_HISTORY_MAX:]))
    except Exception:
        pass


def _get_clipboard_image():
    """Lê imagem do clipboard (Wayland ou X11). Retorna (base64_str, mime_type) ou None."""
    for cmd, mime in [
        (['wl-paste', '--type', 'image/png'], 'image/png'),
        (['wl-paste', '--type', 'image/jpeg'], 'image/jpeg'),
        (['xclip', '-selection', 'clipboard', '-t', 'image/png', '-o'], 'image/png'),
        (['xclip', '-selection', 'clipboard', '-t', 'image/jpeg', '-o'], 'image/jpeg'),
    ]:
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=2)
            if r.returncode == 0 and r.stdout:
                return base64.standard_b64encode(r.stdout).decode(), mime
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def _visual_pos(visible_prompt, line_chars, idx, cols):
    """Retorna (row, col) da posição idx no bloco exibido, considerando wrap e \n.

    Modela deferred wrap: ao preencher a última coluna o cursor fica nela (col=cols-1)
    com wrap pendente; só avança de linha quando o próximo char é escrito.
    """
    row = col = 0
    deferred_wrap = False
    for ch in visible_prompt + ''.join(line_chars[:idx]):
        if ch == '\n':
            row += 1
            col = 0
            deferred_wrap = False
        else:
            if deferred_wrap:
                row += 1
                col = 0
                deferred_wrap = False
            col += 1
            if col == cols:
                deferred_wrap = True
                col = cols - 1
    return row, col


class InputReader:
    def __init__(self, mode_ref, prompt_provider=None):
        """mode_ref: a mutable container [Mode] so we can cycle mode from outside.
        prompt_provider: callable opcional que retorna o prompt já formatado (com
        ANSI) — permite ao chamador (ex: ClaudeClient) incluir badges extras (modelo)
        sem o InputReader precisar conhecê-los. Sem provider, cai no prompt default
        (só o badge de modo)."""
        self._mode_ref = mode_ref
        self._prompt_provider = prompt_provider
        self._history: list[str] = _load_history()
        self.pending_image = None  # {'data': b64str, 'media_type': str}

    def _prompt(self):
        if self._prompt_provider:
            return self._prompt_provider()
        mode = self._mode_ref[0]
        if mode.value == "auto":
            badge = f"{GREEN}auto{RESET}"
        else:
            badge = f"{DIM}normal{RESET}"
        return f"{WHITE}({RESET}{badge}{WHITE}){RESET} {WHITE}>_{RESET} "

    def _vprompt(self):
        # Largura visual real do prompt (sem ANSI) — precisa refletir o prompt_provider
        # (que pode incluir badge de modelo) senão o cálculo de wrap/cursor desalinha.
        return _ANSI_RE.sub('', self._prompt())

    def _cycle_mode(self, modes):
        idx = modes.index(self._mode_ref[0])
        self._mode_ref[0] = modes[(idx + 1) % len(modes)]

    def read_input(self, modes, pre_clear_hook=None):
        """Lê input do terminal em modo raw, com suporte a movimento de cursor e quebra de linha.

        pre_clear_hook(text): chamado com o texto submetido, antes do _CLEAR.
        """
        sys.stdout.write(self._prompt())
        sys.stdout.write('\033[?2004h')
        sys.stdout.flush()

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        line = []
        cursor = 0
        buf = b''
        trow = 0  # linha real do cursor no terminal dentro do bloco
        hist_idx = len(self._history)
        draft = ''

        def _insert(c):
            nonlocal cursor, trow
            line.insert(cursor, c)
            cursor += 1
            if cursor == len(line):
                sys.stdout.write(c)
                sys.stdout.flush()
                cols = os.get_terminal_size().columns
                trow, _ = _visual_pos(self._vprompt(), line, cursor, cols)
            else:
                _redraw()

        def _redraw():
            nonlocal trow
            cols = os.get_terminal_size().columns
            vp = self._vprompt()
            crow, ccol = _visual_pos(vp, line, cursor, cols)
            erow, _    = _visual_pos(vp, line, len(line), cols)
            # sobe até o topo do bloco usando a posição real do terminal
            if trow > 0:
                sys.stdout.write(f'\033[{trow}A')
            sys.stdout.write('\r\033[J')
            sys.stdout.write(self._prompt())
            sys.stdout.write(''.join(line).replace('\n', '\r\n'))
            rdiff = erow - crow
            if rdiff > 0:
                sys.stdout.write(f'\033[{rdiff}A')
            sys.stdout.write('\r')
            if ccol > 0:
                sys.stdout.write(f'\033[{ccol}C')
            sys.stdout.flush()
            trow = crow

        def _submit():
            text = ''.join(line)
            if text.strip():
                self._history.append(text)
                _save_history(self._history)
            if pre_clear_hook:
                pre_clear_hook(text)
            cols     = os.get_terminal_size().columns
            mode_val = self._mode_ref[0].value
            erow, _  = _visual_pos(self._vprompt(), line, len(line), cols)
            if erow > trow:
                sys.stdout.write(f'\033[{erow - trow}B')
            sys.stdout.write('\r')
            if erow > 0:
                sys.stdout.write(f'\033[{erow}A')
            sys.stdout.write('\033[J')  # apaga o prompt da tela sem ir pro scrollback
            sys.stdout.write(CLEAR)    # empurra conteúdo anterior pro scrollback
            text_lines = text.split('\n')
            first = f"  ({mode_val}) >_  {text_lines[0]}"
            rem   = len(first) % cols
            sys.stdout.write(f'\033[J{BG_USER}{WHITE}{first}{" " * ((cols - rem) if rem else 0)}{RESET}\r\n')
            for ln in text_lines[1:]:
                label = f"  {ln}"
                rem   = len(label) % cols
                sys.stdout.write(f'{BG_USER}{WHITE}{label}{" " * ((cols - rem) if rem else 0)}{RESET}\r\n')
            sys.stdout.flush()
            return text

        def _handle_escape(rest):
            nonlocal cursor, hist_idx, draft, line
            if rest.startswith(b'[Z'):                                                    # Shift+Tab
                self._cycle_mode(modes)
                _redraw()
            elif rest in (b'\r', b'OM') or rest.startswith((b'[13;2u', b'[27;2;13~')):  # Alt+Enter
                line.insert(cursor, '\n')
                cursor += 1
                _redraw()
            elif rest.startswith(b'[C') and cursor < len(line):                          # →
                cursor += 1
                _redraw()
            elif rest.startswith(b'[D') and cursor > 0:                                  # ←
                cursor -= 1
                _redraw()
            elif rest.startswith((b'[H', b'[1~')) and cursor > 0:                        # Home
                cursor = 0
                _redraw()
            elif rest.startswith((b'[F', b'[4~')) and cursor < len(line):                # End
                cursor = len(line)
                _redraw()
            elif rest.startswith((b'[A', b'OA')) and self._history and hist_idx > 0:     # ↑
                if hist_idx == len(self._history):
                    draft = ''.join(line)
                hist_idx -= 1
                line   = list(self._history[hist_idx])
                cursor = len(line)
                _redraw()
            elif rest.startswith((b'[B', b'OB')) and hist_idx < len(self._history):      # ↓
                hist_idx += 1
                line   = list(draft if hist_idx == len(self._history) else self._history[hist_idx])
                cursor = len(line)
                _redraw()

        try:
            tty.setraw(fd)
            while True:
                b = os.read(fd, 1)

                if b in (b'\r', b'\n'):
                    return _submit()

                elif b == b'\x7f':  # backspace
                    if cursor > 0:
                        line.pop(cursor - 1)
                        cursor -= 1
                        _redraw()
                    buf = b''

                elif b == b'\x01':  # Ctrl+A — início
                    if cursor > 0:
                        cursor = 0
                        _redraw()
                    buf = b''

                elif b == b'\x05':  # Ctrl+E — fim
                    if cursor < len(line):
                        cursor = len(line)
                        _redraw()
                    buf = b''

                elif b == b'\x03':  # Ctrl+C
                    sys.stdout.write('^C\n')
                    sys.stdout.flush()
                    return ""

                elif b == b'\x04':  # Ctrl+D
                    if not line:
                        raise EOFError

                elif b == b'\x16':  # Ctrl+V — imagem do clipboard
                    result = _get_clipboard_image()
                    if result:
                        b64, mime = result
                        self.pending_image = {'data': b64, 'media_type': mime}
                        tag = list('[img]')
                        line[cursor:cursor] = tag
                        cursor += len(tag)
                        _redraw()
                    buf = b''

                elif b == b'\x1b':  # sequências de escape
                    ready, _, _ = select.select([sys.stdin], [], [], 0.05)
                    if ready:
                        rest = os.read(fd, 8)
                        if rest.startswith(_PASTE_START):  # bracketed paste
                            pasted = _parse_paste(lambda: os.read(fd, 4096), rest[len(_PASTE_START):])
                            line[cursor:cursor] = list(pasted)
                            cursor += len(pasted)
                            _redraw()
                        else:
                            _handle_escape(rest)
                    buf = b''

                else:
                    buf += b
                    try:
                        c = buf.decode('utf-8')
                    except UnicodeDecodeError:
                        if len(buf) < 4:
                            continue
                        c = buf.decode('utf-8', errors='replace')
                    _insert(c)
                    buf = b''

        finally:
            sys.stdout.write('\033[?2004l')
            sys.stdout.flush()
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

        return ''.join(line)
