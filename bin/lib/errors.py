"""Sink único de erros do Frollo.

Regra do projeto: nenhum erro morre em silêncio. Todo caminho de falha — resposta
de erro do backend, processo que morreu, mensagem de protocolo sem handler,
exceção inesperada — passa por `report()`, que faz três coisas de uma vez:

1. appenda uma linha JSON em `~/.config/frollo/errors.jsonl` (histórico, sobrevive
   à sessão; override via `$FROLLO_ERROR_LOG`);
2. escreve uma linha no chat, onde o usuário está olhando;
3. escreve o detalhe no pane de tools e cresce o pane até o detalhe caber.

Nada aqui pode levantar exceção: o sink de erro que quebra transforma um erro
visível em dois erros invisíveis.
"""

import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from .theme import DIM, ERROR_FG, RESET, WARN_FG
from .tools import TOOLS_LOG, _log, _ts

ERROR_LOG = Path(os.environ.get(
    "FROLLO_ERROR_LOG",
    Path.home() / ".config" / "frollo" / "errors.jsonl",
))

_MAX_LOG_BYTES = 2 * 1024 * 1024   # rotaciona 1 geração, como hooks/log.sh
_MAX_RAW_CHARS = 2000
_CHAT_DETAIL_LINES = 4             # no chat cabe o suficiente pra decidir o que fazer
_TOOLS_DETAIL_LINES = 14           # no pane de tools cabe o suficiente pra depurar

SEVERITIES = ("warning", "error", "fatal")
_COLOR = {"warning": WARN_FG, "error": ERROR_FG, "fatal": ERROR_FG}
_ICON = {"warning": "!", "error": "✖", "fatal": "✖"}


def _truncate(raw):
    if raw is None:
        return ""
    if not isinstance(raw, str):
        try:
            raw = json.dumps(raw, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            raw = repr(raw)
    return raw[:_MAX_RAW_CHARS]


def _detail_lines(detail, limit):
    if not detail:
        return []
    lines = [ln.rstrip() for ln in str(detail).splitlines() if ln.strip()]
    if len(lines) <= limit:
        return lines
    return lines[:limit] + [f"… (+{len(lines) - limit} linhas em {ERROR_LOG})"]


def record(source, message, *, severity="error", code=None, detail=None, raw=None):
    """Monta o registro canônico de erro. Puro — não escreve em lugar nenhum."""
    return {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "severity": severity if severity in SEVERITIES else "error",
        "source": str(source or "frollo"),
        "code": code,
        "message": str(message or "erro sem mensagem"),
        "detail": str(detail or ""),
        "raw": _truncate(raw),
    }


def log(rec):
    """Appenda o registro no JSONL. Rotaciona 1 geração. Nunca levanta."""
    try:
        ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        if ERROR_LOG.exists() and ERROR_LOG.stat().st_size > _MAX_LOG_BYTES:
            ERROR_LOG.replace(ERROR_LOG.parent / (ERROR_LOG.name + ".1"))
        with open(ERROR_LOG, "a", buffering=1) as f:
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass


def chat_lines(rec):
    """Linhas ANSI que aparecem no chat. Separado de `report` para testar sem tty."""
    color = _COLOR[rec["severity"]]
    icon = _ICON[rec["severity"]]
    head = f"{color}{icon} {rec['source']}{RESET}  {rec['message']}"
    if rec["code"]:
        head += f"  {DIM}({rec['code']}){RESET}"
    out = [head]
    out += [f"{DIM}  {ln}{RESET}" for ln in _detail_lines(rec["detail"], _CHAT_DETAIL_LINES)]
    return out


def tools_text(rec):
    """Bloco que vai pro pane de tools, com o detalhe mais largo."""
    color = _COLOR[rec["severity"]]
    icon = _ICON[rec["severity"]]
    code = f" {DIM}[{rec['code']}]{RESET}" if rec["code"] else ""
    text = f"{DIM}{_ts()}{RESET}  {color}{icon}{RESET}  {color}{rec['source']}{RESET}{code}  {rec['message']}\n"
    for ln in _detail_lines(rec["detail"], _TOOLS_DETAIL_LINES):
        text += f"{DIM}     {ln}{RESET}\n"
    return text


def _grow_tools_pane(tmux_srv, lines):
    if not tmux_srv:
        return
    try:
        from .runner.panes import _grow_tools  # tardio: panes importa tools, errors não pode ciclar
    except ImportError:
        return
    _grow_tools(tmux_srv, lines)


def report(source, message, *, severity="error", code=None, detail=None, raw=None,
           tmux_srv=None, chat=True, tools=True, render=None):
    """Registra e exibe um erro. Devolve o registro. Nunca levanta.

    `chat=False` para degradações esperadas (ex.: cota indisponível) que devem
    ficar no arquivo sem poluir a conversa — mas nunca sumir.
    `render` é a RenderQueue do turno, quando houver: o typewriter é drenado antes
    de escrever no stdout, senão a linha de erro entra no meio de uma animação.
    """
    rec = record(source, message, severity=severity, code=code, detail=detail, raw=raw)
    log(rec)
    if tools:
        try:
            _log(TOOLS_LOG, tools_text(rec))
            needed = 6 + len(_detail_lines(rec["detail"], _TOOLS_DETAIL_LINES))
            _grow_tools_pane(tmux_srv, needed)
        except OSError:
            pass
    if chat:
        try:
            if render is not None:
                render.join()
            sys.stdout.write("\n" + "\n".join(chat_lines(rec)) + "\n")
            sys.stdout.flush()
        except (OSError, ValueError):
            pass
    return rec


def report_exception(source, exc, *, severity="error", code=None, **kwargs):
    """Atalho para caminhos de `except`: o traceback vira o detalhe."""
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return report(source, f"{type(exc).__name__}: {exc}", severity=severity,
                  code=code, detail=detail, **kwargs)
