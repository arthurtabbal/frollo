_MODEL_PRICES = {
    "claude-opus-4":   (15.0, 75.0),
    "claude-sonnet-4":  (3.0, 15.0),
    "claude-haiku-4":  (0.80,  4.0),
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
