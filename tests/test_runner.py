"""Testes para run_turn — comportamento com dependências ausentes."""
import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from lib.runner import run_turn


@pytest.fixture()
def fake_client():
    client = MagicMock()
    client.mode.value = "normal"
    client.first_turn = True
    client.resume_id = None
    client.nvim_pane = ""
    client.tmux_srv = ""
    client.editor_bin = ""
    client.cwd = "/tmp"
    client._streaming_text = False
    return client


class TestClaudeNaoEncontrado:
    def test_retorna_false_quando_claude_ausente(self, fake_client):
        with patch("lib.runner.subprocess.Popen", side_effect=FileNotFoundError):
            result = run_turn(fake_client, "oi")
        assert result is False

    def test_exibe_mensagem_amigavel(self, fake_client, capsys):
        with patch("lib.runner.subprocess.Popen", side_effect=FileNotFoundError):
            run_turn(fake_client, "oi")
        out = capsys.readouterr().out
        assert "claude" in out.lower()
        assert "npm" in out or "não encontrado" in out
