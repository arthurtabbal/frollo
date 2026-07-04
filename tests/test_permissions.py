"""Testes para lib/runner/permissions.py — protocolo de permissão e stdin fechado."""
import contextlib
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from lib.runner.permissions import _write_stdin, _handle_control_request


class TestWriteStdin:
    def test_sucesso_retorna_true(self):
        proc = MagicMock()
        assert _write_stdin(proc, "y\n") is True
        proc.stdin.write.assert_called_once_with("y\n")
        proc.stdin.flush.assert_called_once()

    def test_stdin_fechado_value_error_retorna_false(self, capsys):
        proc = MagicMock()
        proc.stdin.write.side_effect = ValueError("I/O operation on closed file")
        assert _write_stdin(proc, "y\n") is False
        assert "fechado" in capsys.readouterr().out

    def test_stdin_fechado_broken_pipe_retorna_false(self, capsys):
        proc = MagicMock()
        proc.stdin.write.side_effect = BrokenPipeError()
        assert _write_stdin(proc, "y\n") is False


class TestHandleControlRequestStdinFechado:
    def test_stdin_fechado_trata_como_negado(self, monkeypatch, capsys):
        proc = MagicMock()
        proc.stdin.write.side_effect = ValueError("I/O operation on closed file")

        event = {"request_id": "req1", "tool_name": "Bash", "input": {}}

        fake_stdin = MagicMock()
        fake_stdin.fileno.return_value = 0
        monkeypatch.setattr("lib.runner.permissions._raw_stdin", _fake_raw_stdin)
        monkeypatch.setattr("lib.runner.permissions.sys.stdin", fake_stdin)
        monkeypatch.setattr("os.read", lambda fd, n: b"y")
        monkeypatch.setattr("lib.runner.permissions.config.load", lambda: {"gargoyles": False})

        result = _handle_control_request(event, proc, "/tmp")
        assert result is False


@contextlib.contextmanager
def _fake_raw_stdin():
    yield
