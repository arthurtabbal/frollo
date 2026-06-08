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
from .. import config


@contextlib.contextmanager
def _raw_stdin():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _add_to_allowlist(tool_name, cwd):
    settings_path = Path(cwd) / ".claude" / "settings.local.json"
    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
        allow = settings.setdefault("permissions", {}).setdefault("allow", [])
        if tool_name not in allow:
            allow.append(tool_name)
            settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    except Exception as e:
        sys.stdout.write(f"{DIM}erro ao atualizar settings: {e}{RESET}\n\n")
        sys.stdout.flush()


def _show_perm_banner(tool_name, inp=None, *, blocked=False):
    label = "  permissão bloqueada  " if blocked else "  permissão  "
    sys.stdout.write(f"\n{BG_PERM}{WHITE}{label}{RESET}  {YELLOW}{tool_name}{RESET}")
    if inp:
        detail = json.dumps(inp, ensure_ascii=False)
        detail = detail if len(detail) <= 120 else detail[:120] + "…"
        sys.stdout.write(f"  {DIM}{detail}{RESET}")
    sys.stdout.write("\n")
    sys.stdout.flush()
    if config.load().get("gargoyles", True):
        _g_prefix, _g_fala = _gargula_comment("permission", force=True)
        if _g_prefix:
            sys.stdout.write("\n")
            _typewrite(_g_prefix + _g_fala.rstrip('\n'), delay=0.025)
            sys.stdout.write("\n\n")
            sys.stdout.flush()


def _handle_control_request(event, proc, cwd):
    """Protocolo control_request/control_response (--permission-prompt-tool stdio).
    Dispara ANTES da tool executar — respondendo allow o turno continua normal."""
    request_id = event.get("request_id", "")
    tool = event.get("tool_name", event.get("tool", "?"))
    inp  = event.get("input", {})

    _show_perm_banner(tool, inp)
    sys.stdout.write(f"{DIM}[y] permitir  [n] negar  [a] permitir sempre{RESET}  ")
    sys.stdout.flush()

    with _raw_stdin():
        ch = os.read(sys.stdin.fileno(), 1).decode('utf-8', errors='replace').lower()

    if ch == 'a':
        sys.stdout.write(f"a  {DIM}(permitir sempre){RESET}\n\n")
        sys.stdout.flush()
        _add_to_allowlist(tool, cwd)
        behavior = "allow"
    elif ch == 'y':
        sys.stdout.write(f"y  {DIM}(permitido){RESET}\n\n")
        sys.stdout.flush()
        behavior = "allow"
    else:
        sys.stdout.write(f"n  {DIM}(negado){RESET}\n\n")
        sys.stdout.flush()
        behavior = "deny"

    resp = json.dumps({"type": "control_response", "request_id": request_id,
                       "response": {"behavior": behavior}})
    proc.stdin.write(resp + "\n")
    proc.stdin.flush()
    return behavior == "allow"


def _handle_permission_ask(tool_name, cwd):
    """Fallback: tool já falhou via tool_result error (sem control_request).
    Adiciona ao allowlist para o próximo turno."""
    _show_perm_banner(tool_name, blocked=True)
    sys.stdout.write(f"{DIM}O projeto requer aprovação explícita para {tool_name}.\n")
    sys.stdout.write(f"Adicionar {tool_name} ao allow do projeto (.claude/settings.local.json)? [y/n]{RESET}  ")
    sys.stdout.flush()

    with _raw_stdin():
        ch = os.read(sys.stdin.fileno(), 1).decode('utf-8', errors='replace').lower()

    if ch == 'y':
        sys.stdout.write("y\n")
        sys.stdout.flush()
        _add_to_allowlist(tool_name, cwd)
        sys.stdout.write(f"{DIM}✓ {tool_name} adicionado{RESET}\n\n")
        sys.stdout.flush()
        return True
    else:
        sys.stdout.write("n\n\n")
        sys.stdout.flush()
        return False


def _handle_permission(event, proc):
    """Protocolo antigo permission_request (y/n/a via stdin raw).
    Mantido como fallback caso o CLI emita esse evento."""
    tool = event.get("tool_name", event.get("tool", "?"))
    inp  = event.get("input", {})

    _show_perm_banner(tool, inp)
    sys.stdout.write(f"{DIM}[y] permitir  [n] negar  [a] permitir sempre{RESET}  ")
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
