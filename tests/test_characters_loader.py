"""Testes para o loader de personagens JSON (lib/gargulas.py: _load_characters)."""
import json
import sys
from io import StringIO
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from lib.gargulas import _load_characters, _COLOR_NAMES
from lib.theme import GARGOYLE_VICTOR, GARGOYLE_HUGO, GARGOYLE_GUDULE, PURPLE, GREEN


def _write(tmp_path, filename, data):
    """Atalho: salva dict como JSON em tmp_path."""
    f = tmp_path / filename
    f.write_text(json.dumps(data, ensure_ascii=False))
    return f


def _victor_valido():
    return {
        "name": "Victor",
        "color": "purple",
        "falas": {
            "Bash": ["Mon Dieu!"],
            "Edit": ["Uma obra-prima!"],
            "default": ["Incrível!"],
        },
    }


# ── Carregamento válido ────────────────────────────────────────────────────────


class TestCarregamentoValido:
    def test_personagem_valido_carrega(self, tmp_path):
        _write(tmp_path, "victor.json", _victor_valido())
        result = _load_characters(tmp_path)
        assert "Victor" in result

    def test_nome_vem_do_campo_name(self, tmp_path):
        _write(tmp_path, "qualquercoisa.json", _victor_valido())
        result = _load_characters(tmp_path)
        assert "Victor" in result  # nome vem do JSON, não do filename

    def test_cor_nome_resolvida_para_ansi(self, tmp_path):
        _write(tmp_path, "victor.json", _victor_valido())
        result = _load_characters(tmp_path)
        assert result["Victor"]["cor"] == PURPLE  # "purple" → PURPLE == GARGOYLE_VICTOR

    def test_cor_numerica_resolvida_para_ansi(self, tmp_path):
        _write(tmp_path, "g.json", {**_victor_valido(), "name": "G", "color": 103})
        result = _load_characters(tmp_path)
        assert result["G"]["cor"] == GARGOYLE_GUDULE  # 103 → "\033[38;5;103m"

    def test_cores_nome_e_numero(self, tmp_path):
        _write(tmp_path, "v.json", {**_victor_valido(), "name": "V", "color": "purple"})
        _write(tmp_path, "h.json", {**_victor_valido(), "name": "H", "color": "green"})
        _write(tmp_path, "g.json", {**_victor_valido(), "name": "G", "color": 103})
        result = _load_characters(tmp_path)
        assert result["V"]["cor"] == GARGOYLE_VICTOR
        assert result["H"]["cor"] == GARGOYLE_HUGO
        assert result["G"]["cor"] == GARGOYLE_GUDULE

    def test_default_vira_none(self, tmp_path):
        _write(tmp_path, "victor.json", _victor_valido())
        result = _load_characters(tmp_path)
        falas = result["Victor"]["falas"]
        assert None in falas
        assert falas[None] == ["Incrível!"]

    def test_falas_preservadas(self, tmp_path):
        _write(tmp_path, "victor.json", _victor_valido())
        result = _load_characters(tmp_path)
        assert result["Victor"]["falas"]["Bash"] == ["Mon Dieu!"]

    def test_multiplos_arquivos(self, tmp_path):
        _write(tmp_path, "v.json", _victor_valido())
        _write(tmp_path, "h.json", {**_victor_valido(), "name": "Hugo", "color": "green"})
        result = _load_characters(tmp_path)
        assert "Victor" in result
        assert "Hugo" in result

    def test_diretorio_vazio_retorna_dict_vazio(self, tmp_path):
        result = _load_characters(tmp_path)
        assert result == {}


# ── Resiliência a arquivos estranhos ──────────────────────────────────────────


class TestArquivosIgnorados:
    def test_arquivo_nao_json_ignorado(self, tmp_path):
        (tmp_path / "notas.txt").write_text("anotação qualquer")
        result = _load_characters(tmp_path)
        assert result == {}

    def test_arquivo_nao_json_nao_gera_aviso(self, tmp_path, capsys):
        (tmp_path / "notas.txt").write_text("anotação qualquer")
        _load_characters(tmp_path)
        assert capsys.readouterr().err == ""


# ── Validação com avisos no stderr ────────────────────────────────────────────


class TestErrosDeValidacao:
    def test_json_invalido_pula_arquivo(self, tmp_path):
        (tmp_path / "ruim.json").write_text("{isso não é json")
        result = _load_characters(tmp_path)
        assert result == {}

    def test_json_invalido_avisa_no_stderr(self, tmp_path, capsys):
        (tmp_path / "ruim.json").write_text("{isso não é json")
        _load_characters(tmp_path)
        err = capsys.readouterr().err
        assert "ruim.json" in err

    def test_json_invalido_aviso_menciona_erro_de_sintaxe(self, tmp_path, capsys):
        (tmp_path / "ruim.json").write_text("{isso não é json")
        _load_characters(tmp_path)
        err = capsys.readouterr().err
        # deve indicar que é erro de sintaxe/parse, não campo ausente
        assert any(word in err.lower() for word in ("json", "sintaxe", "parse", "decode"))

    def test_campo_name_ausente_pula(self, tmp_path):
        data = _victor_valido()
        del data["name"]
        _write(tmp_path, "sem_name.json", data)
        assert _load_characters(tmp_path) == {}

    def test_campo_name_ausente_avisa_no_stderr(self, tmp_path, capsys):
        data = _victor_valido()
        del data["name"]
        _write(tmp_path, "sem_name.json", data)
        _load_characters(tmp_path)
        err = capsys.readouterr().err
        assert "sem_name.json" in err
        assert "name" in err

    def test_campo_color_ausente_pula(self, tmp_path):
        data = _victor_valido()
        del data["color"]
        _write(tmp_path, "sem_color.json", data)
        assert _load_characters(tmp_path) == {}

    def test_campo_color_ausente_avisa_no_stderr(self, tmp_path, capsys):
        data = _victor_valido()
        del data["color"]
        _write(tmp_path, "sem_color.json", data)
        _load_characters(tmp_path)
        err = capsys.readouterr().err
        assert "sem_color.json" in err
        assert "color" in err

    def test_campo_falas_ausente_pula(self, tmp_path):
        data = _victor_valido()
        del data["falas"]
        _write(tmp_path, "sem_falas.json", data)
        assert _load_characters(tmp_path) == {}

    def test_campo_falas_ausente_avisa_no_stderr(self, tmp_path, capsys):
        data = _victor_valido()
        del data["falas"]
        _write(tmp_path, "sem_falas.json", data)
        _load_characters(tmp_path)
        err = capsys.readouterr().err
        assert "sem_falas.json" in err
        assert "falas" in err

    def test_cor_desconhecida_pula(self, tmp_path):
        data = {**_victor_valido(), "color": "roxo_medieval"}
        _write(tmp_path, "cor_errada.json", data)
        assert _load_characters(tmp_path) == {}

    def test_cor_desconhecida_avisa_com_opcoes_validas(self, tmp_path, capsys):
        data = {**_victor_valido(), "color": "roxo_medieval"}
        _write(tmp_path, "cor_errada.json", data)
        _load_characters(tmp_path)
        err = capsys.readouterr().err
        assert "cor_errada.json" in err
        assert "roxo_medieval" in err
        # deve listar pelo menos um nome de paleta válido
        assert any(c in err for c in _COLOR_NAMES)

    def test_cor_numerica_fora_do_intervalo_pula(self, tmp_path):
        data = {**_victor_valido(), "color": 256}
        _write(tmp_path, "cor_256.json", data)
        assert _load_characters(tmp_path) == {}

    def test_cor_numerica_fora_do_intervalo_avisa(self, tmp_path, capsys):
        data = {**_victor_valido(), "color": 256}
        _write(tmp_path, "cor_256.json", data)
        _load_characters(tmp_path)
        err = capsys.readouterr().err
        assert "cor_256.json" in err
        assert "256" in err

    def test_cor_numerica_negativa_pula(self, tmp_path):
        data = {**_victor_valido(), "color": -1}
        _write(tmp_path, "cor_neg.json", data)
        assert _load_characters(tmp_path) == {}

    def test_cor_tipo_errado_lista_pula(self, tmp_path):
        data = {**_victor_valido(), "color": [103]}
        _write(tmp_path, "cor_lista.json", data)
        assert _load_characters(tmp_path) == {}

    def test_cor_tipo_errado_lista_avisa(self, tmp_path, capsys):
        data = {**_victor_valido(), "color": [103]}
        _write(tmp_path, "cor_lista.json", data)
        _load_characters(tmp_path)
        err = capsys.readouterr().err
        assert "cor_lista.json" in err
        assert "color" in err

    def test_falas_tipo_errado_string_em_vez_de_lista_pula(self, tmp_path):
        data = _victor_valido()
        data["falas"]["Bash"] = "deveria ser lista"
        _write(tmp_path, "tipo_errado.json", data)
        assert _load_characters(tmp_path) == {}

    def test_falas_tipo_errado_avisa_no_stderr(self, tmp_path, capsys):
        data = _victor_valido()
        data["falas"]["Bash"] = "deveria ser lista"
        _write(tmp_path, "tipo_errado.json", data)
        _load_characters(tmp_path)
        err = capsys.readouterr().err
        assert "tipo_errado.json" in err
        assert "Bash" in err

    def test_falas_nao_e_dict_pula(self, tmp_path):
        data = {**_victor_valido(), "falas": ["lista em vez de dict"]}
        _write(tmp_path, "falas_lista.json", data)
        assert _load_characters(tmp_path) == {}

    def test_falas_nao_e_dict_avisa_no_stderr(self, tmp_path, capsys):
        data = {**_victor_valido(), "falas": ["lista em vez de dict"]}
        _write(tmp_path, "falas_lista.json", data)
        _load_characters(tmp_path)
        err = capsys.readouterr().err
        assert "falas_lista.json" in err
        assert "falas" in err


# ── Resiliência: arquivos mistos ───────────────────────────────────────────────


class TestResilienciaMista:
    def test_arquivo_invalido_nao_afeta_valido(self, tmp_path):
        _write(tmp_path, "valido.json", _victor_valido())
        (tmp_path / "ruim.json").write_text("{json quebrado")
        result = _load_characters(tmp_path)
        assert "Victor" in result
        assert len(result) == 1

    def test_multiplos_invalidos_todos_avisam(self, tmp_path, capsys):
        (tmp_path / "a.json").write_text("{json quebrado")
        data = _victor_valido()
        del data["name"]
        _write(tmp_path, "b.json", data)
        _load_characters(tmp_path)
        err = capsys.readouterr().err
        assert "a.json" in err
        assert "b.json" in err
