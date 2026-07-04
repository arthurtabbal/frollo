"""Testes para lib/runner/text.py — _typewrite e skip por qualquer tecla."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from lib.runner.text import _typewrite, reset_col, col_is_mid_line

_fake_stdin = MagicMock()
_fake_stdin.fileno.return_value = 0


class TestTypewriteSkip:
    def test_sem_tecla_escreve_char_a_char(self, capsys):
        reset_col()
        with patch("lib.runner.text.select.select", return_value=([], [], [])):
            _typewrite("abc", delay=0)
        assert capsys.readouterr().out == "abc"

    def test_qualquer_tecla_despeja_o_resto_de_uma_vez(self, capsys):
        """Item 1.8: com ICANON desligado, uma tecla qualquer (não só Enter) acorda
        o select — a leitura usa os.read, não mais sys.stdin.readline()."""
        reset_col()
        calls = {"n": 0}

        def _fake_select(*a, **k):
            calls["n"] += 1
            # primeiro char já "acorda" o select (tecla pressionada)
            return ([_fake_stdin], [], []) if calls["n"] == 1 else ([], [], [])

        with patch("lib.runner.text.select.select", side_effect=_fake_select), \
             patch("lib.runner.text.sys.stdin", _fake_stdin), \
             patch("lib.runner.text.os.read", return_value=b"x") as mock_read:
            _typewrite("abcde", delay=0)

        mock_read.assert_called_once()
        assert capsys.readouterr().out == "abcde"

    def test_col_apos_skip_reflete_texto_visivel_restante(self, capsys):
        reset_col()
        with patch("lib.runner.text.select.select", side_effect=[([_fake_stdin], [], [])]), \
             patch("lib.runner.text.sys.stdin", _fake_stdin), \
             patch("lib.runner.text.os.read", return_value=b"x"):
            _typewrite("ab\ncd", delay=0)
        assert col_is_mid_line()  # terminou em "cd", não em \n
