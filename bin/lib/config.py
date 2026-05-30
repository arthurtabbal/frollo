import json
import os
from pathlib import Path

CONFIG_PATH = Path(os.environ.get(
    "FROLLO_CONFIG",
    str(Path.home() / ".config" / "frollo" / "config.json"),
))

DEFAULTS: dict = {
    "typewriter": True,
    "gargoyles":  True,
    "stats_pane": True,
    "thinking_autoresize": True,
}

_cache: dict | None = None


def load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    if not CONFIG_PATH.exists():
        return dict(DEFAULTS)
    try:
        with open(CONFIG_PATH) as f:
            _cache = {**DEFAULTS, **json.load(f)}
    except Exception:
        _cache = dict(DEFAULTS)
    return _cache


def save(cfg: dict) -> None:
    global _cache
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    _cache = dict(cfg)


def is_first_run() -> bool:
    return not CONFIG_PATH.exists()
