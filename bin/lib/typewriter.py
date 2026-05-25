import os
import random
import time
from pathlib import Path

SKIP_FLAG = Path(os.environ.get("CLAUDE_RUNDIR", "/tmp/claude-client")) / "skip"


def _char_delay(char, base, hesitate=True):
    d = base * random.uniform(0.4, 1.4)
    if not hesitate:
        return d
    if char in '.!?':
        d += random.uniform(0.18, 0.38)
    elif char in ',;:—':
        d += random.uniform(0.08, 0.16)
    elif char == '\n':
        d += random.uniform(0.06, 0.16)
    elif random.random() < 0.015:
        d += random.uniform(0.18, 0.45)
    return d


def log_animated(path, text, delay=0.030, on_newline=None, hesitate=True):
    with open(path, "a", buffering=1) as f:
        for i, char in enumerate(text):
            if SKIP_FLAG.exists():
                try:
                    SKIP_FLAG.unlink()
                except FileNotFoundError:
                    pass
                f.write(text[i:])
                f.flush()
                return
            if char == '\n' and on_newline:
                on_newline()
            f.write(char)
            f.flush()
            time.sleep(_char_delay(char, delay, hesitate=hesitate))
