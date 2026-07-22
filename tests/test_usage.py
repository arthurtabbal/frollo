"""Testes para lib.usage — parsing do endpoint /api/oauth/usage e render da cota.

O payload de exemplo abaixo foi capturado do endpoint real (jul/2026), que
substituiu o antigo scraping de `claude -p /usage`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from lib.usage import _parse, _fmt_reset
from lib.runner.stats import _render_quota_line

# Payload real (reduzido aos campos que consumimos).
_SAMPLE = {
    "five_hour": {"utilization": 7.0, "resets_at": "2026-07-07T00:30:00.229161+00:00"},
    "seven_day": {"utilization": 25.0, "resets_at": "2026-07-09T10:00:00.229183+00:00"},
    "limits": [
        {"kind": "session", "percent": 7, "severity": "normal",
         "resets_at": "2026-07-07T00:30:00.229161+00:00"},
        {"kind": "weekly_all", "percent": 25, "severity": "normal",
         "resets_at": "2026-07-09T10:00:00.229183+00:00"},
        {"kind": "weekly_scoped", "percent": 12, "severity": "warning",
         "resets_at": "2026-07-09T10:00:00.229504+00:00",
         "scope": {"model": {"display_name": "Fable"}}},
    ],
}


def test_parse_compat_keys():
    r = _parse(_SAMPLE)
    assert r["session_pct"] == 7
    assert r["week_pct"] == 25
    assert r["session_reset"]  # não-vazio
    assert r["week_reset"]


def test_parse_limits_detail():
    limits = _parse(_SAMPLE)["limits"]
    assert [l["label"] for l in limits] == ["sessão", "semana", "Fable"]
    assert [l["pct"] for l in limits] == [7, 25, 12]
    # scoped compartilha o reset semanal → não repete o próprio
    assert limits[2]["reset"] == ""
    assert limits[0]["reset"] and limits[1]["reset"]


def test_parse_rounds_float_utilization():
    r = _parse({"five_hour": {"utilization": 33.7, "resets_at": None}})
    assert r["session_pct"] == 34


def test_parse_skips_null_percent_limits():
    r = _parse({"limits": [{"kind": "session", "percent": None}]})
    assert r is None  # nenhum campo aproveitável


def test_parse_empty_and_garbage():
    assert _parse({}) is None
    assert _parse(None) is None
    assert _parse([]) is None


def test_parse_weekly_scoped_without_model_falls_back_to_kind():
    r = _parse({"limits": [{"kind": "weekly_scoped", "percent": 5}]})
    assert r["limits"][0]["label"] == "weekly_scoped"


def test_fmt_reset_bad_input():
    assert _fmt_reset("") == ""
    assert _fmt_reset(None) == ""
    assert _fmt_reset("not-a-date") == ""


def test_render_uses_limits_when_present():
    line = _render_quota_line(_parse(_SAMPLE))
    assert "sessão" in line and "semana" in line and "Fable" in line
    assert "7%" in line and "25%" in line and "12%" in line
    assert "↺" in line  # resets presentes


def test_render_empty_and_fallback():
    line = _render_quota_line(None)
    assert "carregando" not in line
    assert line.startswith("\r\033[2K")
    # dict legado sem `limits` cai no caminho de compat
    legacy = {"session_pct": 3, "week_pct": 9, "session_reset": "21:30"}
    line = _render_quota_line(legacy)
    assert "sessão" in line and "semana" in line and "3%" in line and "9%" in line
