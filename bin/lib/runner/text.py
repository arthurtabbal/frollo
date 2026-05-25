import os
import re
import select
import sys

from ..typewriter import _char_delay

_ANSI_SEQ = re.compile(r'(\033\[[0-9;]*[mKJH])')

# Coluna atual no terminal — persiste entre chunks do mesmo bloco de texto.
# Compartilhado entre _wrap_text e _typewrite via global de módulo.
_col = 0


def reset_col():
    global _col
    _col = 0


def col_is_mid_line():
    return _col != 0


def _wrap_text(text, width):
    """Insere quebras de linha em fronteiras de palavras. Atualiza _col como side effect."""
    global _col
    result = []
    col = _col
    for token in re.split(r'(\s+)', text):
        if not token:
            continue
        if '\n' in token:
            result.append(token)
            col = len(token) - token.rfind('\n') - 1
        elif token.isspace():
            if col + len(token) > width:
                result.append('\n')
                col = 0
            else:
                result.append(token)
                col += len(token)
        else:
            if col > 0 and col + len(token) > width:
                result.append('\n')
                col = 0
            result.append(token)
            col += len(token)
    _col = col
    return ''.join(result)


def _typewrite(text, delay=0.015, wrap=True):
    global _col
    try:
        width = os.get_terminal_size().columns - 1
    except OSError:
        width = 89
    parts = _ANSI_SEQ.split(text)
    for i, part in enumerate(parts):
        if _ANSI_SEQ.match(part):
            sys.stdout.write(part)
            sys.stdout.flush()
        else:
            body = _wrap_text(part, width) if wrap else part
            final_col = _col
            for j, char in enumerate(body):
                if char == '\n':
                    _col = 0
                sys.stdout.write(char)
                sys.stdout.flush()
                ready, _, _ = select.select([sys.stdin], [], [], _char_delay(char, delay))
                if ready:
                    sys.stdin.readline()
                    sys.stdout.write(body[j+1:])
                    sys.stdout.write(''.join(parts[i+1:]))
                    sys.stdout.flush()
                    _col = final_col
                    return
            _col = final_col
