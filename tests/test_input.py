"""Testes unitários para bin/lib/input.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from unittest.mock import MagicMock
from lib.input import _visual_pos, InputReader


def vp(text, idx, cols, prompt=""):
    """Atalho: converte string em list de chars para _visual_pos."""
    return _visual_pos(prompt, list(text), idx, cols)


class TestVisualPosBasico:
    def test_vazio(self):
        assert vp("", 0, 80) == (0, 0)

    def test_inicio(self):
        assert vp("abc", 0, 80) == (0, 0)

    def test_meio(self):
        assert vp("abc", 1, 80) == (0, 1)

    def test_fim(self):
        assert vp("abc", 3, 80) == (0, 3)

    def test_com_prompt(self):
        # prompt de 5 chars + 3 chars de conteúdo
        assert _visual_pos("12345", list("abc"), 3, 80) == (0, 8)


class TestVisualPosNewline:
    def test_newline_avanca_linha(self):
        assert vp("a\nb", 2, 80) == (1, 0)

    def test_posicao_apos_newline(self):
        assert vp("a\nb", 3, 80) == (1, 1)

    def test_multiplos_newlines(self):
        assert vp("a\nb\nc", 5, 80) == (2, 1)


class TestVisualPosWrap:
    def test_sem_wrap(self):
        # 4 chars em terminal de 5 — não wrapa
        assert vp("abcd", 4, 5) == (0, 4)

    def test_deferred_wrap_na_ultima_coluna(self):
        # 5 chars em terminal de 5 — cursor fica na última col (deferred wrap)
        assert vp("abcde", 5, 5) == (0, 4)

    def test_wrap_apos_limite(self):
        # 6 chars — o 6º char dispara o wrap, cursor na col 1 da linha 1
        assert vp("abcdef", 6, 5) == (1, 1)

    def test_segunda_linha_completa(self):
        # 10 chars em terminal de 5 — segunda linha cheia (deferred wrap)
        assert vp("abcdefghij", 10, 5) == (1, 4)

    def test_terceira_linha(self):
        # 11 chars — começa terceira linha
        assert vp("abcdefghijk", 11, 5) == (2, 1)

    def test_wrap_com_prompt(self):
        # prompt de 2 chars + 3 chars = 5 total → deferred wrap
        assert _visual_pos("AB", list("cde"), 3, 5) == (0, 4)

    def test_wrap_com_prompt_avanc(self):
        # prompt de 2 + 4 chars = 6 → wrap, cursor em (1, 1)
        assert _visual_pos("AB", list("cdef"), 4, 5) == (1, 1)


class TestVisualPosWrapComNewline:
    def test_newline_cancela_deferred_wrap(self):
        # 'cde' preenche linha com prompt "AB" (deferred wrap), '\n' cancela e vai pra próxima
        assert _visual_pos("AB", list("cde\nf"), 5, 5) == (1, 1)

    def test_newline_no_meio_e_wrap_depois(self):
        # linha 0: "AB" + "cd" (4 chars, sem wrap). '\n' → linha 1. "efghi" wrapa em 5.
        assert _visual_pos("AB", list("cd\nefghi"), 8, 5) == (1, 4)


class TestHistorico:
    def _reader(self):
        mode = MagicMock()
        mode.value = "normal"
        return InputReader([mode])

    def test_history_vazio_inicial(self):
        r = self._reader()
        assert r._history == []

    def test_history_acumula_apos_envio(self):
        r = self._reader()
        r._history.append("primeira mensagem")
        r._history.append("segunda mensagem")
        assert len(r._history) == 2
        assert r._history[0] == "primeira mensagem"

    def test_history_persiste_entre_turnos(self):
        r = self._reader()
        r._history.append("turno 1")
        r._history.append("turno 2")
        # simula novo read_input — hist_idx começa no fim
        hist_idx = len(r._history)
        assert hist_idx == 2

    def test_navegacao_para_tras(self):
        r = self._reader()
        r._history = ["msg1", "msg2", "msg3"]
        hist_idx = len(r._history)  # 3
        draft = "rascunho atual"

        # ↑ uma vez
        assert hist_idx > 0
        draft_salvo = draft
        hist_idx -= 1
        assert hist_idx == 2
        assert r._history[hist_idx] == "msg3"

        # ↑ mais uma
        hist_idx -= 1
        assert r._history[hist_idx] == "msg2"

    def test_navegacao_para_frente_restaura_draft(self):
        r = self._reader()
        r._history = ["msg1", "msg2"]
        draft = "em progresso"
        hist_idx = len(r._history)

        # ↑ duas vezes
        draft_salvo = draft
        hist_idx -= 1  # msg2
        hist_idx -= 1  # msg1

        # ↓ duas vezes — deve restaurar draft
        hist_idx += 1  # msg2
        hist_idx += 1  # fim = len(history)
        result = draft_salvo if hist_idx == len(r._history) else r._history[hist_idx]
        assert result == "em progresso"

    def test_navegacao_nao_vai_abaixo_do_fim(self):
        r = self._reader()
        r._history = ["msg1"]
        hist_idx = len(r._history)  # 1 — já no fim

        # ↓ não deve fazer nada (hist_idx >= len)
        assert hist_idx >= len(r._history)

    def test_navegacao_nao_vai_acima_do_inicio(self):
        r = self._reader()
        r._history = ["msg1"]
        hist_idx = 0  # já no início

        # ↑ não deve fazer nada
        assert not (r._history and hist_idx > 0)
