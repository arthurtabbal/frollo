import json
import random
import sys
from pathlib import Path

from .theme import RESET, DIM, GREEN, YELLOW, CYAN, BLUE, PURPLE, WHITE

CHARACTERS_DIR = Path(__file__).parent.parent / "characters"

# Nomes de paleta disponíveis para o campo "color" nos JSONs de personagens.
# Também aceita inteiro 0–255 (cor ANSI 256 direta).
_COLOR_NAMES = {
    "green":  GREEN,
    "yellow": YELLOW,
    "cyan":   CYAN,
    "blue":   BLUE,
    "purple": PURPLE,
    "white":  WHITE,
}


def _resolve_color(raw, filename):
    """Resolve o campo color do JSON para sequência ANSI. Retorna None se inválido."""
    if isinstance(raw, int):
        if 0 <= raw <= 255:
            return f"\033[38;5;{raw}m"
        print(f"[frollo] {filename}: color {raw} fora do intervalo 0–255", file=sys.stderr)
        return None
    if isinstance(raw, str):
        if raw in _COLOR_NAMES:
            return _COLOR_NAMES[raw]
        valid = ", ".join(_COLOR_NAMES)
        print(f"[frollo] {filename}: cor '{raw}' não reconhecida — opções: {valid} ou número 0–255", file=sys.stderr)
        return None
    print(f"[frollo] {filename}: 'color' deve ser string ou inteiro, encontrado {type(raw).__name__}", file=sys.stderr)
    return None


def _load_characters(directory: Path) -> dict:
    result = {}
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"[frollo] {path.name}: JSON inválido — {e}", file=sys.stderr)
            continue

        ok = True
        for field in ("name", "color", "falas"):
            if field not in data:
                print(f"[frollo] {path.name}: campo '{field}' não encontrado", file=sys.stderr)
                ok = False
                break
        if not ok:
            continue

        cor = _resolve_color(data["color"], path.name)
        if cor is None:
            continue

        if not isinstance(data["falas"], dict):
            print(f"[frollo] {path.name}: 'falas' deve ser um objeto, encontrado {type(data['falas']).__name__}", file=sys.stderr)
            continue

        for category, lines in data["falas"].items():
            if not isinstance(lines, list):
                print(f"[frollo] {path.name}: falas.{category} deve ser uma lista, encontrado {type(lines).__name__}", file=sys.stderr)
                ok = False
                break
        if not ok:
            continue

        falas = {(None if k == "default" else k): v for k, v in data["falas"].items()}
        result[data["name"]] = {"cor": cor, "falas": falas}

    return result


_GARGULAS = _load_characters(CHARACTERS_DIR)


def _gargula_comment(tool_name=None):
    """Retorna (prefix, fala) onde prefix aparece instantâneo e fala é animada. Ou (None, None)."""
    if random.random() > 0.15:
        return None, None
    nome, g = random.choice(list(_GARGULAS.items()))
    cor = g["cor"]
    falas = g["falas"].get(tool_name) or g["falas"][None]
    fala = random.choice(falas)
    prefix = f"  {cor}{nome}{RESET}{DIM}:  "
    return prefix, fala + f"{RESET}\n"
