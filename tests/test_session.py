"""Testes para lib/session.py — parsing de sessões nos dois schemas de jsonl."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from lib.session import _load_sessions, _first_user_text, pick_session


def _write_jsonl(path, events):
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


class TestFirstUserText:
    def test_schema_antigo_queue_operation(self):
        ev = {"type": "queue-operation", "operation": "enqueue", "content": "oi tudo bem"}
        assert _first_user_text(ev) == "oi tudo bem"

    def test_schema_atual_content_string(self):
        ev = {"type": "user", "message": {"content": "corrige o bug"}}
        assert _first_user_text(ev) == "corrige o bug"

    def test_schema_atual_content_lista_de_blocos(self):
        ev = {"type": "user", "message": {"content": [
            {"type": "text", "text": "implementa a feature"},
        ]}}
        assert _first_user_text(ev) == "implementa a feature"

    def test_evento_meta_nao_vira_preview(self):
        ev = {
            "type": "user",
            "isMeta": True,
            "message": {"content": "texto interno do cli"},
        }
        assert _first_user_text(ev) == ""

    def test_caveat_de_comando_local_nao_vira_preview(self):
        ev = {
            "type": "user",
            "message": {
                "content": "<local-command-caveat>Caveat: The messages below were generated</local-command-caveat>"
            },
        }
        assert _first_user_text(ev) == ""

    def test_comando_local_nao_vira_preview(self):
        ev = {
            "type": "user",
            "message": {
                "content": "<command-name>/clear</command-name>\n"
                           "            <command-message>clear</command-message>\n"
                           "            <command-args></command-args>"
            },
        }
        assert _first_user_text(ev) == ""

    def test_evento_nao_reconhecido_retorna_vazio(self):
        assert _first_user_text({"type": "assistant"}) == ""


class TestLoadSessions:
    def test_schema_antigo(self, tmp_path):
        _write_jsonl(tmp_path / "session-a.jsonl", [
            {"type": "queue-operation", "operation": "enqueue", "content": "primeira mensagem"},
        ])
        sessions = _load_sessions(tmp_path)
        assert len(sessions) == 1
        assert sessions[0][0] == "session-a"
        assert sessions[0][2] == "primeira mensagem"

    def test_schema_atual_sem_queue_operation(self, tmp_path):
        _write_jsonl(tmp_path / "session-b.jsonl", [
            {"type": "system", "subtype": "init"},
            {"type": "user", "message": {"role": "user", "content": "segunda mensagem"}},
        ])
        sessions = _load_sessions(tmp_path)
        assert len(sessions) == 1
        assert sessions[0][0] == "session-b"
        assert sessions[0][2] == "segunda mensagem"

    def test_sessao_sem_texto_e_ignorada(self, tmp_path):
        _write_jsonl(tmp_path / "session-c.jsonl", [
            {"type": "system", "subtype": "init"},
        ])
        assert _load_sessions(tmp_path) == []

    def test_linha_corrompida_nao_derruba_parsing(self, tmp_path):
        f = tmp_path / "session-d.jsonl"
        f.write_text('not-json\n{"type": "user", "message": {"content": "sobrevive"}}\n')
        sessions = _load_sessions(tmp_path)
        assert len(sessions) == 1
        assert sessions[0][2] == "sobrevive"

    def test_ignora_caveat_e_usa_primeira_mensagem_real(self, tmp_path):
        _write_jsonl(tmp_path / "session-e.jsonl", [
            {
                "type": "user",
                "isMeta": True,
                "message": {
                    "content": "<local-command-caveat>Caveat: The messages below were generated</local-command-caveat>"
                },
            },
            {"type": "user", "message": {"content": "agora sim a conversa"}},
        ])
        sessions = _load_sessions(tmp_path)
        assert len(sessions) == 1
        assert sessions[0][2] == "agora sim a conversa"

    def test_ignora_comando_local_e_usa_primeira_mensagem_real(self, tmp_path):
        _write_jsonl(tmp_path / "session-f.jsonl", [
            {
                "type": "user",
                "message": {
                    "content": "<command-name>/clear</command-name>\n"
                               "            <command-message>clear</command-message>\n"
                               "            <command-args></command-args>"
                },
            },
            {"type": "user", "message": {"content": "depois do clear"}},
        ])
        sessions = _load_sessions(tmp_path)
        assert len(sessions) == 1
        assert sessions[0][2] == "depois do clear"


class TestPickSessionErrorLogging:
    def test_excecao_e_logada_antes_de_retornar_none(self, monkeypatch, tmp_path):
        rundir = tmp_path / "rundir"
        rundir.mkdir()
        monkeypatch.setattr("lib.session.RUNDIR", rundir)
        monkeypatch.setattr("lib.session._pick_session_impl", lambda cwd: (_ for _ in ()).throw(RuntimeError("boom")))

        result = pick_session("/tmp/algum-projeto")

        assert result is None
        err_log = (rundir / "err.log").read_text()
        assert "boom" in err_log
