"""Descoberta e seleção de modelo/effort pelos backends nativos."""
import json
from concurrent.futures import ThreadPoolExecutor
import re
import subprocess
import sys

from ..session import pick_session_entries


_CLAUDE_DISCOVERY_TIMEOUT = 10


def _claude_local_command(command, cwd, timeout=_CLAUDE_DISCOVERY_TIMEOUT):
    completed = subprocess.run(
        [
            "claude", "-p", f"/{command}", "--output-format", "json",
            "--no-session-persistence",
        ],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(detail or f"claude /{command} terminou com exit {completed.returncode}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"claude /{command} não devolveu JSON válido") from exc
    if payload.get("is_error"):
        raise RuntimeError(payload.get("result") or f"claude /{command} falhou")
    return str(payload.get("result") or "")


def discover_claude_models(cwd):
    """Pede ao slash command nativo o catálogo válido para esta conta/versão."""
    result = _claude_local_command("model", cwd)
    match = re.search(r"Available:\s*(.+?)(?:,?\s+or a full model ID)?\.?$", result, re.I)
    if not match:
        raise RuntimeError(f"resposta de /model não reconhecida: {result}")
    models = [item.strip().strip("`.") for item in match.group(1).split(",")]
    models = [item for item in models if item]

    # O catálogo lista aliases, mas omite a versão para a qual cada um aponta.
    # Resolver os aliases pelo próprio slash command mantém o picker alinhado à
    # conta/versão do CLI sem codificar um mapa que envelhece no Frollo.
    def resolve_display_name(model):
        selected = _claude_local_command(f"model {model}", cwd)
        quoted = re.search(r"`([^`]+)`", selected)
        if quoted:
            resolved = quoted.group(1).strip()
        else:
            resolved_match = re.match(
                r"((?:Sonnet|Opus|Haiku|Fable)\s+\d+(?:\.\d+)*)\b", selected, re.I,
            )
            resolved = resolved_match.group(1) if resolved_match else model
        if model.lower() in ("sonnet", "opus", "haiku", "fable"):
            return resolved
        return f"{model} · {resolved}"

    with ThreadPoolExecutor(max_workers=min(4, len(models))) as pool:
        display_names = list(pool.map(resolve_display_name, models))
    return [
        {"model": model, "display_name": display, "efforts": []}
        for model, display in zip(models, display_names)
    ]


def discover_claude_efforts(cwd):
    result = _claude_local_command("effort", cwd)
    match = re.search(r"<([^>]+)>", result)
    if not match:
        raise RuntimeError(f"resposta de /effort não reconhecida: {result}")
    return [item.strip() for item in match.group(1).split("|") if item.strip()]


def pick_model(models, backend_label, current=None):
    entries = []
    for item in models:
        model = item["model"]
        marker = "atual · " if current and model == current else ""
        default = "default · " if item.get("is_default") else ""
        efforts = item.get("efforts") or []
        suffix = f" · effort: {'/'.join(efforts)}" if efforts else ""
        entries.append((model, "", f"{marker}{default}{item.get('display_name') or model}{suffix}"))
    return _pick_entries(entries, f"Modelos {backend_label}")


def pick_effort(efforts, backend_label, current=None):
    entries = [
        (effort, "", f"{'atual · ' if effort == current else ''}{effort}")
        for effort in efforts
    ]
    return _pick_entries(entries, f"Effort {backend_label}")


def _pick_entries(entries, title):
    """Usa o picker existente sem apagar o histórico visível do chat."""
    if not entries:
        return None
    sys.stdout.write("\033[?1049h")
    sys.stdout.flush()
    try:
        return pick_session_entries(entries, title)
    finally:
        sys.stdout.write("\033[?1049l")
        sys.stdout.flush()
