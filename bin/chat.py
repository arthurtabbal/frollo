#!/usr/bin/env python3
"""
Claude multi-pane terminal client.
Consome stream-json e roteia eventos para panes tmux via arquivos de log.
"""

import os
import re
import subprocess
import sys
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


def _short_model(name):
    """Reduz 'claude-opus-4-7-20251022' → 'opus'. Aceita aliases já curtos."""
    if not name:
        return ""
    n = name.lower()
    for alias in ("opus", "sonnet", "haiku"):
        if alias in n:
            return alias
    return name


MODEL_ALIASES = ("opus", "sonnet", "haiku")
from lib.session import pick_session
from lib.input import InputReader
from lib.runner import run_turn, _terminate_proc
from lib import config as _config
from lib.configure import run_configure
from lib.usage import fetch_usage

RUNDIR       = Path(os.environ.get("CLAUDE_RUNDIR", "/tmp/claude-client"))
THINKING_LOG = RUNDIR / "thinking"
TOOLS_LOG    = RUNDIR / "tools"


class Mode(Enum):
    NORMAL = "normal"
    AUTO   = "auto"

MODES = [Mode.NORMAL, Mode.AUTO]


class ClaudeClient:
    def __init__(self, resume_id=None, model=None):
        self.resume_id = resume_id        # None = nova sessão, "" = --continue, "<id>" = --resume <id>
        self.session_id = None            # preenchido após o primeiro turno via evento result
        self.first_turn = True
        self.mode = Mode.NORMAL
        self.model = model                # None = default do claude CLI; senão alias/id passado pra --model
        self.observed_model = ""          # preenchido via stream events (message_start.model)
        self.cwd = os.getcwd()
        self.nvim_pane = os.environ.get("CLAUDE_NVIM_PANE", "")
        self.tmux_srv = os.environ.get("CLAUDE_TMUX_SRV", "")
        self.editor_bin = os.environ.get("CLAUDE_EDITOR_BIN", "")
        self.proc = None
        self._streaming_text = False  # True enquanto typewriter está ativo

        RUNDIR.mkdir(exist_ok=True)
        THINKING_LOG.write_text("")
        TOOLS_LOG.write_text("")

        self._mode_ref = [self.mode]
        self._input_reader = InputReader(self._mode_ref, prompt_provider=self._prompt)

    def _sync_mode(self):
        """Sincroniza self.mode com o _mode_ref compartilhado com InputReader."""
        self.mode = self._mode_ref[0]

    def _update_model_title(self):
        """Fixa o modelo atual no título da borda do pane de chat (chrome do tmux —
        sempre visível, não rola com o output). Mostra o pedido ou, na falta, o observado."""
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
        model = _short_model(self.model or self.observed_model) or "?"
        try:
            subprocess.run(
                ["tmux", "-L", self.tmux_srv, "select-pane", "-t", pane, "-T", f"▲ chat · {model}"],
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
        model_display = _short_model(self.model or self.observed_model)
        if model_display:
            model_badge = f"{PURPLE}{model_display}{RESET} "
        else:
            model_badge = ""
        return f"{model_badge}{badge} {WHITE}>_{RESET} "

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
        labels = [
            f"  {HEADER_TITLE}Claude Frollo Observer{R}",
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

    def _startup_stats(self):
        """No startup de um resume, restaura stats do último turno e atualiza cota async."""
        import threading
        import json as _json
        from lib.runner.stats import (
            _model_ctx_window, _render_quota_line, _render_ctx_line,
            _render_turn_line, _render_total_line, _render_no_data_lines,
        )

        stats_tty_file = RUNDIR / "stats_tty"
        if not stats_tty_file.exists():
            return
        stats_tty = stats_tty_file.read_text().strip()
        if not stats_tty:
            return

        cfg_dir = Path.home() / ".config" / "frollo"

        # ── carregar dados salvos ──────────────────────────────────────────
        sess = {}
        sess_file = cfg_dir / "last_session.json"
        if sess_file.exists():
            try: sess = _json.loads(sess_file.read_text())
            except Exception: pass

        quota = {}
        quota_file = cfg_dir / "last_quota.json"
        if quota_file.exists():
            try: quota = _json.loads(quota_file.read_text())
            except Exception: pass

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

        # ── atualizar cota em background ───────────────────────────────────
        def _bg():
            result = fetch_usage()
            if not result:
                return
            try:
                fd = os.open(stats_tty, os.O_WRONLY | os.O_NOCTTY)
                os.write(fd, ("\033[4;1H" + _render_quota_line(result)).encode())
                os.close(fd)
            except OSError:
                pass

        threading.Thread(target=_bg, daemon=True).start()

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
                    run_turn(self, f"[snapshot do estado atual do terminal]\n\n{snapshot}")
                    continue
                if user_input.strip() == "/paste":
                    content = self._paste()
                    if content:
                        sys.stdout.write('\n')
                        sys.stdout.flush()
                        run_turn(self, content)
                    continue
                if user_input.strip().startswith("/model"):
                    parts = user_input.strip().split(maxsplit=1)
                    if len(parts) == 1:
                        current = self.model or self.observed_model or "default"
                        sys.stdout.write(f"\n{DIM}modelo atual: {RESET}{_short_model(current) or current}\n")
                        sys.stdout.flush()
                    else:
                        choice = parts[1].strip().lower()
                        self.model = choice
                        self._update_model_title()
                        sys.stdout.write(f"\n{DIM}modelo → {RESET}{PURPLE}{_short_model(choice) or choice}{RESET}{DIM} (próximo turno){RESET}\n")
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
                while run_turn(self, user_input, images=images):
                    user_input = getattr(self, '_retry_context', '.')
                    images = None
                self._update_model_title()
            except KeyboardInterrupt:
                if self.proc and self.proc.poll() is None:
                    self.proc.kill()
                    self.proc.wait()
                # durante typewriter: preserva a linha e desce; durante spinner: limpa a linha
                if self._streaming_text:
                    sys.stdout.write(f"\n{DIM}cancelado{RESET}\n")
                else:
                    sys.stdout.write(f"\r\033[2K{DIM}cancelado{RESET}\n")
                self._streaming_text = False
                sys.stdout.flush()
                continue
            except EOFError:
                _terminate_proc(self.proc)
                print(f"\n{DIM}saindo...{RESET}")
                break
            except Exception as e:
                import traceback
                err = traceback.format_exc()
                err_log = RUNDIR / "err.log"
                with open(err_log, "a") as f:
                    f.write(err)
                if self.proc and self.proc.poll() is None:
                    self.proc.kill()
                    self.proc.wait()
                print(f"\n{YELLOW}erro inesperado (ver {err_log}):{RESET} {e}\n")
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
                       help="modelo do claude: alias (opus|sonnet|haiku) ou ID completo")
        mg = p.add_mutually_exclusive_group()
        for alias in MODEL_ALIASES:
            mg.add_argument(f"--{alias}", dest="model_alias", action="store_const", const=alias,
                            help=f"atalho para --model {alias}")
        args = p.parse_args()

        if args.model and args.model_alias:
            p.error("use --model OU um shortcut (--opus/--sonnet/--haiku), não ambos")
        model = args.model or args.model_alias or "sonnet"

        resume_id = None
        if args.resume is not None:
            if args.resume:
                resume_id = args.resume
            else:
                resume_id = pick_session(os.getcwd()) or ""

        _first_run = _config.is_first_run()
        if args.configure or _first_run:
            run_configure(first_run=_first_run)

        ClaudeClient(resume_id=resume_id, model=model).chat()
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
