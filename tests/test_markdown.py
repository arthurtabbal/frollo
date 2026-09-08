"""Testes para o renderer Markdown leve em lib/theme.py."""
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from lib.theme import MdBuffer, _md
from lib.runner.assistant_text import AssistantTextRenderer


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
OSC_RE = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)")


def _plain(text):
    return ANSI_RE.sub("", OSC_RE.sub("", text))


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

    def test_buffer_marca_flush_com_tabela(self):
        buf = MdBuffer()

        assert buf.feed("| Nome | Qtde |\n| --- | ---: |\n| maçã | 12 |\n\n") != ""

        assert buf.last_flush_had_table is True

    def test_buffer_nao_marca_tabela_dentro_de_code_block(self):
        buf = MdBuffer()

        assert buf.feed("```md\n| Nome | Qtde |\n| --- | ---: |\n```\n") != ""

        assert buf.last_flush_had_table is False

    def test_pipe_em_texto_comum_continua_texto(self):
        rendered = _plain(_md("use `cmd | jq` quando fizer sentido"))

        assert "cmd | jq" in rendered
        assert "-+-" not in rendered

    def test_link_em_tabela_conta_largura_renderizada(self):
        rendered = _plain(_md(
            "| Arquivo | Nota |\n"
            "|---|---|\n"
            "| [theme.py](/home/arthur/dev/frollo/bin/lib/theme.py:283) | renderer |\n"
        ))

        assert "[theme.py](" not in rendered
        assert "Arquivo      | Nota" in rendered
        assert "theme.py:283 | renderer" in rendered

    def test_tabela_quebra_celulas_quando_pane_e_estreito(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "58")
        rendered = _plain(_md(
            "| Achado da Q | Realidade |\n"
            "|---|---|\n"
            "| Senha hardcoded admin em code.sh:75, porta 8080 | "
            "Não há admin nem 8080 no módulo; a linha é comentário |\n"
        ))

        table_lines = [line for line in rendered.splitlines() if line.startswith("  ")]

        assert len(table_lines) > 4
        assert all(len(line) <= 58 for line in table_lines)
        assert "Senha hardcoded admin" in rendered
        assert "Não há admin nem 8080" in rendered


class TestMarkdownLinks:
    def test_link_externo_fica_legivel_no_terminal(self):
        raw = _md("Veja [Codex App Server](https://learn.chatgpt.com/docs/app-server).")
        rendered = _plain(raw)

        assert "\033]8;;https://learn.chatgpt.com/docs/app-server\033\\" in raw
        assert "[Codex App Server](" not in rendered
        assert "Codex App Server" in rendered
        assert "https://learn.chatgpt.com/docs/app-server" not in rendered

    def test_link_para_arquivo_local_encurta_para_nome_e_linha(self):
        raw = _md("Abra [codex.py](/home/arthur/dev/frollo/bin/lib/runner/codex.py:601).")
        rendered = _plain(raw)

        assert "\033]8;;file:///home/arthur/dev/frollo/bin/lib/runner/codex.py#L601\033\\" in raw
        assert "(/home/arthur" not in rendered
        assert "codex.py:601" in rendered
        assert "codex.py <codex.py:601>" not in rendered


class TestAssistantMarkdownTables:
    def test_tabela_no_finish_e_enfileirada_sem_typewriter(self):
        client = SimpleNamespace(_streaming_text=False, _last_response_text="")
        render = MagicMock()
        renderer = AssistantTextRenderer(client, {"typewriter": True}, render)

        renderer.push_delta(
            "| Item | Estado |\n"
            "|---|---:|\n"
            "| Markdown | OK |"
        )
        with patch("lib.runner.assistant_text.col_is_mid_line", return_value=False):
            renderer.finish(add_newline_if_mid_line=True)

        table_call = next(
            call for call in render.push_stdout.call_args_list
            if "-+-" in call.args[0]
        )
        assert table_call.kwargs["delay"] == 0

    def test_texto_normal_continua_com_typewriter(self):
        client = SimpleNamespace(_streaming_text=False, _last_response_text="")
        render = MagicMock()
        renderer = AssistantTextRenderer(client, {"typewriter": True}, render)

        renderer.push_delta("texto comum")

        assert render.push_stdout.call_args.kwargs["delay"] == 0.015
