import json
import os
import select
import sys
import termios
import time
import traceback
import tty
from datetime import datetime
from pathlib import Path

from .theme import BG_USER, DIM, RESET, WHITE
from .tools import RUNDIR


def _sessions_dir(cwd):
    project_key = cwd.replace('/', '-')
    return Path.home() / ".claude" / "projects" / project_key


def pick_session(cwd):
    """Picker interativo de sessões anteriores. Retorna session ID ou None."""
    try:
        return _pick_session_impl(cwd)
    except Exception:
        try:
            with open(RUNDIR / "err.log", "a") as f:
                f.write(f"[{time.strftime('%F %T')}] session picker: {traceback.format_exc()}\n")
        except OSError:
            pass
        return None


def _first_user_text(ev):
    """Extrai texto de um evento de sessão, cobrindo dois schemas conhecidos:
    queue-operation/enqueue (schema antigo) e message.content de type=="user"
    (schema atual, string ou lista de blocos). Retorna "" se não reconhecido."""
    if ev.get("isMeta"):
        return ""

    if ev.get("type") == "queue-operation" and ev.get("operation") == "enqueue":
        content = (ev.get("content") or "").strip()
        if _is_session_preview_noise(content):
            return ""
        return content

    if ev.get("type") == "user":
        msg_content = ev.get("message", {}).get("content", "")
        if isinstance(msg_content, list):
            msg_content = next(
                (b.get("text", "") for b in msg_content if b.get("type") == "text"), ""
            )
        content = (msg_content or "").strip()
        if _is_session_preview_noise(content):
            return ""
        return content
    return ""


def _is_session_preview_noise(content):
    """Mensagens internas do CLI não devem virar título no picker."""
    return content.startswith((
        "<local-command-caveat>",
        "<command-name>",
    ))


def _visible_user_text(content):
    """Remove envelopes internos que o Frollo anexa ao prompt real.

    O backend precisa do contexto interrompido, mas o histórico visual deve
    reconstruir o que a pessoa digitou, não reimprimir esse transporte interno.
    """
    content = (content or "").strip()
    context_marker = "[contexto local preservado pelo Frollo:"
    message_marker = "=== nova mensagem do usuário ==="
    context_pos = content.find(context_marker)
    if context_pos >= 0 and message_marker in content:
        # No Codex, imagens são itens anteriores ao texto; mantenha apenas esses
        # marcadores visuais ao remover o envelope de continuidade.
        prefix = "\n".join(
            line for line in content[:context_pos].splitlines() if line.strip() == "[imagem]"
        )
        content = content.rsplit(message_marker, 1)[1].strip()
        if prefix:
            content = prefix + "\n" + content
    return "" if _is_session_preview_noise(content) else content


def _content_text(content, *, image_label=False):
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and block.get("text"):
            parts.append(str(block["text"]).strip())
        elif image_label and block.get("type") in ("image", "localImage"):
            parts.append("[imagem]")
    return "\n".join(part for part in parts if part)


def _append_message(messages, role, text):
    text = (text or "").strip()
    if not text:
        return
    if messages and messages[-1][0] == role:
        if messages[-1][1] == text:
            return
        messages[-1] = (role, messages[-1][1] + "\n\n" + text)
        return
    messages.append((role, text))


def _session_messages(events):
    """Normaliza um transcript Claude em ``[(role, text)]`` para exibição."""
    has_user_events = any(
        isinstance(ev, dict) and ev.get("type") == "user" and not ev.get("isMeta")
        for ev in events
    )
    messages = []
    for ev in events:
        if not isinstance(ev, dict) or ev.get("isMeta") or ev.get("isSidechain"):
            continue
        typ = ev.get("type")
        if typ == "queue-operation" and ev.get("operation") == "enqueue":
            if has_user_events:
                continue
            _append_message(messages, "user", _visible_user_text(ev.get("content")))
            continue
        if typ not in ("user", "assistant"):
            continue
        content = (ev.get("message") or {}).get("content", "")
        text = _content_text(content, image_label=(typ == "user"))
        if typ == "user":
            text = _visible_user_text(text)
        _append_message(messages, typ, text)
    return messages


def load_session_messages(cwd, session_id=None):
    """Carrega falas visíveis de uma sessão Claude sem alterar o contexto.

    ``session_id=None`` representa o ``--continue`` do CLI e escolhe o JSONL
    mais recente do projeto. Falhas de I/O sobem para o chamador reportar pelo
    sink único de erros.
    """
    sessions_dir = _sessions_dir(cwd)
    if session_id:
        path = (sessions_dir / f"{session_id}.jsonl").resolve()
        if path.parent != sessions_dir.resolve():
            raise ValueError("id de sessão inválido")
        candidates = [path]
    else:
        candidates = sorted(
            sessions_dir.glob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True
        )
    if not candidates or not candidates[0].exists():
        raise FileNotFoundError("transcript da sessão Claude não encontrado")

    events = []
    with open(candidates[0]) as fp:
        for line in fp:
            try:
                events.append(json.loads(line))
            except (json.JSONDecodeError, TypeError):
                continue
    return _session_messages(events)


def _load_sessions(sessions_dir, limit=20):
    """Varre os .jsonl de um diretório de projeto e monta [(session_id, ts_str, first_msg)],
    mais recentes primeiro. Função pura (sem I/O de terminal) — testável isoladamente."""
    files = sorted(sessions_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)

    sessions = []
    for f in files[:limit]:
        session_id = f.stem
        first_msg = ""
        ts_str = ""
        try:
            ts_str = datetime.fromtimestamp(f.stat().st_mtime).strftime("%d/%m %H:%M")
            with open(f) as fp:
                for line in fp:
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    content = _first_user_text(ev)
                    if content:
                        first_msg = content.replace("\n", " ")
                        break
        except Exception:
            pass
        if first_msg:
            sessions.append((session_id, ts_str, first_msg))
    return sessions


def _pick_session_impl(cwd):
    sessions_dir = _sessions_dir(cwd)
    if not sessions_dir.exists():
        return None

    sessions = _load_sessions(sessions_dir)
    if not sessions:
        return None

    return pick_session_entries(sessions, "Sessões anteriores")


def pick_session_entries(sessions, title="Sessões anteriores"):
    """Escolhe uma entrada ``(id, data, preview)`` numa lista já carregada."""
    if not sessions:
        return None

    selected = 0
    try:
        cols = os.get_terminal_size().columns
    except OSError:
        cols = 80

    def render():
        sys.stdout.write('\033[2J\033[H')
        sys.stdout.write(f"{DIM}{title} — ↑↓ navegar · Enter confirmar · Esc cancelar{RESET}\r\n\r\n")
        for i, (sid, ts, msg) in enumerate(sessions):
            preview = msg[:65] + "…" if len(msg) > 65 else msg
            if i == selected:
                pad = max(0, cols - len(f"  {ts}  {preview}  ") - 2)
                sys.stdout.write(f"  {BG_USER}{WHITE}{ts}  {preview}{' ' * pad}{RESET}\r\n")
            else:
                sys.stdout.write(f"  {DIM}{ts}{RESET}  {preview}\r\n")
        sys.stdout.flush()

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    result = None
    try:
        tty.setraw(fd)
        render()
        while True:
            b = os.read(fd, 1)
            if b in (b'\r', b'\n'):
                result = sessions[selected][0]
                break
            elif b in (b'q', b'\x03'):
                break
            elif b == b'\x1b':
                ready, _, _ = select.select([sys.stdin], [], [], 0.05)
                if ready:
                    rest = os.read(fd, 8)
                    if rest.startswith(b'[A') and selected > 0:
                        selected -= 1
                        render()
                    elif rest.startswith(b'[B') and selected < len(sessions) - 1:
                        selected += 1
                        render()
                else:
                    break  # Esc
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write('\033[2J\033[H')
        sys.stdout.flush()

    return result
