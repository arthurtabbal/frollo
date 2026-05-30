"""Testes para log_tool_call — lógica de jump para o editor."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from lib.tools import log_tool_call


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Neutraliza todos os side effects de I/O em cada teste."""
    monkeypatch.setattr("lib.tools._clear_tools_pane", lambda: None)
    monkeypatch.setattr("lib.tools._log", lambda *a: None)
    # _entry vive em tools.display e chama _log no namespace de display —
    # patchar só lib.tools._log não cobre o caminho Edit/Write/Read/Bash.
    monkeypatch.setattr("lib.tools.display._log", lambda *a: None)
    monkeypatch.setattr("lib.tools._gargula_comment", lambda *a: ("", ""))
    monkeypatch.setattr("lib.tools.log_animated", lambda *a, **kw: None)
    monkeypatch.setattr("lib.tools._find_edit_line", lambda *a: None)


@pytest.fixture()
def mock_system(monkeypatch):
    m = MagicMock()
    monkeypatch.setattr("lib.tools.os.system", m)
    return m


# ── helpers ───────────────────────────────────────────────────────────────────


def _edit(fp="/tmp/foo.py", old_string=""):
    return {"name": "Edit", "input": {"file_path": fp, "old_string": old_string}}


def _write(fp="/tmp/foo.py"):
    return {"name": "Write", "input": {"file_path": fp}}


def _bash(cmd="ls", desc=""):
    return {"name": "Bash", "input": {"command": cmd, "description": desc}}


# ── editores que disparam o jump ───────────────────────────────────────────────


class TestEditoresVim:
    def test_nvim(self, mock_system):
        log_tool_call(_edit(), nvim_pane="pane_id", editor_bin="nvim")
        mock_system.assert_called_once()
        assert ":e" in mock_system.call_args[0][0]

    def test_vim(self, mock_system):
        log_tool_call(_edit(), nvim_pane="pane_id", editor_bin="vim")
        mock_system.assert_called_once()
        assert ":e" in mock_system.call_args[0][0]

    def test_nvim_path_absoluto(self, mock_system):
        log_tool_call(_edit(), nvim_pane="pane_id", editor_bin="/usr/bin/nvim")
        mock_system.assert_called_once()

    def test_vim_path_absoluto(self, mock_system):
        log_tool_call(_edit(), nvim_pane="pane_id", editor_bin="/usr/local/bin/vim")
        mock_system.assert_called_once()

    def test_write_block_tambem_faz_jump(self, mock_system):
        log_tool_call(_write(), nvim_pane="pane_id", editor_bin="nvim")
        mock_system.assert_called_once()


# ── editores que NÃO disparam o jump ─────────────────────────────────────────


class TestEditoresNaoVim:
    def test_nano(self, mock_system):
        log_tool_call(_edit(), nvim_pane="pane_id", editor_bin="nano")
        mock_system.assert_not_called()

    def test_helix(self, mock_system):
        log_tool_call(_edit(), nvim_pane="pane_id", editor_bin="hx")
        mock_system.assert_not_called()

    def test_code(self, mock_system):
        log_tool_call(_edit(), nvim_pane="pane_id", editor_bin="code")
        mock_system.assert_not_called()

    def test_editor_bin_vazio(self, mock_system):
        log_tool_call(_edit(), nvim_pane="pane_id", editor_bin="")
        mock_system.assert_not_called()


# ── condições que bloqueiam o jump mesmo com nvim ────────────────────────────


class TestCondicoesBloqueantes:
    def test_sem_pane(self, mock_system):
        log_tool_call(_edit(), nvim_pane="", editor_bin="nvim")
        mock_system.assert_not_called()

    def test_sem_filepath(self, mock_system):
        block = {"name": "Edit", "input": {"file_path": "", "old_string": ""}}
        log_tool_call(block, nvim_pane="pane_id", editor_bin="nvim")
        mock_system.assert_not_called()

    def test_bash_nao_faz_jump(self, mock_system):
        log_tool_call(_bash(), nvim_pane="pane_id", editor_bin="nvim")
        mock_system.assert_not_called()


# ── conteúdo do comando tmux ──────────────────────────────────────────────────


class TestComandoTmux:
    def test_filepath_incluido(self, mock_system):
        log_tool_call(_edit(fp="/home/user/main.py"), nvim_pane="pane_id", editor_bin="nvim")
        assert "/home/user/main.py" in mock_system.call_args[0][0]

    def test_tmux_srv_incluido(self, mock_system):
        log_tool_call(_edit(), nvim_pane="pane_id", tmux_srv="meu-srv", editor_bin="nvim")
        assert "-L 'meu-srv'" in mock_system.call_args[0][0]

    def test_sem_tmux_srv_sem_flag_L(self, mock_system):
        log_tool_call(_edit(), nvim_pane="pane_id", tmux_srv="", editor_bin="nvim")
        assert "-L" not in mock_system.call_args[0][0]

    def test_com_linha_inclui_mais_N(self, monkeypatch, mock_system):
        monkeypatch.setattr("lib.tools._find_edit_line", lambda *a: 42)
        log_tool_call(_edit(old_string="algo"), nvim_pane="pane_id", editor_bin="nvim")
        assert "+42" in mock_system.call_args[0][0]

    def test_sem_linha_nao_inclui_mais(self, mock_system):
        log_tool_call(_edit(), nvim_pane="pane_id", editor_bin="nvim")
        cmd = mock_system.call_args[0][0]
        assert "+None" not in cmd
        assert "+0" not in cmd
