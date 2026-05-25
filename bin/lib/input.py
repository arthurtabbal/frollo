import base64
import os
import select
import subprocess
import sys
import termios
import tty

from .theme import DIM, RESET, GREEN, WHITE, BG_USER, CLEAR


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
    def __init__(self, mode_ref):
        """mode_ref: a mutable container [Mode] so we can cycle mode from outside."""
        self._mode_ref = mode_ref
        self._history: list[str] = []
        self.pending_image = None  # {'data': b64str, 'media_type': str}

    def _prompt(self):
        mode = self._mode_ref[0]
        if mode.value == "auto":
            badge = f"{GREEN}auto{RESET}"
        else:
            badge = f"{DIM}normal{RESET}"
        return f"{WHITE}({RESET}{badge}{WHITE}){RESET} {WHITE}>_{RESET} "

    def _vprompt(self):
        return f"({self._mode_ref[0].value}) >_ "

    def _cycle_mode(self, modes):
        idx = modes.index(self._mode_ref[0])
        self._mode_ref[0] = modes[(idx + 1) % len(modes)]

    def read_input(self, modes, pre_clear_hook=None):
        """Lê input do terminal em modo raw, com suporte a movimento de cursor e quebra de linha.

        pre_clear_hook(text): chamado com o texto submetido, antes do _CLEAR.
        Útil para capturar estado do terminal enquanto a resposta anterior ainda está visível.
        """
        sys.stdout.write(self._prompt())
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

        try:
            tty.setraw(fd)
            while True:
                b = os.read(fd, 1)

                if b in (b'\r', b'\n'):
                    text = ''.join(line)
                    if text.strip():
                        self._history.append(text)
                    if pre_clear_hook:
                        pre_clear_hook(text)
                    cols = os.get_terminal_size().columns
                    vp = self._vprompt()
                    mode_val = self._mode_ref[0].value
                    erow, _ = _visual_pos(vp, line, len(line), cols)
                    # move para o fim do bloco
                    if erow > trow:
                        sys.stdout.write(f'\033[{erow - trow}B')
                    sys.stdout.write('\r')
                    if erow > 0:
                        sys.stdout.write(f'\033[{erow}A')
                    sys.stdout.write('\033[J')  # apaga o prompt da tela sem ir pro scrollback
                    sys.stdout.write(CLEAR)    # empurra conteúdo anterior pro scrollback
                    text_lines = text.split('\n')
                    first = f"  ({mode_val}) >_  {text_lines[0]}"
                    rem = len(first) % cols
                    sys.stdout.write(f'\033[J{BG_USER}{WHITE}{first}{" " * ((cols - rem) if rem else 0)}{RESET}\r\n')
                    for ln in text_lines[1:]:
                        label = f"  {ln}"
                        rem = len(label) % cols
                        sys.stdout.write(f'{BG_USER}{WHITE}{label}{" " * ((cols - rem) if rem else 0)}{RESET}\r\n')
                    sys.stdout.flush()
                    return text

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
                        for ch in '[img]':
                            line.insert(cursor, ch)
                            cursor += 1
                        _redraw()
                    buf = b''

                elif b == b'\x1b':  # sequências de escape
                    ready, _, _ = select.select([sys.stdin], [], [], 0.05)
                    if ready:
                        rest = os.read(fd, 8)
                        if rest.startswith(b'[Z'):    # Shift+Tab
                            self._cycle_mode(modes)
                            _redraw()
                        elif rest in (b'\r', b'OM') or rest.startswith(b'[13;2u') or rest.startswith(b'[27;2;13~'):  # Shift+Enter / Alt+Enter
                            line.insert(cursor, '\n')
                            cursor += 1
                            _redraw()
                        elif rest.startswith(b'[C'):  # →
                            if cursor < len(line):
                                cursor += 1
                                _redraw()
                        elif rest.startswith(b'[D'):  # ←
                            if cursor > 0:
                                cursor -= 1
                                _redraw()
                        elif rest.startswith(b'[H') or rest.startswith(b'[1~'):  # Home
                            if cursor > 0:
                                cursor = 0
                                _redraw()
                        elif rest.startswith(b'[F') or rest.startswith(b'[4~'):  # End
                            if cursor < len(line):
                                cursor = len(line)
                                _redraw()
                        elif rest.startswith(b'[A') or rest.startswith(b'OA'):  # ↑ — histórico anterior
                            if self._history and hist_idx > 0:
                                if hist_idx == len(self._history):
                                    draft = ''.join(line)
                                hist_idx -= 1
                                line = list(self._history[hist_idx])
                                cursor = len(line)
                                _redraw()
                        elif rest.startswith(b'[B') or rest.startswith(b'OB'):  # ↓ — histórico seguinte
                            if hist_idx < len(self._history):
                                hist_idx += 1
                                line = list(draft if hist_idx == len(self._history) else self._history[hist_idx])
                                cursor = len(line)
                                _redraw()
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
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

        return ''.join(line)
