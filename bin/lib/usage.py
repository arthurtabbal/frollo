import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

# Endpoint que a própria TUI do Claude Code consome (fetchUtilization →
# GET /api/oauth/usage). Substitui o antigo scraping de `claude -p /usage`:
# a partir de jul/2026 o output de print deixou de trazer as % de sessão/semana
# (elas passaram a renderizar só no componente interativo). O endpoint devolve
# JSON estruturado — mais rápido (~200ms, um GET) e sem spawnar subprocesso.
_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_OAUTH_BETA = "oauth-2025-04-20"

# kind → rótulo curto no pane. weekly_scoped cai no display_name do modelo.
_LABELS = {"session": "sessão", "weekly_all": "semana"}


def _config_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def _access_token():
    """accessToken OAuth mantido fresco pelos processos `claude` (renovado no
    próprio arquivo). Como o Frollo roda `claude` a cada turno, o token está
    válido quando buscamos a cota logo depois."""
    path = os.path.join(_config_dir(), ".credentials.json")
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return (data.get("claudeAiOauth") or {}).get("accessToken")


def fetch_usage(timeout=6.0):
    """
    Busca a cota de assinatura no endpoint OAuth do Claude Code e devolve um dict
    com session_pct/week_pct/resets (compat com o runner) + `limits` detalhado
    (sessão, semana e cotas por modelo). Retorna None em qualquer falha — o pane
    então mantém a última cota ou mostra placeholder, sem quebrar.
    """
    token = _access_token()
    if not token:
        return None
    req = urllib.request.Request(
        _USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "anthropic-beta": _OAUTH_BETA,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return _parse(data)


def _fmt_reset(iso):
    """ISO 8601 (UTC) → hora local curta. Reset próximo (<16h) vira 'HH:MM';
    mais distante vira 'Jul9'."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return ""
    local = dt.astimezone()
    delta = (local - datetime.now(timezone.utc).astimezone()).total_seconds()
    if 0 <= delta < 16 * 3600:
        return local.strftime("%H:%M")
    return f"{local:%b}{local.day}"


def _parse(data):
    if not isinstance(data, dict):
        return None

    # `limits` já vem mastigado pelo servidor (só as cotas relevantes, não-nulas):
    # session, weekly_all e um weekly_scoped por modelo com cota própria.
    limits = []
    for lim in data.get("limits") or []:
        pct = lim.get("percent")
        if pct is None:
            continue
        kind = lim.get("kind", "")
        label = _LABELS.get(kind)
        if label is None:
            model = ((lim.get("scope") or {}).get("model") or {}).get("display_name")
            label = model or kind or "?"
        # reset só nas cotas primárias; os scoped compartilham o reset semanal.
        reset = _fmt_reset(lim.get("resets_at")) if kind in ("session", "weekly_all") else ""
        limits.append({
            "label": label,
            "pct": int(round(pct)),
            "severity": lim.get("severity"),
            "reset": reset,
            "kind": kind,
        })

    result = {}
    if limits:
        result["limits"] = limits

    # Chaves legadas que o runner/stats já consomem (e o cache last_quota.json).
    five = data.get("five_hour") or {}
    seven = data.get("seven_day") or {}
    if five.get("utilization") is not None:
        result["session_pct"] = int(round(five["utilization"]))
        result["session_reset"] = _fmt_reset(five.get("resets_at"))
    if seven.get("utilization") is not None:
        result["week_pct"] = int(round(seven["utilization"]))
        result["week_reset"] = _fmt_reset(seven.get("resets_at"))

    return result or None
