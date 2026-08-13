"""Testes para o renderer Markdown leve em lib/theme.py."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from lib.theme import MdBuffer, _md


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _plain(text):
    return ANSI_RE.sub("", text)


class TestMarkdownTables:
    def test_pipe_table_vira_tabela_alinhada(self):
        rendered = _plain(_md("| Nome | Qtde |\n| --- | ---: |\n| maçã | 12 |\n| figo | 3 |\n"))

        assert "| --- |" not in rendered
        assert "Nome | Qtde" in rendered
        assert "-----+-----" in rendered
        assert "maçã |   12" in rendered
        assert "figo |    3" in rendered

    def test_buffer_segura_table_ate_terminador(self):
        buf = MdBuffer()

        assert buf.feed("| Nome | Qtde |\n") == ""
        assert buf.feed("| --- | ---: |\n") == ""
        assert buf.feed("| maçã | 12 |\n\n") != ""

    def test_buffer_segura_table_mesmo_com_chunks_no_meio_da_linha(self):
        buf = MdBuffer()

        assert buf.feed("| Nome | Q") == ""
        assert buf.feed("tde |\n| ---") == ""
        assert buf.feed(" | ---: |\n| maç") == ""
        rendered = _plain(buf.feed("ã | 12 |\n\n"))

        assert "| --- |" not in rendered
        assert "-----+-----" in rendered
        assert "maçã |   12" in rendered

    def test_buffer_segura_separador_quebrado_em_linha_que_parece_completa(self):
        buf = MdBuffer()

        assert buf.feed("| Nome | Qtde |\n|---|") == ""
        rendered = _plain(buf.feed("---:|\n| maçã | 12 |\n\n"))

        assert "|---|" not in rendered
        assert "-----+-----" in rendered
        assert "maçã |   12" in rendered

    def test_pipe_em_texto_comum_continua_texto(self):
        rendered = _plain(_md("use `cmd | jq` quando fizer sentido"))

        assert "cmd | jq" in rendered
        assert "-+-" not in rendered
