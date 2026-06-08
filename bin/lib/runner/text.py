import re
import select
import sys

from ..typewriter import _char_delay

_ANSI_SEQ = re.compile(r'(\033\[[0-9;]*[mKJH])')

# Coluna atual no terminal — persiste entre chunks do mesmo bloco de texto.
# Não inserimos quebras de linha: deixamos o terminal fazer soft-wrap (reflui
# no resize, copia como linha única). _col só serve pra col_is_mid_line saber
# se terminamos no meio de uma linha (≠0) ou logo após um \n (==0).
_col = 0


def reset_col():
    global _col
    _col = 0


def col_is_mid_line():
    return _col != 0


def _advance_col(text):
    """Atualiza _col com o texto (visível) escrito: zera em \n, senão acumula."""
    global _col
    nl = text.rfind('\n')
    if nl >= 0:
        _col = len(text) - nl - 1
    else:
        _col += len(text)


def _typewrite(text, delay=0.015):
    global _col
    parts = _ANSI_SEQ.split(text)
    for i, part in enumerate(parts):
        if _ANSI_SEQ.match(part):
            sys.stdout.write(part)
            sys.stdout.flush()
        else:
            for j, char in enumerate(part):
                sys.stdout.write(char)
                sys.stdout.flush()
                _col = 0 if char == '\n' else _col + 1
                ready, _, _ = select.select([sys.stdin], [], [], _char_delay(char, delay))
                if ready:
                    sys.stdin.readline()
                    sys.stdout.write(part[j+1:])
                    sys.stdout.write(''.join(parts[i+1:]))
                    sys.stdout.flush()
                    # despejo de uma vez: recomputa _col do conteúdo visível restante
                    rest = _ANSI_SEQ.sub('', part[j+1:] + ''.join(parts[i+1:]))
                    _advance_col(rest)
                    return
