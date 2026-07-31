#!/usr/bin/env python3
"""
Claude multi-pane terminal client.
Consome stream-json e roteia eventos para panes tmux via arquivos de log.
"""

import os
import json
import re
import subprocess
import sys
import threading
import time
import random
from enum import Enum
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.theme import (
    DIM, RESET, YELLOW, WHITE, PURPLE,
    CHAT_FG, TOOLS_BASH,
    HEADER_TITLE, HEADER_STONE, HEADER_DARK, HEADER_ROSE,
    _QUOTES,
)


MODEL_ALIASES = ("opus", "sonnet", "haiku", "fable")
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_MODEL = "sonnet"
DEFAULT_EFFORT = "high"


def _model_family_version(name):
    if not name:
        return "", ""
    n = name.lower().strip().replace(".", "-")
    parts = [p for p in n.split("-") if p]
    family_idx = None
    for i, part in enumerate(parts):
        if part in MODEL_ALIASES:
            family_idx = i
            break
    if family_idx is None:
        return "", ""
    family = parts[family_idx]
    version_parts = []
    for part in parts[family_idx + 1:]:
        if not part.isdigit() or len(part) >= 8:
            break
        version_parts.append(part)
        if len(version_parts) == 2:
            break
    if not version_parts:
        prev = []
        for part in reversed(parts[:family_idx]):
            if not part.isdigit() or len(part) >= 8:
                break
            prev.append(part)
            if len(prev) == 2:
                break
        version_parts = list(reversed(prev))
    return family, ".".join(version_parts)


def _short_model(name):
    """Reduz ids longos para 'opus 4.8', preservando a versão quando existir."""
    if not name:
        return ""
    family, version = _model_family_version(name)
    if family:
        return f"{family} {version}" if version else family
    return name


def _normalize_model_choice(name, version=None):
    """Aceita alias, alias+versão ou ID completo do Claude CLI."""
    model = (name or "").strip().lower()
    if not model:
        return ""
    ver = (version or "").strip().lower().lstrip("v").replace(".", "-")
    if ver and model in MODEL_ALIASES:
        return f"claude-{model}-{ver}"
    match = re.fullmatch(rf"({'|'.join(MODEL_ALIASES)})[- ]v?(\d+(?:[.-]\d+)*)", model)
    if match:
        return f"claude-{match.group(1)}-{match.group(2).replace('.', '-')}"
    return model

from lib.session import pick_session
from lib.input import InputReader
from lib.runner import run_turn, _terminate_proc
from lib.runner.capabilities import backend_names, backend_profile, supports
from lib.runner.codex import run_codex_turn
from lib import config as _config
from lib import errors
from lib.configure import run_configure
from lib.usage import fetch_usage

RUNDIR       = Path(os.environ.get("CLAUDE_RUNDIR", "/tmp/claude-client"))
THINKING_LOG = RUNDIR / "thinking"
TOOLS_LOG    = RUNDIR / "tools"
USAGE_REFRESH_DEFAULT_SECONDS = 5 * 60.0
USAGE_REFRESH_MIN_SECONDS = 60.0


def _usage_refresh_seconds():
    raw = os.environ.get("FROLLO_USAGE_REFRESH_SECONDS")
    if not raw:
        return USAGE_REFRESH_DEFAULT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return USAGE_REFRESH_DEFAULT_SECONDS
    return max(USAGE_REFRESH_MIN_SECONDS, value)


USAGE_REFRESH_SECONDS = _usage_refresh_seconds()


class Mode(Enum):
    NORMAL = "normal"
    AUTO   = "auto"

MODES = [Mode.NORMAL, Mode.AUTO]


class ClaudeClient:
    def __init__(self, resume_id=None, model=None, backend="claude", effort=None, agent=None):
        self.resume_id = resume_id        # None = nova sessão, "" = --continue, "<id>" = --resume <id>
        self.session_id = None            # preenchido após o primeiro turno via evento result
        self.first_turn = True
        self.backend = backend
        self.backend_profile = backend_profile(backend)
        self.mode = Mode.NORMAL
        self.model = model                # None = default do claude CLI; senão alias/id passado pra --model
        self.effort = effort              # None = default do backend; senão passado para --effort/turn.start
        self.agent = agent                # None = default do Claude; senão passado para --agent
        self.observed_model = ""          # preenchido via stream events (message_start.model)
        self.cwd = os.getcwd()
        self.nvim_pane = os.environ.get("CLAUDE_NVIM_PANE", "")
        self.tmux_srv = os.environ.get("CLAUDE_TMUX_SRV", "")
        self.editor_bin = os.environ.get("CLAUDE_EDITOR_BIN", "")
        self.proc = None
        self._streaming_text = False  # True enquanto typewriter está ativo
        self._usage_thread = None
        self._codex_usage_thread = None
        self._usage_errors_seen = set()

        RUNDIR.mkdir(exist_ok=True)
        THINKING_LOG.write_text("")
        TOOLS_LOG.write_text("")

        self._mode_ref = [self.mode]
        self._input_reader = InputReader(self._mode_ref, prompt_provider=self._prompt)

    def _backend_profile(self):
        profile = getattr(self, "backend_profile", None)
        if profile is None:
            profile = backend_profile(getattr(self, "backend", "claude"))
            self.backend_profile = profile
        return profile

    def _supports(self, capability):
        return supports(self._backend_profile(), capability)

    def _stats_tty(self):
        stats_tty_file = RUNDIR / "stats_tty"
        if not stats_tty_file.exists():
            return ""
        try:
            return stats_tty_file.read_text().strip()
        except OSError:
            return ""

    def _stats_pane(self):
        stats_pane_file = RUNDIR / "stats_pane"
        if not stats_pane_file.exists():
            return ""
        try:
            return stats_pane_file.read_text().strip()
        except OSError:
            return ""

    def _update_stats_title(self, email=None):
        if not self.tmux_srv:
            return
        pane = self._stats_pane()
        if not pane:
            return
        label = self._backend_profile()["label"]
        title = "〰 stats"
        if email:
            title = f"{title} · {email}"
        elif label != "claude":
            title = f"{title} · {label}"
        try:
            subprocess.run(
                ["tmux", "-L", self.tmux_srv, "select-pane", "-t", pane, "-T", title],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    def _quota_file(self):
        return _config.CONFIG_PATH.parent / "last_quota.json"

    def _load_cached_usage(self):
        if getattr(self, "_usage_cache_loaded", False):
            return getattr(self, "_last_usage", None)
        self._usage_cache_loaded = True
        quota_file = self._quota_file()
        try:
            usage = json.loads(quota_file.read_text())
            if usage:
                self._last_usage = usage
                self._last_usage_at = quota_file.stat().st_mtime
        except (OSError, ValueError, TypeError):
            pass
        return getattr(self, "_last_usage", None)

    def _store_usage(self, usage):
        if not usage:
            return
        self._last_usage = usage
        self._last_usage_at = time.time()
        try:
            quota_file = self._quota_file()
            quota_file.parent.mkdir(parents=True, exist_ok=True)
            quota_file.write_text(json.dumps(usage))
        except OSError:
            pass

    def _write_quota_line(self, usage=None):
        from lib.runner.stats import _render_quota_line

        stats_tty = self._stats_tty()
        if not stats_tty:
            return
        if usage is None:
            usage = getattr(self, "_last_usage", None)
        try:
            fd = os.open(stats_tty, os.O_WRONLY | os.O_NOCTTY)
            os.write(fd, ("\033[4;1H" + _render_quota_line(usage)).encode())
            os.close(fd)
        except OSError:
            pass

    def _fetch_usage_once(self):
        try:
            result = fetch_usage()
        except Exception as exc:
            errors.report_once(
                self._usage_errors_seen, "usage_fetch_failed",
                "frollo/cota", f"{type(exc).__name__}: {exc}",
                severity="warning", code="usage_fetch_failed",
                chat=False, tools=False,
            )
            return None
        if not result:
            errors.report_once(
                self._usage_errors_seen, "usage_unavailable",
                "frollo/cota", "não foi possível ler a cota da assinatura",
                severity="warning", code="usage_unavailable",
                chat=False, tools=False,
            )
            return None
        self._store_usage(result)
        self._write_quota_line(result)
        return result

    def _fetch_codex_usage_once(self):
        try:
            from lib.runner.codex import fetch_codex_usage
            result = fetch_codex_usage(self.cwd)
        except Exception as exc:
            errors.report_once(
                self._usage_errors_seen, "codex_usage_fetch_failed",
                "frollo/cota", f"{type(exc).__name__}: {exc}",
                severity="warning", code="codex_usage_fetch_failed",
                chat=False, tools=False,
            )
            return None
        if not result:
            errors.report_once(
                self._usage_errors_seen, "codex_usage_unavailable",
                "frollo/cota", "não foi possível ler a cota do Codex",
                severity="warning", code="codex_usage_unavailable",
                chat=False, tools=False,
            )
            return None
        email = result.get("_account_email")
        if email:
            self._codex_account_email = email
        self._update_stats_title(getattr(self, "_codex_account_email", None))
        ctx = getattr(self, "_last_codex_ctx", None)
        if ctx:
            from lib.runner.codex import _write_codex_ctx_line
            _write_codex_ctx_line(self, ctx.get("used", 0), ctx.get("max", 0))
        self._last_codex_usage = result
        self._write_quota_line(result)
        return result

    def _usage_loop(self):
        while True:
            last = getattr(self, "_last_usage_at", 0.0)
            wait = max(0.0, USAGE_REFRESH_SECONDS - (time.time() - last)) if last else 0.0
            if wait:
                time.sleep(wait)
            self._fetch_usage_once()
            time.sleep(USAGE_REFRESH_SECONDS)

    def _codex_usage_loop(self):
        while True:
            self._fetch_codex_usage_once()
            time.sleep(USAGE_REFRESH_SECONDS)

    def _ensure_usage_updater(self):
        if self._backend_profile()["label"] != "claude":
            return
        if not self._stats_tty():
            return
        self._load_cached_usage()
        if self._usage_thread and self._usage_thread.is_alive():
            return
        self._usage_thread = threading.Thread(target=self._usage_loop, daemon=True)
        self._usage_thread.start()

    def _ensure_codex_usage_updater(self):
        if self._backend_profile()["label"] != "codex":
            return
        if not self._stats_tty():
            return
        if self._codex_usage_thread and self._codex_usage_thread.is_alive():
            return
        self._codex_usage_thread = threading.Thread(target=self._codex_usage_loop, daemon=True)
        self._codex_usage_thread.start()

    def _start_claude_usage_pane(self):
        if self._backend_profile()["label"] != "claude":
            return
        self._load_cached_usage()
        self._write_quota_line()
        self._ensure_usage_updater()

    def _start_codex_usage_pane(self):
        if self._backend_profile()["label"] != "codex":
            return
        self._write_quota_line(getattr(self, "_last_codex_usage", None))
        self._ensure_codex_usage_updater()

    def _start_usage_pane(self):
        if self._backend_profile()["label"] == "codex":
            self._start_codex_usage_pane()
        else:
            self._start_claude_usage_pane()

    def _sync_mode(self):
        """Sincroniza self.mode com o _mode_ref compartilhado com InputReader."""
        self.mode = self._mode_ref[0]

    def _model_for_display(self):
        if self._supports("model_selection"):
            return self.model or self.observed_model
        return self.observed_model

    def _status_parts(self):
        parts = []
        model = _short_model(self._model_for_display())
        if model:
            parts.append(model)
        if self._supports("effort_selection") and self.effort:
            parts.append(self.effort)
        if self._supports("agent_selection") and self.agent:
            parts.append(self.agent)
        return parts

    def _update_model_title(self):
        """Fixa status do backend no título da borda do pane de chat (chrome do tmux)."""
        if not self.tmux_srv:
            return
        pane = os.environ.get("TMUX_PANE", "")  # tmux exporta o pane do próprio cliente
        if not pane:
            try:
                pane = (RUNDIR / "chat_pane").read_text().strip()
            except OSError:
                return
        if not pane:
            return
        label = self._backend_profile()["label"]
        parts = self._status_parts() or ["?"]
        if label != "claude":
            parts.insert(0, label)
        title = "▲ chat · " + " · ".join(parts)
        try:
            subprocess.run(
                ["tmux", "-L", self.tmux_srv, "select-pane", "-t", pane, "-T", title],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    def _prompt(self):
        # Lê _mode_ref (não self.mode) porque InputReader muda o modo ao vivo via
        # Shift+Tab durante a edição, antes que _sync_mode() rode — self.mode ficaria
        # um passo atrasado no meio da digitação.
        if self._mode_ref[0] == Mode.AUTO:
            badge = f"{TOOLS_BASH}auto{RESET}"
        else:
            badge = f"{DIM}normal{RESET}"
        profile = self._backend_profile()
        provider_badge = f"{PURPLE}{profile['label']}{RESET} " if profile["label"] != "claude" else ""
        status_badges = "".join(f"{PURPLE}{part}{RESET} " for part in self._status_parts())
        return f"{provider_badge}{status_badges}{badge} {WHITE}>_{RESET} "

    def _print_header(self):
        R = RESET
        _strip = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b[a-zA-Z]')
        try:
            cwd_display = "~/" + Path(self.cwd).relative_to(Path.home()).as_posix()
        except ValueError:
            cwd_display = self.cwd
        art = [
            f"{HEADER_STONE}   ,             ,{R}",
            f"{HEADER_STONE}   :===.     .===:{R}",
            f"{HEADER_STONE}   |/V\\|     |/V\\|{R}",
            f"{HEADER_STONE}   ||||;  |  |||||{R}",
            f"{HEADER_STONE}   |||||__{HEADER_DARK}T{HEADER_STONE}__|||||{R}",
            f"{HEADER_STONE}   |;:;|.,.,.|;:;|{R}",
            f"{HEADER_STONE}   |/V\\|({HEADER_ROSE}{{o}}{HEADER_STONE})|/V\\|{R}",
            f"{HEADER_STONE}   ||||| `=' |||||{R}",
            f"{HEADER_STONE}   |;:;|:;;;:|:::|{R}",
            f'{HEADER_STONE}   |,".|,:::.|,".|{R}',
            f"{HEADER_STONE}   ||:|||:::|||:||{R}",
            f"{HEADER_DARK}---''\"'-'\"\"\"'-'\"''---{R}",
        ]
        quote = random.choice(_QUOTES)
        title = self._backend_profile()["title"]
        labels = [
            f"  {HEADER_TITLE}{title}{R}",
            f"  {DIM}Notre-Dame de Paris · 1482{R}",
            f"  {DIM}{cwd_display}{R}",
            f"  {DIM}Shift+Tab: alterna modo  Ctrl+C: sair{R}",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            f"  {DIM}{quote}{R}",
        ]
        label_width = 44
        sys.stdout.write("\n")
        for l, a in zip(labels, art):
            visible = len(_strip.sub("", l))
            pad = " " * max(0, label_width - visible)
            sys.stdout.write(l + pad + a + "\n")
        sys.stdout.write("\n")
        sys.stdout.flush()

    def _paste(self):
        paste_path = RUNDIR / "paste.txt"
        paste_path.write_text("")
        editor = os.environ.get("EDITOR", "nvim")
        try:
            subprocess.call([editor, str(paste_path)])
        except FileNotFoundError:
            sys.stdout.write(f"\n{DIM}editor '{editor}' não encontrado — defina $EDITOR{RESET}\n")
            sys.stdout.flush()
            return None
        content = paste_path.read_text().strip()
        if not content:
            sys.stdout.write(f"\n{DIM}paste vazio, ignorado{RESET}\n")
            sys.stdout.flush()
            return None
        lines = content.splitlines()
        preview = lines[0][:60] + ("…" if len(lines[0]) > 60 else "")
        suffix = f" {DIM}+{len(lines)-1} linhas{RESET}" if len(lines) > 1 else ""
        sys.stdout.write(f"\n{DIM}paste: {RESET}{preview}{suffix}\n")
        sys.stdout.flush()
        return content

    def _take_snapshot(self):
        """Captura estado do último turno: resposta do agente + tools + thinking."""
        _ansi = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b[a-zA-Z]')
        out_path = RUNDIR / "snapshot.txt"
        sections = []

        last_response = getattr(self, '_last_response_text', '').strip()
        if last_response:
            sections.append("=== última resposta ===\n" + last_response)

        for label, path in [("tools", TOOLS_LOG), ("thinking", THINKING_LOG)]:
            if path.exists():
                raw = _ansi.sub("", path.read_text()).strip()
                if raw:
                    sections.append(f"=== {label} ===\n" + raw[-3000:])

        content = "\n\n".join(sections) + "\n"
        out_path.write_text(content)
        return content

    def _interrupted_context_path(self):
        return RUNDIR / "interrupted_context.md"

    def _strip_ansi(self, text):
        return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b[a-zA-Z]', '', text or '')

    def _read_log_since(self, path, offset):
        try:
            size = path.stat().st_size
            start = offset if offset <= size else 0
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(start)
                return self._strip_ansi(f.read()).strip()
        except OSError:
            return ""

    def _begin_interrupted_turn_capture(self, message, images=None):
        self._interrupted_turn_capture = {
            "message": message,
            "attached_context": getattr(self, "_attached_interrupted_context", ""),
            "images": len(images or []),
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "tools_offset": TOOLS_LOG.stat().st_size if TOOLS_LOG.exists() else 0,
            "thinking_offset": THINKING_LOG.stat().st_size if THINKING_LOG.exists() else 0,
        }

    def _finish_interrupted_turn_capture(self):
        self._interrupted_turn_capture = None

    def _preserve_interrupted_turn(self, reason="ctrl_c"):
        capture = getattr(self, "_interrupted_turn_capture", None)
        if not capture:
            return False

        sections = [
            "=== turno anterior interrompido ===",
            f"motivo: {reason}",
            f"iniciado: {capture.get('started_at', '')}",
            "",
            "=== mensagem do usuário nesse turno ===",
            (capture.get("message") or "").strip(),
        ]
        attached_context = (capture.get("attached_context") or "").strip()
        if attached_context:
            sections += ["", "=== contexto interrompido já pendente antes desse turno ===", attached_context]
        if capture.get("images"):
            sections += ["", f"[{capture['images']} imagem(ns) foram enviadas nesse turno interrompido]"]

        last_response = getattr(self, "_last_response_text", "").strip()
        if last_response:
            sections += ["", "=== resposta parcial já exibida ===", last_response]

        tools = self._read_log_since(TOOLS_LOG, capture.get("tools_offset", 0))
        if tools:
            sections += ["", "=== tools observadas ===", tools]

        thinking = self._read_log_since(THINKING_LOG, capture.get("thinking_offset", 0))
        if thinking:
            sections += ["", "=== thinking visível no pane ===", thinking]

        content = "\n".join(sections).strip() + "\n"
        try:
            path = self._interrupted_context_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        except OSError:
            return False
        return True

    def _attach_interrupted_context(self, message):
        path = self._interrupted_context_path()
        try:
            pending = path.read_text().strip()
        except OSError:
            self._attached_interrupted_context = ""
            return message
        if not pending:
            self._attached_interrupted_context = ""
            return message
        self._attached_interrupted_context = pending
        return (
            "[contexto local preservado pelo Frollo: o turno anterior foi interrompido antes de "
            "o backend finalizar/persistir a resposta. Use isto para manter continuidade; não "
            "repita esse bloco ao usuário.]\n\n"
            f"{pending}\n\n"
            "=== nova mensagem do usuário ===\n"
            f"{message}"
        )

    def _clear_interrupted_context(self):
        try:
            self._interrupted_context_path().unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        self._attached_interrupted_context = ""

    def _startup_stats(self):
        """No startup de um resume, restaura stats do último turno e atualiza cota async."""
        if not self._supports("session_resume"):
            return
        from lib.runner.stats import (
            _model_ctx_window, _render_quota_line, _render_ctx_line,
            _render_turn_line, _render_total_line, _render_no_data_lines,
        )

        stats_tty = self._stats_tty()
        if not stats_tty:
            return

        cfg_dir = _config.CONFIG_PATH.parent

        # ── carregar dados salvos ──────────────────────────────────────────
        sess = {}
        sess_file = cfg_dir / "last_session.json"
        if sess_file.exists():
            try: sess = json.loads(sess_file.read_text())
            except Exception: pass

        quota = self._load_cached_usage()

        # ── renderizar as 4 linhas ─────────────────────────────────────────
        if sess:
            turn_line = _render_turn_line(
                sess.get('ts', ''), sess['input_tokens'], sess['output_tokens'],
                sess['elapsed'], sess['cost_turn'], sess.get('cache_read_tokens', 0),
            )
            total_line = _render_total_line(
                sess['total_input'], sess['total_output'], sess['total_elapsed'], sess['total_cost'],
            )
            _ctx_used = sess.get('ctx_tokens', 0)
            _ctx_max  = sess.get('ctx_max') or _model_ctx_window(sess.get('model', ''))
            ctx_line = _render_ctx_line(_ctx_used, _ctx_max)
        else:
            turn_line, total_line, ctx_line = _render_no_data_lines()

        quota_line = _render_quota_line(quota)

        try:
            content = "\033[H" + turn_line + "\n" + total_line + "\n" + ctx_line + "\n" + quota_line
            fd = os.open(stats_tty, os.O_WRONLY | os.O_NOCTTY)
            os.write(fd, content.encode())
            os.close(fd)
        except OSError:
            pass

        self._ensure_usage_updater()

    def _run_turn(self, message, images=None):
        user_message = message
        outbound_message = self._attach_interrupted_context(user_message)
        self._begin_interrupted_turn_capture(user_message, images=images)
        try:
            if self._backend_profile()["label"] == "codex":
                result = run_codex_turn(self, outbound_message, images=images)
            else:
                result = run_turn(self, outbound_message, images=images)
        except KeyboardInterrupt:
            raise
        else:
            self._finish_interrupted_turn_capture()
            self._clear_interrupted_context()
            return result

    def chat(self):
        self._print_header()
        self._update_model_title()
        if self.resume_id is not None:
            if self.resume_id:
                sys.stdout.write(f"{CHAT_FG}retomando sessão {self.resume_id[:8]}…{RESET}\n\n")
            else:
                sys.stdout.write(f"{CHAT_FG}retomando conversa anterior{RESET}\n\n")
            sys.stdout.flush()
            self._startup_stats()
        else:
            self._start_usage_pane()

        while True:
            try:
                self._sync_mode()
                sys.stdout.write('\n')
                sys.stdout.flush()
                _snapshot_buf = [None]
                def _pre_clear(text):
                    if text.strip() == '/snapshot':
                        _snapshot_buf[0] = self._take_snapshot()
                user_input = self._input_reader.read_input(MODES, pre_clear_hook=_pre_clear)
                pending_image = self._input_reader.pending_image
                self._input_reader.pending_image = None
                self._sync_mode()
                if not user_input.strip():
                    continue
                if user_input.strip() == "/snapshot":
                    snapshot = _snapshot_buf[0] or ""
                    sys.stdout.write(f"\n{DIM}snapshot capturado — enviando ao agente…{RESET}\n\n")
                    sys.stdout.flush()
                    self._run_turn(f"[snapshot do estado atual do terminal]\n\n{snapshot}")
                    continue
                if user_input.strip() == "/paste":
                    content = self._paste()
                    if content:
                        sys.stdout.write('\n')
                        sys.stdout.flush()
                        self._run_turn(content)
                    continue
                if user_input.strip().startswith("/model"):
                    if not self._supports("model_selection"):
                        current = self.observed_model or f"default do {self._backend_profile()['label']}"
                        sys.stdout.write(
                            f"\n{DIM}backend {self._backend_profile()['label']} não suporta troca de modelo pelo Frollo: "
                            f"{RESET}{current}\n"
                        )
                        sys.stdout.flush()
                        continue
                    parts = user_input.strip().split(maxsplit=1)
                    if len(parts) == 1:
                        current = self.model or self.observed_model or "default"
                        shown = _short_model(current) or current
                        detail = f"  {DIM}({current}){RESET}" if shown != current else ""
                        sys.stdout.write(f"\n{DIM}modelo atual: {RESET}{shown}{detail}\n")
                        sys.stdout.flush()
                    else:
                        tokens = parts[1].strip().split()
                        choice = _normalize_model_choice(tokens[0], tokens[1] if len(tokens) > 1 else None)
                        self.model = choice
                        self._update_model_title()
                        sys.stdout.write(
                            f"\n{DIM}modelo → {RESET}{PURPLE}{_short_model(choice) or choice}{RESET}"
                            f"{DIM}  ({choice}, próximo turno){RESET}\n"
                        )
                        sys.stdout.flush()
                    continue
                if user_input.strip().startswith("/effort"):
                    if not self._supports("effort_selection"):
                        sys.stdout.write(
                            f"\n{DIM}backend {self._backend_profile()['label']} não suporta troca de effort pelo Frollo{RESET}\n"
                        )
                        sys.stdout.flush()
                        continue
                    parts = user_input.strip().split(maxsplit=1)
                    if len(parts) == 1:
                        current = self.effort or f"default do {self._backend_profile()['label']}"
                        sys.stdout.write(f"\n{DIM}effort atual: {RESET}{current}\n")
                        sys.stdout.flush()
                    else:
                        choice = parts[1].strip().lower()
                        if choice not in EFFORT_LEVELS:
                            sys.stdout.write(
                                f"\n{YELLOW}effort inválido: {choice}{RESET}  "
                                f"{DIM}use {'/'.join(EFFORT_LEVELS)}{RESET}\n"
                            )
                            sys.stdout.flush()
                            continue
                        self.effort = choice
                        self._update_model_title()
                        sys.stdout.write(f"\n{DIM}effort → {RESET}{PURPLE}{choice}{RESET}{DIM} (próximo turno){RESET}\n")
                        sys.stdout.flush()
                    continue
                if user_input.strip().startswith("/agent") or user_input.strip() == "/advisor":
                    if not self._supports("agent_selection"):
                        sys.stdout.write(
                            f"\n{DIM}backend {self._backend_profile()['label']} não suporta agent/advisor pelo Frollo{RESET}\n"
                        )
                        sys.stdout.flush()
                        continue
                    if user_input.strip() == "/advisor":
                        choice = "advisor"
                    else:
                        parts = user_input.strip().split(maxsplit=1)
                        if len(parts) == 1:
                            current = self.agent or "default"
                            sys.stdout.write(f"\n{DIM}agent atual: {RESET}{current}\n")
                            sys.stdout.flush()
                            continue
                        choice = parts[1].strip().lower()
                    if choice in ("default", "none", "off"):
                        self.agent = None
                        shown = "default"
                    else:
                        self.agent = choice
                        shown = choice
                    self._update_model_title()
                    sys.stdout.write(f"\n{DIM}agent → {RESET}{PURPLE}{shown}{RESET}{DIM} (próximo turno){RESET}\n")
                    sys.stdout.flush()
                    continue
                if user_input.strip() == "/new":
                    sys.stdout.write(f"{DIM}novo contexto…{RESET}\n")
                    sys.stdout.flush()
                    # execvp substitui a imagem do processo sem rodar cleanup Python —
                    # em modo persistente self.proc pode seguir vivo (é o propósito da
                    # Fase 4); sem isso viraria órfão escrevendo num stdin sem leitor.
                    _terminate_proc(self.proc)
                    argv = sys.argv[:]
                    if "--resume" in argv:
                        i = argv.index("--resume")
                        argv = argv[:i] + argv[i+2:]
                    os.execvp(argv[0], argv)
                if user_input.strip() == "/refresh":
                    if not self._supports("session_resume"):
                        sys.stdout.write(
                            f"{YELLOW}backend {self._backend_profile()['label']} ainda não suporta /refresh ou --resume{RESET}\n\n"
                        )
                        sys.stdout.flush()
                        continue
                    if not self.session_id:
                        sys.stdout.write(f"{YELLOW}sem sessão ativa ainda{RESET}\n\n")
                        sys.stdout.flush()
                        continue
                    sys.stdout.write(f"{DIM}reiniciando…{RESET}\n")
                    sys.stdout.flush()
                    _terminate_proc(self.proc)
                    os.execvp(sys.argv[0], [sys.argv[0], "--resume", self.session_id])
                sys.stdout.write('\n')
                sys.stdout.flush()
                images = [pending_image] if pending_image else None
                while self._run_turn(user_input, images=images):
                    user_input = getattr(self, '_retry_context', '.')
                    images = None
                self._update_model_title()
            except KeyboardInterrupt:
                if self.proc and self.proc.poll() is None:
                    self.proc.kill()
                    self.proc.wait()
                preserved = self._preserve_interrupted_turn()
                # durante typewriter: preserva a linha e desce; durante spinner: limpa a linha
                if self._streaming_text:
                    sys.stdout.write(f"\n{DIM}cancelado{RESET}\n")
                else:
                    sys.stdout.write(f"\r\033[2K{DIM}cancelado{RESET}\n")
                if preserved:
                    sys.stdout.write(f"{DIM}contexto parcial preservado para o próximo turno{RESET}\n")
                self._finish_interrupted_turn_capture()
                self._streaming_text = False
                sys.stdout.flush()
                continue
            except EOFError:
                _terminate_proc(self.proc)
                print(f"\n{DIM}saindo...{RESET}")
                break
            except Exception as e:
                if self.proc and self.proc.poll() is None:
                    self.proc.kill()
                    self.proc.wait()
                preserved = self._preserve_interrupted_turn(reason="erro")
                self._finish_interrupted_turn_capture()
                errors.report_exception(
                    "frollo", e, severity="fatal", code="unexpected",
                    tmux_srv=self.tmux_srv,
                )
                if preserved:
                    sys.stdout.write(f"{DIM}contexto parcial preservado para o próximo turno{RESET}\n")
                sys.stdout.write(f"{DIM}histórico completo em {errors.ERROR_LOG}{RESET}\n")
                sys.stdout.flush()
                continue


if __name__ == "__main__":
    import argparse
    import traceback

    try:
        p = argparse.ArgumentParser(description="Claude multi-pane client")
        p.add_argument("--resume", "-r", nargs="?", const="", metavar="SESSION_ID",
                       help="retoma conversa: sem ID abre picker, com ID retoma direto")
        p.add_argument("--configure", action="store_true",
                       help="reconfigura preferências (typewriter, gárgulas, stats)")
        p.add_argument("--model", metavar="NAME",
                       help="modelo: alias (opus|sonnet|haiku|fable) ou ID completo")
        p.add_argument("--model-version", metavar="VERSION",
                       help="versão do alias em --model/shortcut (ex: 4.6 vira claude-sonnet-4-6)")
        p.add_argument("--effort", choices=EFFORT_LEVELS, default=DEFAULT_EFFORT,
                       help=f"nível de esforço/reasoning (default: {DEFAULT_EFFORT})")
        p.add_argument("--agent", metavar="NAME",
                       help="agent do Claude Code para a sessão (ex: advisor)")
        p.add_argument("--advisor", dest="agent_alias", action="store_const", const="advisor",
                       help="atalho para --agent advisor")
        p.add_argument("--backend", choices=backend_names(), default="claude",
                       help="backend experimental: claude (default) ou codex")
        mg = p.add_mutually_exclusive_group()
        for alias in MODEL_ALIASES:
            mg.add_argument(f"--{alias}", dest="model_alias", action="store_const", const=alias,
                            help=f"atalho para --model {alias}")
        args = p.parse_args()

        if args.model and args.model_alias:
            p.error("use --model OU um shortcut (--opus/--sonnet/--haiku/--fable), não ambos")
        if args.agent and args.agent_alias:
            p.error("use --agent OU --advisor, não ambos")
        profile = backend_profile(args.backend)
        if not supports(profile, "model_selection"):
            if args.model or args.model_alias or args.model_version:
                p.error(f"--backend {args.backend} ainda não suporta seleção de modelo")
        if not supports(profile, "effort_selection") and args.effort:
            p.error(f"--backend {args.backend} ainda não suporta seleção de effort")
        if not supports(profile, "agent_selection"):
            if args.agent or args.agent_alias:
                p.error(f"--backend {args.backend} ainda não suporta seleção de agent")
        if not supports(profile, "session_resume"):
            if args.resume is not None:
                p.error(f"--backend {args.backend} ainda não suporta --resume")
        if not supports(profile, "model_selection"):
            model = None
        else:
            model = _normalize_model_choice(args.model or args.model_alias or DEFAULT_MODEL, args.model_version)
        effort = args.effort if supports(profile, "effort_selection") else None
        agent = (args.agent or args.agent_alias) if supports(profile, "agent_selection") else None

        resume_id = None
        if args.resume is not None:
            if args.resume:
                resume_id = args.resume
            else:
                resume_id = pick_session(os.getcwd()) or ""

        _first_run = _config.is_first_run()
        if args.configure or _first_run:
            run_configure(first_run=_first_run)

        ClaudeClient(resume_id=resume_id, model=model, backend=args.backend, effort=effort, agent=agent).chat()
    except SystemExit:
        raise
    except (KeyboardInterrupt, EOFError):
        pass
    except BaseException as e:
        err = traceback.format_exc()
        RUNDIR.mkdir(exist_ok=True)
        with open(RUNDIR / "err.log", "w") as f:
            f.write(err)
        sys.stderr.write(f"\n\nERRO FATAL: {e}\n{err}\n")
        sys.stderr.write("\nPressione Enter para fechar...\n")
        sys.stderr.flush()
        try:
            input()
        except Exception:
            time.sleep(30)
