_MODEL_PRICES = {
    "claude-opus-4":   (15.0, 75.0),
    "claude-sonnet-4":  (3.0, 15.0),
    "claude-haiku-4":  (0.80,  4.0),
}


def _model_price(model):
    for prefix, prices in _MODEL_PRICES.items():
        if model.startswith(prefix):
            return prices
    return (3.0, 15.0)


def _fmt_cost(cost):
    return f"${cost:.4f}" if cost < 0.01 else f"${cost:.2f}"
