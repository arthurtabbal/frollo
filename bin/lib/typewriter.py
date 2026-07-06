import random


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
