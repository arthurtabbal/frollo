"""Testes para lib/runner/permissions.py — protocolo de permissão e stdin fechado."""
import contextlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from lib.runner.permissions import (
    _answer_question_request,
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


def _can_use_tool(tool="Bash", inp=None, **extra):
    """Formato real do CLI (`--permission-prompt-tool stdio`): o pedido vem
    aninhado em `request`, não no topo do evento."""
    request = {"subtype": "can_use_tool", "tool_name": tool, "input": inp or {}}
    request.update(extra)
    return {"type": "control_request", "request_id": "req1", "request": request}


def _press(monkeypatch, key):
    fake_stdin = MagicMock()
    fake_stdin.fileno.return_value = 0
    monkeypatch.setattr("lib.runner.permissions._raw_stdin", _fake_raw_stdin)
    monkeypatch.setattr("lib.runner.permissions.sys.stdin", fake_stdin)
    monkeypatch.setattr("os.read", lambda fd, n: key)
    monkeypatch.setattr("lib.runner.permissions.config.load", lambda: {"gargoyles": False})


def _sent(proc):
    return json.loads(proc.stdin.write.call_args.args[0])


class TestHandleControlRequestStdinFechado:
    def test_stdin_fechado_trata_como_negado(self, monkeypatch, capsys):
        proc = MagicMock()
        proc.stdin.write.side_effect = ValueError("I/O operation on closed file")
        _press(monkeypatch, b"y")

        result = _handle_control_request(_can_use_tool(), proc, "/tmp")
        assert result is False


class TestHandleControlRequest:
    def test_permitir_responde_no_envelope_do_cli(self, monkeypatch, capsys):
        proc = MagicMock()
        _press(monkeypatch, b"y")
        inp = {"file_path": "/p/.claude/skills/x/SKILL.md", "content": "hi"}

        assert _handle_control_request(_can_use_tool("Write", inp), proc, "/tmp") is True
        assert _sent(proc) == {"type": "control_response", "response": {
            "subtype": "success", "request_id": "req1",
            "response": {"behavior": "allow", "updatedInput": inp},
        }}
        assert "Write" in capsys.readouterr().out

    def test_negar_manda_mensagem_ao_agente(self, monkeypatch):
        proc = MagicMock()
        _press(monkeypatch, b"n")

        assert _handle_control_request(_can_use_tool(), proc, "/tmp") is False
        inner = _sent(proc)["response"]["response"]
        assert inner["behavior"] == "deny"
        assert inner["message"]

    def test_mostra_motivo_do_pedido(self, monkeypatch, capsys):
        _press(monkeypatch, b"n")
        _handle_control_request(_can_use_tool(
            "Write", decision_reason="Claude requested permissions to write to /p/.claude/x",
        ), MagicMock(), "/tmp")
        assert "requested permissions to write" in capsys.readouterr().out

    def test_sempre_persiste_regra_sugerida_pelo_cli(self, monkeypatch, tmp_path):
        # Regressão: `"Write"` já estava no allowlist e mesmo assim a escrita em
        # `.claude/` era negada — só a regra sugerida (escopo do caminho) cobre.
        proc = MagicMock()
        _press(monkeypatch, b"a")
        suggestion = {"type": "addRules", "behavior": "allow", "destination": "session",
                      "rules": [{"toolName": "Edit", "ruleContent": "/.claude/skills/x/**"}]}

        _handle_control_request(_can_use_tool("Write", permission_suggestions=[suggestion]), proc, str(tmp_path))

        inner = _sent(proc)["response"]["response"]
        assert inner["behavior"] == "allow"
        assert inner["updatedPermissions"] == [dict(suggestion, destination="localSettings")]
        assert not (tmp_path / ".claude" / "settings.local.json").exists()

    def test_sempre_sem_sugestao_cai_no_allowlist_local(self, monkeypatch, tmp_path):
        proc = MagicMock()
        _press(monkeypatch, b"a")

        _handle_control_request(_can_use_tool("WebSearch"), proc, str(tmp_path))

        settings = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        assert settings["permissions"]["allow"] == ["WebSearch"]
        assert "updatedPermissions" not in _sent(proc)["response"]["response"]


class TestAnswerQuestionRequest:
    def test_responde_com_answers_por_texto_da_pergunta(self, monkeypatch):
        import io

        monkeypatch.setattr("lib.runner.permissions._cooked_stdin", _fake_raw_stdin)
        monkeypatch.setattr("lib.runner.permissions.sys.stdin", io.StringIO("2\n"))
        request = _can_use_tool("AskUserQuestion", {"questions": [{
            "question": "Qual cor?", "header": "Cor",
            "options": [{"label": "Vermelho"}, {"label": "Azul"}],
        }]})["request"]

        answers, response = _answer_question_request(request, normalize_user_questions(request["input"]))

        assert answers == {"Cor": ["Azul"]}
        assert response["behavior"] == "allow"
        assert response["updatedInput"]["answers"] == {"Qual cor?": "Azul"}
        assert response["updatedInput"]["questions"] == request["input"]["questions"]

    def test_cancelar_nega_a_tool(self, monkeypatch):
        import io

        monkeypatch.setattr("lib.runner.permissions._cooked_stdin", _fake_raw_stdin)
        monkeypatch.setattr("lib.runner.permissions.sys.stdin", io.StringIO("\n"))
        request = _can_use_tool("AskUserQuestion", {"question": "Continuar?"})["request"]

        answers, response = _answer_question_request(request, normalize_user_questions(request["input"]))

        assert answers is None
        assert response["behavior"] == "deny"


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
