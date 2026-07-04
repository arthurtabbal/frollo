"""Testes para ClaudeClient — comportamento com dependências ausentes."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from chat import ClaudeClient
from lib.theme import DIM, RESET, WHITE


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr("chat.RUNDIR", tmp_path)
    monkeypatch.setattr("chat.THINKING_LOG", tmp_path / "thinking")
    monkeypatch.setattr("chat.TOOLS_LOG", tmp_path / "tools")
    (tmp_path / "thinking").write_text("")
    (tmp_path / "tools").write_text("")


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr("lib.input.InputReader.__init__", lambda self, *a, **kw: None)
    c = ClaudeClient.__new__(ClaudeClient)
    c.resume_id = None
    c.session_id = None
    c.first_turn = True
    c.mode = MagicMock()
    c.cwd = "/tmp"
    c.nvim_pane = ""
    c.tmux_srv = ""
    c.editor_bin = ""
    c.proc = None
    c._streaming_text = False
    c._mode_ref = [c.mode]
    return c


class TestPasteEditorAusente:
    def test_retorna_none_quando_editor_ausente(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("EDITOR", "editor-que-nao-existe-xyz")
        monkeypatch.setattr("chat.RUNDIR", tmp_path)
        with patch("chat.subprocess.call", side_effect=FileNotFoundError):
            result = client._paste()
        assert result is None

    def test_exibe_mensagem_com_nome_do_editor(self, client, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("EDITOR", "meu-editor-magico")
        monkeypatch.setattr("chat.RUNDIR", tmp_path)
        with patch("chat.subprocess.call", side_effect=FileNotFoundError):
            client._paste()
        out = capsys.readouterr().out
        assert "meu-editor-magico" in out


class TestPromptBadgeDeModelo:
    """Badge de modelo no prompt (item 1.7 do plano) — ClaudeClient._prompt é
    religado como prompt_provider do InputReader, em vez de código morto."""

    def test_input_reader_recebe_prompt_provider(self):
        c = ClaudeClient()
        assert c._input_reader._prompt_provider == c._prompt

    def test_prompt_inclui_badge_de_modelo_quando_definido(self):
        c = ClaudeClient(model="opus")
        assert "opus" in c._prompt()

    def test_prompt_sem_modelo_nao_inclui_badge(self):
        c = ClaudeClient()
        c.observed_model = ""
        assert c._prompt() == f"{DIM}normal{RESET} {WHITE}>_{RESET} "

    def test_prompt_reflete_modo_atual_via_mode_ref(self):
        from chat import Mode
        c = ClaudeClient()
        c._mode_ref[0] = Mode.AUTO
        assert "auto" in c._prompt()
