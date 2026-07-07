"""Testes unitários para bin/lib/input.py."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from unittest.mock import MagicMock
import lib.input as input_mod
from lib.input import _visual_pos, InputReader, _parse_paste


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
    def _reader(self, monkeypatch):
        # isola HISTORY_PATH num arquivo temporário — não deve ler nem escrever
        # no histórico real do usuário durante os testes.
        monkeypatch.setattr(input_mod, "HISTORY_PATH", Path(tempfile.mkdtemp()) / "history.json")
        mode = MagicMock()
        mode.value = "normal"
        return InputReader([mode])

    def test_history_vazio_inicial(self, monkeypatch):
        r = self._reader(monkeypatch)
        assert r._history == []

    def test_history_persiste_em_disco(self, monkeypatch):
        history_path = Path(tempfile.mkdtemp()) / "history.json"
        monkeypatch.setattr(input_mod, "HISTORY_PATH", history_path)

        r1 = InputReader([MagicMock(value="normal")])
        r1._history.append("mensagem salva")
        input_mod._save_history(r1._history)

        r2 = InputReader([MagicMock(value="normal")])
        assert r2._history == ["mensagem salva"]

    def test_history_carrega_vazio_se_arquivo_corrompido(self, monkeypatch):
        history_path = Path(tempfile.mkdtemp()) / "history.json"
        history_path.write_text("não é json válido")
        monkeypatch.setattr(input_mod, "HISTORY_PATH", history_path)

        r = InputReader([MagicMock(value="normal")])
        assert r._history == []

    def test_history_acumula_apos_envio(self, monkeypatch):
        r = self._reader(monkeypatch)
        r._history.append("primeira mensagem")
        r._history.append("segunda mensagem")
        assert len(r._history) == 2
        assert r._history[0] == "primeira mensagem"

    def test_history_persiste_entre_turnos(self, monkeypatch):
        r = self._reader(monkeypatch)
        r._history.append("turno 1")
        r._history.append("turno 2")
        # simula novo read_input — hist_idx começa no fim
        hist_idx = len(r._history)
        assert hist_idx == 2

    def test_navegacao_para_tras(self, monkeypatch):
        r = self._reader(monkeypatch)
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

    def test_navegacao_para_frente_restaura_draft(self, monkeypatch):
        r = self._reader(monkeypatch)
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

    def test_navegacao_nao_vai_abaixo_do_fim(self, monkeypatch):
        r = self._reader(monkeypatch)
        r._history = ["msg1"]
        hist_idx = len(r._history)  # 1 — já no fim

        # ↓ não deve fazer nada (hist_idx >= len)
        assert hist_idx >= len(r._history)

    def test_navegacao_nao_vai_acima_do_inicio(self, monkeypatch):
        r = self._reader(monkeypatch)
        r._history = ["msg1"]
        hist_idx = 0  # já no início

        # ↑ não deve fazer nada
        assert not (r._history and hist_idx > 0)


class TestParsePaste:
    """Parser do bracketed paste (\\x1b[200~ ... \\x1b[201~), isolado do I/O real
    pra testar com bytes sintéticos (item 5.1)."""

    def _reader_from_chunks(self, chunks):
        it = iter(chunks)
        return lambda: next(it, b'')

    def test_conteudo_simples_terminador_no_mesmo_chunk(self):
        read_more = self._reader_from_chunks([b'ola mundo\x1b[201~'])
        assert _parse_paste(read_more) == 'ola mundo'

    def test_prefix_ja_contem_terminador(self):
        # rest[len(_PASTE_START):] pode já trazer conteúdo + terminador no mesmo os.read
        assert _parse_paste(self._reader_from_chunks([]), prefix=b'abc\x1b[201~') == 'abc'

    def test_terminador_dividido_entre_chunks(self):
        read_more = self._reader_from_chunks([b'abc\x1b[201', b'~'])
        assert _parse_paste(read_more) == 'abc'

    def test_multiplos_chunks_antes_do_terminador(self):
        read_more = self._reader_from_chunks([b'linha1\n', b'linha2\n', b'fim\x1b[201~'])
        assert _parse_paste(read_more) == 'linha1\nlinha2\nfim'

    def test_newline_preservado_como_texto_literal(self):
        read_more = self._reader_from_chunks([b'a\nb\nc\x1b[201~'])
        assert _parse_paste(read_more) == 'a\nb\nc'

    def test_eof_sem_terminador_nao_trava(self):
        read_more = self._reader_from_chunks([b'sem fim'])
        assert _parse_paste(read_more) == 'sem fim'

    def test_utf8_multibyte(self):
        read_more = self._reader_from_chunks(['café ☕'.encode('utf-8') + b'\x1b[201~'])
        assert _parse_paste(read_more) == 'café ☕'

    def test_cr_lone_normalizado(self):
        # paste do tmux (paste-buffer) usa `\r` como quebra — deve virar `\n`
        read_more = self._reader_from_chunks([b'linha1\rlinha2\rfim\x1b[201~'])
        assert _parse_paste(read_more) == 'linha1\nlinha2\nfim'

    def test_crlf_normalizado(self):
        read_more = self._reader_from_chunks([b'a\r\nb\r\nc\x1b[201~'])
        assert _parse_paste(read_more) == 'a\nb\nc'

    def test_cr_dividido_entre_chunks_nao_vira_dupla_quebra(self):
        # `\r\n` partido no meio de dois os.read não pode virar `\n\n`
        read_more = self._reader_from_chunks([b'a\r', b'\nb\x1b[201~'])
        assert _parse_paste(read_more) == 'a\nb'


class TestPromptProvider:
    """prompt_provider permite ao chamador (ClaudeClient) incluir badges extras
    (ex: modelo) no prompt; _vprompt deve refletir a largura visual real."""

    def test_sem_provider_usa_default(self):
        mode = MagicMock(value="normal")
        r = InputReader([mode])
        assert r._vprompt() == "(normal) >_ "

    def test_com_provider_usa_texto_do_provider(self):
        mode = MagicMock(value="normal")
        r = InputReader([mode], prompt_provider=lambda: "\x1b[35msonnet\x1b[0m (normal) >_ ")
        assert r._vprompt() == "sonnet (normal) >_ "

    def test_vprompt_muda_com_o_provider_mesmo_modo(self):
        """Badge de modelo muda a largura mesmo sem o modo mudar — cursor tem que
        acompanhar, senão desalinha (bug que este item corrige)."""
        mode = MagicMock(value="normal")
        r = InputReader([mode], prompt_provider=lambda: "\x1b[35mopus\x1b[0m (normal) >_ ")
        vprompt_curto = r._vprompt()
        r._prompt_provider = lambda: "\x1b[35mclaude-opus-4-5-longuinho\x1b[0m (normal) >_ "
        vprompt_longo = r._vprompt()
        assert len(vprompt_longo) > len(vprompt_curto)
