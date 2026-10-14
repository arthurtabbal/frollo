"""Testes para lib/runner/permissions.py — protocolo de permissão e stdin fechado."""
import contextlib
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from lib.runner.permissions import (
    _handle_control_request,
    _handle_user_question,
    _write_stdin,
    normalize_user_questions,
)


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


class TestStructuredQuestions:
    def test_normaliza_schema_do_ask_user_question(self):
        questions = normalize_user_questions({"questions": [{
            "header": "Estratégia",
            "question": "Como seguimos?",
            "options": [
                {"label": "Incremental", "description": "entrega por etapas"},
                {"label": "Completa", "description": "faz tudo agora"},
            ],
            "multiSelect": False,
        }]})

        assert questions == [{
            "id": "Estratégia",
            "header": "Estratégia",
            "question": "Como seguimos?",
            "options": [
                {"label": "Incremental", "description": "entrega por etapas"},
                {"label": "Completa", "description": "faz tudo agora"},
            ],
            "multiple": False,
            "secret": False,
            "allow_other": False,
        }]

    def test_picker_retorna_rotulos_de_selecao_multipla(self, monkeypatch):
        import io

        monkeypatch.setattr("lib.runner.permissions._cooked_stdin", _fake_raw_stdin)
        monkeypatch.setattr("lib.runner.permissions.sys.stdin", io.StringIO("1,3\n"))
        answer = _handle_user_question("AskUserQuestion", {"questions": [{
            "question": "Quais entram?",
            "options": ["A", "B", "C"],
            "multiSelect": True,
        }]})

        assert answer == "A, C"

    def test_picker_vazio_cancela(self, monkeypatch):
        import io

        monkeypatch.setattr("lib.runner.permissions._cooked_stdin", _fake_raw_stdin)
        monkeypatch.setattr("lib.runner.permissions.sys.stdin", io.StringIO("\n"))
        assert _handle_user_question("AskUserQuestion", {"question": "Continuar?"}) is None

    def test_texto_livre_preserva_virgulas(self, monkeypatch):
        import io
        from lib.runner.permissions import ask_user_questions

        monkeypatch.setattr("lib.runner.permissions._cooked_stdin", _fake_raw_stdin)
        monkeypatch.setattr("lib.runner.permissions.sys.stdin", io.StringIO("sim, mas depois\n"))
        answers = ask_user_questions("request_user_input", normalize_user_questions({
            "questions": [{"id": "detail", "question": "Algum detalhe?"}],
        }))

        assert answers == {"detail": ["sim, mas depois"]}

    def test_picker_oferece_outro_com_resposta_livre(self, monkeypatch):
        import io
        from lib.runner.permissions import ask_user_questions

        monkeypatch.setattr("lib.runner.permissions._cooked_stdin", _fake_raw_stdin)
        monkeypatch.setattr("lib.runner.permissions.sys.stdin", io.StringIO("2\nMinha opção\n"))
        questions = normalize_user_questions({"questions": [{
            "id": "choice",
            "question": "Qual?",
            "options": ["Padrão"],
            "isOther": True,
        }]})

        assert ask_user_questions("request_user_input", questions) == {
            "choice": ["Minha opção"],
        }

    def test_texto_secreto_nao_usa_readline(self, monkeypatch):
        from lib.runner.permissions import ask_user_questions

        monkeypatch.setattr("lib.runner.permissions._cooked_stdin", _fake_raw_stdin)
        monkeypatch.setattr("lib.runner.permissions.getpass.getpass", lambda prompt: "segredo")
        questions = normalize_user_questions({"questions": [{
            "id": "token", "question": "Token?", "isSecret": True,
        }]})

        assert ask_user_questions("request_user_input", questions) == {"token": ["segredo"]}
