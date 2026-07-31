"""Testes para ClaudeClient — comportamento com dependências ausentes."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from chat import ClaudeClient, _normalize_model_choice, _short_model, _usage_refresh_seconds
from lib.runner.capabilities import backend_profile, supports
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

    def test_prompt_preserva_versao_do_modelo(self):
        c = ClaudeClient(model="claude-opus-4-8")
        assert "opus 4.8" in c._prompt()

    def test_prompt_exibe_effort_e_agent_quando_definidos(self):
        c = ClaudeClient(model="sonnet", effort="max", agent="advisor")
        prompt = c._prompt()

        assert "sonnet" in prompt
        assert "max" in prompt
        assert "advisor" in prompt

    def test_prompt_sem_modelo_nao_inclui_badge(self):
        c = ClaudeClient()
        c.observed_model = ""
        assert c._prompt() == f"{DIM}normal{RESET} {WHITE}>_{RESET} "

    def test_prompt_reflete_modo_atual_via_mode_ref(self):
        from chat import Mode
        c = ClaudeClient()
        c._mode_ref[0] = Mode.AUTO
        assert "auto" in c._prompt()

    def test_prompt_codex_exibe_backend_e_modelo_observado(self):
        c = ClaudeClient(backend="codex", effort="high")
        c.observed_model = "gpt-5-codex"
        assert "codex" in c._prompt()
        assert "gpt-5-codex" in c._prompt()
        assert "high" in c._prompt()


class TestModeloVersao:
    def test_short_model_extrai_familia_e_versao(self):
        assert _short_model("claude-sonnet-4-6") == "sonnet 4.6"
        assert _short_model("claude-haiku-4-5-20251001") == "haiku 4.5"
        assert _short_model("claude-3-5-sonnet-20241022") == "sonnet 3.5"
        assert _short_model("claude-fable-5") == "fable 5"

    def test_normalize_model_alias_com_versao(self):
        assert _normalize_model_choice("sonnet", "4.6") == "claude-sonnet-4-6"
        assert _normalize_model_choice("opus-4.8") == "claude-opus-4-8"
        assert _normalize_model_choice("claude-sonnet-4-6") == "claude-sonnet-4-6"


class TestBackendCapabilities:
    def test_claude_suporta_modelo_e_resume(self):
        profile = backend_profile("claude")
        assert supports(profile, "model_selection")
        assert supports(profile, "effort_selection")
        assert supports(profile, "agent_selection")
        assert supports(profile, "session_resume")

    def test_codex_declara_limites_atuais(self):
        profile = backend_profile("codex")
        assert not supports(profile, "model_selection")
        assert supports(profile, "effort_selection")
        assert not supports(profile, "agent_selection")
        assert not supports(profile, "session_resume")
        assert supports(profile, "reasoning_stream")

    def test_codex_nao_restaura_cache_de_cota_claude_no_startup(self):
        c = ClaudeClient(backend="codex")
        c._load_cached_usage = MagicMock()
        c._write_quota_line = MagicMock()
        c._ensure_usage_updater = MagicMock()

        c._start_claude_usage_pane()

        c._load_cached_usage.assert_not_called()
        c._write_quota_line.assert_not_called()
        c._ensure_usage_updater.assert_not_called()

    def test_start_usage_pane_codex_liga_updater_codex(self):
        c = ClaudeClient(backend="codex")
        c._start_claude_usage_pane = MagicMock()
        c._start_codex_usage_pane = MagicMock()

        c._start_usage_pane()

        c._start_codex_usage_pane.assert_called_once_with()
        c._start_claude_usage_pane.assert_not_called()

    def test_stats_title_codex_usa_email_do_codex(self):
        c = ClaudeClient(backend="codex")
        c.tmux_srv = "srv"
        import chat
        (chat.RUNDIR / "stats_pane").write_text("%42")

        with patch("chat.subprocess.run") as run:
            c._update_stats_title("codex@example.com")

        assert run.call_args.args[0] == [
            "tmux", "-L", "srv", "select-pane", "-t", "%42", "-T", "〰 stats · codex@example.com",
        ]


class TestUsageRefresh:
    def test_intervalo_default_de_cota_e_conservador(self, monkeypatch):
        monkeypatch.delenv("FROLLO_USAGE_REFRESH_SECONDS", raising=False)

        assert _usage_refresh_seconds() == 300.0

    def test_intervalo_de_cota_aceita_override_com_piso(self, monkeypatch):
        monkeypatch.setenv("FROLLO_USAGE_REFRESH_SECONDS", "10")

        assert _usage_refresh_seconds() == 60.0

    def test_intervalo_de_cota_ignora_override_invalido(self, monkeypatch):
        monkeypatch.setenv("FROLLO_USAGE_REFRESH_SECONDS", "quasimodo")

        assert _usage_refresh_seconds() == 300.0


class TestInterruptedContext:
    def test_preserva_contexto_local_do_turno_cancelado(self, client):
        import chat

        chat.THINKING_LOG.write_text("thinking antigo\n")
        chat.TOOLS_LOG.write_text("tools antigo\n")
        client._begin_interrupted_turn_capture("pergunta interrompida", images=[{"media_type": "image/png"}])
        client._last_response_text = "resposta parcial"
        chat.THINKING_LOG.write_text(chat.THINKING_LOG.read_text() + "\x1b[31mpensei nisso\x1b[0m\n")
        chat.TOOLS_LOG.write_text(chat.TOOLS_LOG.read_text() + "\x1b[32mrodei uma tool\x1b[0m\n")

        assert client._preserve_interrupted_turn()

        content = client._interrupted_context_path().read_text()
        assert "pergunta interrompida" in content
        assert "resposta parcial" in content
        assert "pensei nisso" in content
        assert "rodei uma tool" in content
        assert "thinking antigo" not in content
        assert "\x1b[" not in content
        assert "1 imagem" in content

    def test_preserva_tools_mesmo_se_pane_foi_truncado_no_turno(self, client):
        import chat

        chat.TOOLS_LOG.write_text("tools antigo que sera truncado")
        client._begin_interrupted_turn_capture("pergunta")
        chat.TOOLS_LOG.write_text("tool depois do clear\n")

        client._preserve_interrupted_turn()

        assert "tool depois do clear" in client._interrupted_context_path().read_text()

    def test_anexa_contexto_interrompido_no_proximo_turno_e_limpa_arquivo(self, client):
        import chat

        client.backend_profile = backend_profile("claude")
        client._interrupted_context_path().write_text("=== turno anterior interrompido ===\nresposta parcial\n")

        with patch("chat.run_turn", return_value=False) as run:
            result = client._run_turn("continua daqui")

        sent = run.call_args.args[1]
        assert result is False
        assert "resposta parcial" in sent
        assert "continua daqui" in sent
        assert not client._interrupted_context_path().exists()

    def test_turno_cancelado_mantem_captura_para_o_handler_do_ctrl_c(self, client):
        client.backend_profile = backend_profile("claude")

        with patch("chat.run_turn", side_effect=KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):
                client._run_turn("para no meio")

        assert client._interrupted_turn_capture["message"] == "para no meio"

    def test_cancelamento_repetido_nao_aninha_contexto_injetado(self, client):
        client.backend_profile = backend_profile("claude")
        client._interrupted_context_path().write_text("=== turno anterior interrompido ===\nprimeiro parcial\n")

        with patch("chat.run_turn", side_effect=KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):
                client._run_turn("segunda tentativa")

        client._last_response_text = "segundo parcial"
        client._preserve_interrupted_turn()
        content = client._interrupted_context_path().read_text()

        assert "primeiro parcial" in content
        assert "segunda tentativa" in content
        assert "segundo parcial" in content
        assert "contexto local preservado pelo Frollo" not in content
        assert content.count("=== turno anterior interrompido ===") == 2
