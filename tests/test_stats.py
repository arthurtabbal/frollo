"""Testes para lib/runner/stats.py — tabela de preços por modelo."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from lib.runner.stats import _model_price


class TestModelPrice:
    def test_opus_4_1_mantem_preco_antigo(self):
        assert _model_price("claude-opus-4-1-20250805") == (15.0, 75.0)

    def test_opus_4_0_mantem_preco_antigo(self):
        assert _model_price("claude-opus-4-0-20250514") == (15.0, 75.0)

    def test_opus_generico_usa_preco_novo(self):
        assert _model_price("claude-opus-4-5-20260101") == (5.0, 25.0)

    def test_sonnet_4(self):
        assert _model_price("claude-sonnet-4-5-20250929") == (3.0, 15.0)

    def test_haiku_4_5(self):
        assert _model_price("claude-haiku-4-5-20251001") == (1.0, 5.0)

    def test_modelo_desconhecido_usa_fallback_sonnet(self):
        assert _model_price("claude-4-6-desconhecido") == (3.0, 15.0)
