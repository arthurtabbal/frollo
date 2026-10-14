import contextlib
import json
import os
import sys
import termios
import tty
from pathlib import Path

from ..theme import DIM, RESET, YELLOW, WHITE, BG_PERM, BG_USER
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


@contextlib.contextmanager
def _cooked_stdin():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    cooked = list(old)
    cooked[3] |= termios.ECHO | termios.ICANON
    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, cooked)
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


def _write_stdin(proc, data):
    """Escreve em proc.stdin tolerando pipe morto (turno com imagem fecha stdin
    após o envio; um control_request/permission_request tardio não pode escrever)."""
    try:
        proc.stdin.write(data)
        proc.stdin.flush()
        return True
    except (ValueError, BrokenPipeError):
        sys.stdout.write(f"{DIM}(stdin do turno já fechado — resposta de permissão não pôde ser enviada, tratando como negado){RESET}\n")
        sys.stdout.flush()
        return False


def _question_text(inp, fallback=""):
    if isinstance(inp, dict):
        for key in ("question", "prompt", "message", "text"):
            value = inp.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in inp.values():
            if isinstance(value, str) and value.strip():
                return value.strip()
    if fallback:
        lines = [line.strip() for line in fallback.splitlines() if line.strip()]
        if lines:
            return lines[0]
    return "O agente pediu uma resposta."


def normalize_user_questions(inp, fallback=""):
    """Converte shapes conhecidos de AskUserQuestion em perguntas neutras.

    O contrato é deliberadamente pequeno para servir também a outros backends:
    texto, cabeçalho opcional, opções e seleção simples/múltipla.
    """
    source = inp if isinstance(inp, dict) else {}
    raw_questions = source.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        raw_questions = [source]

    questions = []
    for index, raw in enumerate(raw_questions, 1):
        raw = raw if isinstance(raw, dict) else {"question": str(raw)}
        options = []
        for option in raw.get("options", []) if isinstance(raw.get("options"), list) else []:
            if isinstance(option, str) and option.strip():
                options.append({"label": option.strip(), "description": ""})
            elif isinstance(option, dict):
                label = option.get("label") or option.get("value") or option.get("name")
                if isinstance(label, str) and label.strip():
                    description = option.get("description")
                    options.append({
                        "label": label.strip(),
                        "description": description.strip() if isinstance(description, str) else "",
                    })
        questions.append({
            "id": str(raw.get("id") or raw.get("header") or f"question-{index}"),
            "header": str(raw.get("header") or "").strip(),
            "question": _question_text(raw, fallback if index == 1 else ""),
            "options": options,
            "multiple": bool(raw.get("multiSelect") or raw.get("multiple")),
        })
    return questions


def _read_question_answer(question):
    options = question["options"]
    if not options:
        sys.stdout.write(f"{DIM}> {RESET}")
        sys.stdout.flush()
        return sys.stdin.readline().rstrip("\n") or None

    for index, option in enumerate(options, 1):
        detail = f" — {option['description']}" if option["description"] else ""
        sys.stdout.write(f"  {YELLOW}{index}.{RESET} {option['label']}{DIM}{detail}{RESET}\n")
    hint = "números separados por vírgula" if question["multiple"] else "número"
    while True:
        sys.stdout.write(f"{DIM}> {hint} (vazio cancela): {RESET}")
        sys.stdout.flush()
        raw = sys.stdin.readline().strip()
        if not raw:
            return None
        parts = [part.strip() for part in raw.split(",")]
        if not question["multiple"] and len(parts) != 1:
            sys.stdout.write(f"{DIM}Escolha apenas uma opção.{RESET}\n")
            continue
        try:
            indexes = [int(part) for part in parts]
        except ValueError:
            indexes = []
        if indexes and len(set(indexes)) == len(indexes) and all(1 <= item <= len(options) for item in indexes):
            return ", ".join(options[item - 1]["label"] for item in indexes)
        sys.stdout.write(f"{DIM}Escolha entre 1 e {len(options)}.{RESET}\n")


def ask_user_questions(tool_name, questions):
    """Renderiza perguntas normalizadas e devolve respostas por id."""
    sys.stdout.write(f"\n{BG_USER}{WHITE}  pergunta  {RESET}  {YELLOW}{tool_name or 'AskUser'}{RESET}\n")
    answers = {}
    with _cooked_stdin():
        for question in questions:
            if question["header"]:
                sys.stdout.write(f"{DIM}{question['header']}{RESET}\n")
            sys.stdout.write(f"{question['question']}\n")
            answer = _read_question_answer(question)
            if answer is None:
                sys.stdout.write("\n")
                sys.stdout.flush()
                return None
            answers[question["id"]] = (
                [part.strip() for part in answer.split(",")]
                if question["multiple"] else [answer]
            )
    sys.stdout.write("\n")
    sys.stdout.flush()
    return answers


def _handle_user_question(tool_name, inp=None, result_text=""):
    """Fallback para ferramentas de pergunta que o CLI não suporta em --print.

    O Frollo não executa a tool no lugar do Claude CLI; coleta a resposta humana
    e devolve como contexto de retry no próximo turno.
    """
    questions = normalize_user_questions(inp or {}, result_text)
    answers = ask_user_questions(tool_name, questions)
    if answers is None:
        return None
    if len(answers) == 1:
        return ", ".join(next(iter(answers.values())))
    return "\n\n".join(
        f"{question['question']}\n{', '.join(answers[question['id']])}"
        for question in questions
    )


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
    if not _write_stdin(proc, resp + "\n"):
        return False
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
        return _write_stdin(proc, "a\n")
    elif ch == 'y':
        sys.stdout.write(f"y  {DIM}(permitido){RESET}\n\n")
        sys.stdout.flush()
        return _write_stdin(proc, "y\n")
    else:
        sys.stdout.write(f"n  {DIM}(negado){RESET}\n\n")
        sys.stdout.flush()
        return False
