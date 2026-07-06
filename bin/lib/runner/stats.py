from ..theme import DIM, RESET, YELLOW

# Preços por milhão de tokens (input, output). Prefixos específicos antes do
# genérico correspondente — dict preserva ordem de inserção e _model_price para
# no primeiro match. claude-opus-4-1/4-0 são as versões antigas (mantêm 15/75);
# o genérico claude-opus-4 cobre as versões novas (4.5+, incl. 4.7/4.8) a 5/25.
# verificado jul/2026
_MODEL_PRICES = {
    "claude-opus-4-1":   (15.0, 75.0),
    "claude-opus-4-0":   (15.0, 75.0),
    "claude-opus-4":      (5.0, 25.0),
    "claude-sonnet-4":    (3.0, 15.0),
    "claude-haiku-4-5":   (1.0,  5.0),
    "claude-haiku-4":    (0.80,  4.0),
}

_MODEL_CTX = {
    "claude-opus-4":   200_000,
    "claude-sonnet-4": 200_000,
    "claude-haiku-4":  200_000,
}


def _model_price(model):
    for prefix, prices in _MODEL_PRICES.items():
        if model.startswith(prefix):
            return prices
    return (3.0, 15.0)


def _model_ctx_window(model):
    for prefix, ctx in _MODEL_CTX.items():
        if model.startswith(prefix):
            return ctx
    return 200_000


def _ctx_bar(ctx_tokens, max_tokens=200_000, width=16):
    pct = ctx_tokens / max(1, max_tokens)
    filled = round(min(1.0, pct) * width)
    return '█' * filled + '░' * (width - filled), min(1.0, pct)


def _fmt_cost(cost):
    return f"${cost:.4f}" if cost < 0.01 else f"${cost:.2f}"


def _fmt_tok(n):
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


def _quota_color(pct):
    if pct is None:
        return DIM
    if pct >= 85:
        return '\033[91m'
    if pct >= 70:
        return YELLOW
    return DIM


def _limit_color(pct, severity=None):
    """Cor de uma cota: honra a severidade do servidor quando presente; senão
    cai nos thresholds de porcentagem (alerta visual precoce)."""
    if severity == 'warning':
        return YELLOW
    if severity in ('critical', 'exceeded', 'blocked', 'severe'):
        return '\033[91m'
    return _quota_color(pct)


def _render_quota_line(usage):
    """usage: dict com `limits` detalhado (sessão/semana/por-modelo) e/ou as
    chaves legadas session_pct/week_pct/session_reset. None/{} = ainda carregando."""
    if not usage:
        return f"\r\033[2K{DIM}{'cota':>8}  ◎   carregando…{RESET}"

    limits = usage.get('limits')
    if limits:
        parts = []
        for lim in limits:
            col = _limit_color(lim.get('pct'), lim.get('severity'))
            rst = f" {DIM}↺ {lim['reset']}{RESET}" if lim.get('reset') else ""
            parts.append(f"{lim['label']} {col}{lim['pct']}%{RESET}{rst}")
        body = f"  {DIM}·{RESET}  ".join(parts)
        return f"\r\033[2K{DIM}{'cota':>8}{RESET}  ◎   {body}"

    # Fallback legado (cache last_quota.json antigo, sem `limits`).
    s_pct = usage.get('session_pct')
    w_pct = usage.get('week_pct')
    s_rst = usage.get('session_reset', '')
    s_part = f"{_limit_color(s_pct)}{s_pct}%{RESET}" if s_pct is not None else f"{DIM}?%{RESET}"
    w_part = f"{_limit_color(w_pct)}{w_pct}%{RESET}" if w_pct is not None else f"{DIM}?%{RESET}"
    rst_part = f"  {DIM}↺ {s_rst}{RESET}" if s_rst else ""
    return (
        f"\r\033[2K{DIM}{'cota':>8}{RESET}  ◎   "
        f"sessão {s_part}  {DIM}·{RESET}  semana {w_part}{rst_part}"
    )


def _render_ctx_line(ctx_used, ctx_max):
    bar, pct = _ctx_bar(ctx_used, ctx_max)
    if pct >= 0.85:
        col = '\033[91m'
    elif pct >= 0.70:
        col = YELLOW
    else:
        col = DIM
    return (
        f"\r\033[2K{DIM}{'ctx':>8}{RESET}  ▦   "
        f"{col}{bar}{RESET}  "
        f"{pct*100:.0f}%  {_fmt_tok(ctx_used)}/{_fmt_tok(ctx_max)}"
    )


def _render_turn_line(ts, input_tok, output_tok, elapsed, cost, cache_read_tokens=0):
    cache_part = f"  ⚡{_fmt_tok(cache_read_tokens)}" if cache_read_tokens > 500 else ""
    return (
        f"\r\033[2K{DIM}{ts}{RESET}  🔢  "
        f"{_fmt_tok(input_tok)} in · {_fmt_tok(output_tok)} out · "
        f"{elapsed:.1f}s · {_fmt_cost(cost)}{cache_part}"
    )


def _render_total_line(total_input, total_output, total_elapsed, total_cost):
    return (
        f"\r\033[2K{DIM}{'sessão':>8}{RESET}  ∑   "
        f"{_fmt_tok(total_input)} in · "
        f"{_fmt_tok(total_output)} out · "
        f"{total_elapsed:.0f}s · {_fmt_cost(total_cost)}"
    )


def _render_no_data_lines():
    """3 linhas placeholder quando não há last_session.json ainda (resume sem histórico salvo)."""
    return (
        f"\r\033[2K{DIM}{'turno':>8}  🔢   –{RESET}",
        f"\r\033[2K{DIM}{'sessão':>8}  ∑    –{RESET}",
        f"\r\033[2K{DIM}{'ctx':>8}  ▦   {'░' * 16}  –{RESET}",
    )
