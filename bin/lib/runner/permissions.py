import contextlib
import json
import os
import sys
import termios
import tty
from pathlib import Path

from ..theme import DIM, RESET, YELLOW, WHITE, BG_PERM
from ..gargulas import _gargula_comment
from .text import _typewrite


@contextlib.contextmanager
def _raw_stdin():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _handle_permission_ask(tool_name, cwd):
    """Trata o caso de permissions.ask: tool falhou por falta de aprovação no projeto.
    Retorna True se permissão foi concedida e o turno deve ser retentado."""
    settings_path = Path(cwd) / ".claude" / "settings.local.json"

    sys.stdout.write(f"\n{BG_PERM}{WHITE}  permissão bloqueada  {RESET}  {YELLOW}{tool_name}{RESET}\n")
    sys.stdout.flush()
    _g_prefix, _g_fala = _gargula_comment("permission", force=True)
    if _g_prefix:
        _typewrite(_g_prefix + _g_fala.rstrip('\n'), delay=0.025, wrap=False)
        sys.stdout.write("\n")
        sys.stdout.flush()
    sys.stdout.write(f"{DIM}O projeto requer aprovação explícita para {tool_name}.\n")
    sys.stdout.write(f"Adicionar {tool_name} ao allow do projeto (.claude/settings.local.json)? [y/n]{RESET}  ")
    sys.stdout.flush()

    with _raw_stdin():
        ch = os.read(sys.stdin.fileno(), 1).decode('utf-8', errors='replace').lower()

    if ch == 'y':
        sys.stdout.write("y\n")
        sys.stdout.flush()
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
            allow = settings.setdefault("permissions", {}).setdefault("allow", [])
            if tool_name not in allow:
                allow.append(tool_name)
                settings_path.write_text(json.dumps(settings, indent=2) + "\n")
            sys.stdout.write(f"{DIM}✓ {tool_name} adicionado — retentando automaticamente…{RESET}\n\n")
            sys.stdout.flush()
            return True
        except Exception as e:
            sys.stdout.write(f"{DIM}erro ao atualizar settings: {e}{RESET}\n\n")
            sys.stdout.flush()
            return False
    else:
        sys.stdout.write("n\n\n")
        sys.stdout.flush()
        return False


def _handle_permission(event, proc):
    tool = event.get("tool_name", event.get("tool", "?"))
    inp  = event.get("input", {})

    sys.stdout.write(f"\n{BG_PERM}{WHITE}  permissão  {RESET}  {YELLOW}{tool}{RESET}")
    if inp:
        detail = json.dumps(inp, ensure_ascii=False)
        detail = detail if len(detail) <= 120 else detail[:120] + "…"
        sys.stdout.write(f"  {DIM}{detail}{RESET}")
    sys.stdout.write(f"\n{DIM}[y] permitir  [n] negar  [a] permitir sempre{RESET}  ")
    sys.stdout.flush()

    with _raw_stdin():
        ch = os.read(sys.stdin.fileno(), 1).decode('utf-8', errors='replace').lower()

    if ch == 'a':
        sys.stdout.write(f"a  {DIM}(permitir sempre){RESET}\n\n")
        sys.stdout.flush()
        proc.stdin.write("a\n")
        proc.stdin.flush()
        return True
    elif ch == 'y':
        sys.stdout.write(f"y  {DIM}(permitido){RESET}\n\n")
        sys.stdout.flush()
        proc.stdin.write("y\n")
        proc.stdin.flush()
        return True
    else:
        sys.stdout.write(f"n  {DIM}(negado){RESET}\n\n")
        sys.stdout.flush()
        return False
