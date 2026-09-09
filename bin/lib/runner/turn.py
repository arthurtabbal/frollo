"""Máquina de estados de um turno: consome linhas do stream-json e despacha por tipo.

Extraído de `run_turn` (Fase 2 do PLANO_MELHORIAS.md) — mesmo comportamento, estado que
antes eram ~30 variáveis locais/closures agora são atributos de `Turn`. `run_turn`
(`runner/__init__.py`) fica responsável por spawnar o subprocess, alimentar
`Turn.handle_line` a partir do loop `select` e finalizar (stats/cota/restore).
"""
import json
import re
import sys
import time
from datetime import datetime

from ..theme import (
    DIM, RESET, YELLOW,
    _F, _GLOW,
    THINKING_FG, THINKING_TS,
    CLEAR,
)
from ..tools import log_tool_call, log_tool_result, TOOLS_LOG, RUNDIR, _log, _ts
from ..gargulas import _gargula_comment
from .. import errors

from .assistant_text import AssistantTextRenderer
from .panes import THINKING_LOG, _resize_thinking
from .permissions import (
    _handle_permission,
    _handle_permission_ask,
    _handle_control_request,
    _handle_user_question,
    _write_stdin,
)
from .protocol import make_event
from .stats import _fmt_tok

_STDOUT_JSON_BUFFER_CAP = 64 * 1024


def _tool_result_text(content):
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text" and item.get("text")
        )
    return str(content or "")


def _looks_like_user_question_tool(tool_name, tool_input, err_text):
    name = (tool_name or "").lower()
    text = (err_text or "").lower()
    if any(part in name for part in ("ask", "question", "input")) and "user" in name:
        return True
    if "question" in (tool_input or {}):
        return True
    unavailable = (
        "not available in this environment",
        "not supported in this environment",
        "does not work in this environment",
        "doesn't work in this environment",
        "not available in non-interactive",
        "not supported in non-interactive",
    )
    asks_user = (
        "ask user",
        "ask the user",
        "askuser",
        "user question",
        "pergunta",
    )
    return any(marker in text for marker in unavailable) and any(marker in text for marker in asks_user)


def _escape_control_chars_in_json_strings(text):
    """Best-effort repair for CLI JSON split by literal newlines inside strings."""
    out = []
    in_string = False
    escaped = False
    for ch in text:
        if not in_string:
            out.append(ch)
            if ch == '"':
                in_string = True
            continue
        if escaped:
            out.append(ch)
            escaped = False
        elif ch == "\\":
            out.append(ch)
            escaped = True
        elif ch == '"':
            out.append(ch)
            in_string = False
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
    return "".join(out)


def _loads_stdout_json(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError as original:
        if any(ch in text for ch in "\n\r\t"):
            try:
                return json.loads(_escape_control_chars_in_json_strings(text))
            except json.JSONDecodeError:
                pass
        raise original


def _json_error_may_be_incomplete(exc, text):
    stripped = text.lstrip()
    if not stripped.startswith(("{", "[")):
        return False
    if len(text) > _STDOUT_JSON_BUFFER_CAP:
        return False
    msg = exc.msg.lower()
    return (
        exc.pos >= len(text) - 1
        or "unterminated string" in msg
        or "expecting value" in msg
        or "expecting ',' delimiter" in msg
        or "expecting property name" in msg
    )


class Turn:
    """Estado de um turno em andamento + dispatch de eventos do stream-json."""

    def __init__(self, client, proc, cfg, thinking_autoresize, max_think_lines, idle_lines, render):
        self.client = client
        self.proc = proc
        self.cfg = cfg
        self.thinking_autoresize = thinking_autoresize
        self._max_think_lines = max_think_lines
        self._idle_lines = idle_lines
        self.render = render

        self._tool_names = {}
        self._tool_inputs = {}
        self.start_time = time.time()
        self.turn_id = f"claude-turn-{int(self.start_time * 1000)}"
        self.frollo_events = []
        self._frollo_seq = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_creation_tokens = 0
        self.current_block = None
        self.current_block_item_id = None
        self._text_block_buffers = {}
        self.text_started = False
        self.fire_frame = 0
        self.assistant_text = AssistantTextRenderer(client, cfg, render)
        self.thinking_lines = idle_lines
        self.thinking_count = 0
        self.thinking_col = 0
        self.thinking_header_written = False
        self.thinking_visible = False
        self._empty_thinking_blocks = 0
        self._thinking_notice_written = False
        self.thinking_tokens_seen = 0
        self.spinner_shown = False
        self._suppress_perm_text = False
        self.perm_approved = False
        self.retry_requested = False
        self.turn_done = False
        self.unknown_event_types = set()
        self.rate_limited = False
        self.rate_limit_ts = 0.0
        self.rate_limit_retry = 0
        self.rate_limit_msg = ""
        self.rate_limit_reset_str = ""
        self.model_name = ""
        # Agregados do evento 'result' (fim do turno) — cobrem todos os requests à API
        # feitos durante o turno, ao contrário de input_tokens/output_tokens acima, que
        # são sobrescritos a cada message_start/delta e refletem só o último request
        # (um turno com N tool calls faz N+1 requests).
        self.result_cost = None
        self.result_in_tok = None
        self.result_out_tok = None
        self._stdout_json_buf = ""
        self._emit("turn.started", {"status": "in_progress"})

    # -- Frollo v0 events -------------------------------------------------

    def _provider(self):
        return {
            "name": "claude",
            "surface": "stream-json",
            "version": None,
            "model": self.model_name or getattr(self.client, "observed_model", None),
        }

    def _emit(self, kind, payload, *, item_id=None, raw=None):
        self._frollo_seq += 1
        event = make_event(
            kind,
            payload,
            seq=self._frollo_seq,
            provider=self._provider(),
            session_id=getattr(self.client, "session_id", None),
            turn_id=self.turn_id,
            item_id=item_id,
            raw=raw,
        )
        self.frollo_events.append(event)
        return event

    def _block_item_id(self, event):
        index = event.get("index")
        if index is None:
            return None
        return f"claude:block:{index}"

    # -- spinner ----------------------------------------------------------

    def _show_status(self):
        if self.client._streaming_text:
            return
        elapsed = time.time() - self.start_time
        tok     = self.input_tokens + self.output_tokens
        flame   = _F[self.fire_frame % len(_F)]
        glow    = _GLOW[self.fire_frame % len(_GLOW)]
        self.fire_frame += 1
        tok_part = f"· {_fmt_tok(tok)} tok " if tok else ""
        if not self.spinner_shown:
            sys.stdout.write('\n')
            self.spinner_shown = True
        if self.rate_limited:
            waiting = time.time() - self.rate_limit_ts
            if self.rate_limit_retry:
                remaining  = max(0, self.rate_limit_retry - waiting)
                reset_info = f"{self.rate_limit_reset_str}  {DIM}({remaining:.0f}s){RESET}" if self.rate_limit_reset_str else f"{remaining:.0f}s"
                wait_part  = f"retoma às {reset_info}"
            else:
                wait_part = f"aguardando {waiting:.0f}s"
            sys.stdout.write(f"\r\033[2K{YELLOW}⏳{RESET}  {YELLOW}rate limit{RESET}  {wait_part}")
        else:
            sys.stdout.write(
                f"\r\033[2K{flame}{RESET}  {glow}pensando…{RESET}  {DIM}{elapsed:.0f}s {tok_part}{RESET}"
            )
        sys.stdout.flush()

    def _clear_status(self):
        # Só apaga se o spinner está de fato visível. `_clear_status` é chamado
        # antes de CADA chunk de stdout (render._dispatch); com spinner já
        # apagado, um `\r\033[2K` aqui limparia a linha de resposta já digitada
        # pelo chunk anterior — daí texto "some" enquanto o typewriter avança.
        if self.spinner_shown:
            sys.stdout.write("\r\033[2K\033[1A\r\033[2K")
            self.spinner_shown = False
            sys.stdout.flush()

    # -- dispatch -----------------------------------------------------------

    def handle_line(self, raw):
        line = raw.rstrip("\r\n")
        if self._stdout_json_buf:
            text = self._stdout_json_buf + "\n" + line
        else:
            text = line.strip()
        if not text:
            return
        try:
            event = _loads_stdout_json(text)
        except json.JSONDecodeError as exc:
            if _json_error_may_be_incomplete(exc, text):
                self._stdout_json_buf = text
                return
            # stderr é um canal separado (_stderr_reader em run_turn) — uma linha
            # não-JSON no stdout é anomalia, não caminho esperado. Vai pro pane de
            # tools e pro errors.jsonl; fora do chat pra não picotar o texto do
            # assistente com ruído que raramente é acionável na hora. Mesmo code
            # que o backend Codex usa pro equivalente dele (codex.py) — é a mesma
            # falha de forma, canal diferente.
            errors.report(
                "claude/stdout", "linha não-JSON no stdout do claude",
                severity="warning", code="non_json_line", detail=text[:400],
                tmux_srv=getattr(self.client, "tmux_srv", None), chat=False,
            )
            return
        self._stdout_json_buf = ""
        self.handle_event(event)

    def flush_pending_json(self):
        if not self._stdout_json_buf:
            return False
        pending = self._stdout_json_buf
        self._stdout_json_buf = ""
        try:
            event = _loads_stdout_json(pending)
        except json.JSONDecodeError:
            errors.report(
                "claude/stdout", "JSON incompleto no stdout do claude",
                severity="warning", code="non_json_line", detail=pending[:400],
                tmux_srv=getattr(self.client, "tmux_srv", None), chat=False,
            )
            return True
        self.handle_event(event)
        return False

    def handle_event(self, event):
        etype = event.get("type")

        if etype == "stream_event":
            self._handle_stream_event(event.get("event", {}))
        elif etype == "assistant":
            self._handle_assistant(event)
        elif etype == "user":
            self._handle_user(event)
        elif etype == "control_request":
            self.render.suspend()
            try:
                _handle_control_request(event, self.proc, self.client.cwd)
            finally:
                self.render.resume()
        elif etype == "permission_request":
            self.render.suspend()
            try:
                approved = _handle_permission(event, self.proc)
                if not approved:
                    _write_stdin(self.proc, "n\n")
            finally:
                self.render.resume()
        elif etype == "result":
            self._handle_result(event)
        elif etype == "rate_limit_event":
            self._handle_rate_limit_event(event)
        elif etype not in ("system", None):
            with open(RUNDIR / "events.log", "a") as f:
                f.write(json.dumps(event) + "\n")
            # Lacuna de cobertura do protocolo, não falha do turno: fica no
            # errors.jsonl uma vez por tipo (report_once), sem gastar espaço de tela.
            errors.report_once(
                self.unknown_event_types, etype,
                "claude/protocolo", f"evento do stream-json não mapeado: {etype}",
                severity="warning", code="unknown_event_type", raw=event,
                chat=False, tools=False,
            )

    # -- stream_event ---------------------------------------------------

    def _handle_stream_event(self, e):
        et = e.get("type")

        if et == "message_start":
            self.rate_limited = False
            msg   = e.get("message", {})
            usage = msg.get("usage", {})
            self.input_tokens          = usage.get("input_tokens", 0)
            self.cache_read_tokens     = usage.get("cache_read_input_tokens", 0)
            self.cache_creation_tokens = usage.get("cache_creation_input_tokens", 0)
            self.model_name = msg.get("model", self.model_name)
            if self.model_name:
                self.client.observed_model = self.model_name
            self._emit("usage.updated", {
                "usage": {
                    "scope": "turn",
                    "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens,
                    "cached_input_tokens": self.cache_read_tokens,
                    "cache_creation_input_tokens": self.cache_creation_tokens,
                }
            }, raw=e)

        elif et == "message_delta":
            usage = e.get("usage", {})
            self.output_tokens = usage.get("output_tokens", 0)
            details = usage.get("output_tokens_details") or {}
            thinking_tokens = details.get("thinking_tokens")
            if isinstance(thinking_tokens, int):
                self.thinking_tokens_seen = max(self.thinking_tokens_seen, thinking_tokens)
            self._emit("usage.updated", {
                "usage": {
                    "scope": "turn",
                    "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens,
                    "cached_input_tokens": self.cache_read_tokens,
                    "cache_creation_input_tokens": self.cache_creation_tokens,
                    "reasoning_output_tokens": thinking_tokens,
                }
            }, raw=e)

        elif et == "content_block_start":
            block = e.get("content_block", {})
            self.current_block = block.get("type")
            self.current_block_item_id = self._block_item_id(e)
            if self.current_block == "thinking":
                # Header e resize são preguiçosos: só ocorrem quando chega texto
                # de thinking de fato (ver content_block_delta). Alguns requests
                # chegam só com signature_delta; a nota fica deferida para o fim do
                # turno, para um bloco vazio não apagar thinking visível anterior.
                self.thinking_header_written = False
                self._emit("reasoning.started", {
                    "status": "in_progress",
                    "visibility": "summary",
                    "summary_text": "",
                    "content_text": "",
                    "summary_parts": [],
                    "content_parts": [],
                }, item_id=self.current_block_item_id, raw=e)
            elif self.current_block == "text":
                if self.thinking_visible and self.thinking_autoresize:
                    _resize_thinking(self.client.tmux_srv, "summary")
                # Seta o flag antes de enfileirar — o render thread só desenha o
                # spinner quando isso é False, então essa ordem evita que ele
                # tente desenhar bem no instante em que o texto começa a sair.
                self.text_started = True
                self.assistant_text.start_block()
                if self.current_block_item_id:
                    self._text_block_buffers[self.current_block_item_id] = ""

        elif et == "content_block_delta":
            self._handle_content_block_delta(e.get("delta", {}))

        elif et == "content_block_stop":
            self._handle_content_block_stop()

    def _handle_content_block_delta(self, delta):
        dtype = delta.get("type")

        # Texto do thinking: modelos Claude atuais mandam thinking_delta; alguns
        # caminhos ainda podem mandar text_delta dentro do bloco thinking. Blocos
        # só com signature_delta ficam sem texto visível e são tratados no result.
        if dtype == "thinking_delta" or (dtype == "text_delta" and self.current_block == "thinking"):
            chunk_t = delta.get("thinking") or delta.get("text") or ""

            if chunk_t:
                self._emit("reasoning.delta", {
                    "delta": chunk_t,
                    "visibility": "summary",
                }, item_id=self.current_block_item_id, raw=delta)
                self.thinking_visible = True
                if not self.thinking_header_written:
                    _log(THINKING_LOG, f"{CLEAR}{THINKING_TS}[{_ts()}]{RESET}\n\033[40m{THINKING_FG}")
                    if self.thinking_autoresize:
                        _resize_thinking(self.client.tmux_srv, self._max_think_lines)
                        self.thinking_lines = self._max_think_lines
                    self.thinking_header_written = True

                def _on_newline():
                    self.thinking_count += 1
                    self.thinking_col    = 0

                for ch in chunk_t:
                    if ch == "\n":
                        self.thinking_col = 0
                    else:
                        self.thinking_col += 1
                        if self.thinking_col % 80 == 0:
                            _on_newline()

                self.render.push_file(THINKING_LOG, chunk_t, delay=0.001, on_newline=_on_newline, hesitate=False)

        elif dtype == "text_delta":
            chunk = delta.get("text", "")
            if chunk:
                self._emit("message.assistant.delta", {
                    "role": "assistant",
                    "delta": chunk,
                }, item_id=self.current_block_item_id, raw=delta)
                if self.current_block_item_id:
                    self._text_block_buffers[self.current_block_item_id] = (
                        self._text_block_buffers.get(self.current_block_item_id, "") + chunk
                    )
            self.assistant_text.push_delta(chunk, suppress=self._suppress_perm_text)

    def _handle_content_block_stop(self):
        if self.current_block == "thinking":
            # Flush: sem isso, o fechamento (RESET/nota) escreveria no arquivo
            # antes do render thread terminar de animar o último chunk enfileirado.
            self.render.join()
            if self.thinking_header_written:
                _log(THINKING_LOG, f"{RESET}\n")
            else:
                self._empty_thinking_blocks += 1
            self._emit("reasoning.completed", {
                "status": "completed",
                "visibility": "summary",
                "summary_text": "",
                "content_text": "",
                "summary_parts": [],
                "content_parts": [],
            }, item_id=self.current_block_item_id)
        elif self.current_block == "text":
            self.assistant_text.finish(add_newline_if_mid_line=True)
            text = self._text_block_buffers.pop(self.current_block_item_id, "")
            self._emit("message.assistant.completed", {
                "role": "assistant",
                "text": text,
                "phase": "final_answer",
            }, item_id=self.current_block_item_id)
        self.current_block = None
        self.current_block_item_id = None

    # -- assistant / user -------------------------------------------------

    def _handle_assistant(self, event):
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                log_tool_call(block, self.client.nvim_pane, self.client.tmux_srv, self.client.editor_bin, render=self.render)
                tool_id = block.get("id", "")
                tool_name = block.get("name", "?")
                tool_input = block.get("input") or {}
                self._tool_names[tool_id] = tool_name
                self._tool_inputs[tool_id] = tool_input
                if tool_name == "Bash":
                    self._emit("command.started", {
                        "status": "in_progress",
                        "command": {
                            "command": tool_input.get("command"),
                            "cwd": self.client.cwd,
                            "source": "claude_tool",
                        },
                    }, item_id=tool_id, raw=block)
                elif tool_name in ("Edit", "MultiEdit", "Write"):
                    self._emit("file.change.started", {
                        "status": "in_progress",
                        "file": {
                            "path": tool_input.get("file_path") or tool_input.get("path"),
                            "operation": "add" if tool_name == "Write" else "update",
                        },
                    }, item_id=tool_id, raw=block)
                else:
                    self._emit("tool.started", {
                        "status": "in_progress",
                        "tool": {
                            "name": tool_name,
                            "input": tool_input,
                        },
                    }, item_id=tool_id, raw=block)

    def _handle_user(self, event):
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_result":
                tool_id = block.get("tool_use_id", "")
                tool_name = self._tool_names.get(tool_id, "")
                tool_input = self._tool_inputs.get(tool_id, {})
                result_text = _tool_result_text(block.get("content", ""))
                if block.get("is_error"):
                    err_text = result_text
                    if ("requested permissions" in err_text or "haven't granted" in err_text
                            or "requires approval" in err_text):
                        self.render.suspend()
                        try:
                            if _handle_permission_ask(tool_name, self.client.cwd):
                                self.perm_approved = True
                                self.retry_requested = True
                                self.client._retry_context = f"({tool_name} aprovado — prossiga)"
                        finally:
                            self.render.resume()
                        self._suppress_perm_text = True
                    elif _looks_like_user_question_tool(tool_name, tool_input, err_text):
                        self.render.suspend()
                        try:
                            answer = _handle_user_question(tool_name, tool_input, err_text)
                        finally:
                            self.render.resume()
                        self.retry_requested = True
                        self.client._retry_context = (
                            "Resposta do usuário à sua pergunta anterior:\n\n"
                            f"{answer if answer else '(sem resposta textual)'}"
                        )
                        self._suppress_perm_text = True
                log_tool_result(block)
                if tool_name == "Bash":
                    exit_match = re.search(r'exit code[:\s]+([1-9]\d*)', result_text, re.IGNORECASE)
                    command_failed = bool(block.get("is_error") or exit_match)
                    self._emit("command.failed" if command_failed else "command.finished", {
                        "status": "failed" if command_failed else "completed",
                        "command": {
                            "command": tool_input.get("command"),
                            "cwd": self.client.cwd,
                            "source": "claude_tool",
                            "output": result_text,
                            "exit_code": int(exit_match.group(1)) if exit_match else None,
                            "duration_ms": None,
                        },
                    }, item_id=tool_id, raw=block)
                elif tool_name in ("Edit", "MultiEdit", "Write"):
                    self._emit("file.change.finished", {
                        "status": "failed" if block.get("is_error") else "completed",
                        "file": {
                            "path": tool_input.get("file_path") or tool_input.get("path"),
                            "operation": "add" if tool_name == "Write" else "update",
                            "diff": None,
                        },
                    }, item_id=tool_id, raw=block)
                elif tool_name:
                    self._emit("tool.failed" if block.get("is_error") else "tool.finished", {
                        "status": "failed" if block.get("is_error") else "completed",
                        "tool": {
                            "name": tool_name,
                            "output": result_text,
                        },
                    }, item_id=tool_id, raw=block)
                _blk_tool = tool_name
                if _blk_tool == "Bash" and not block.get("is_error"):
                    _blk_text = result_text
                    if re.search(r'exit code[:\s]+([1-9]\d*)', _blk_text, re.IGNORECASE):
                        if self.cfg.get("gargoyles", True):
                            _g_prefix, _g_fala = _gargula_comment("bash_error", force=True)
                            if _g_prefix:
                                _log(TOOLS_LOG, '\n')
                                _log(TOOLS_LOG, _g_prefix)
                                self.render.push_file(TOOLS_LOG, _g_fala)
                                self.render.join()
                                _log(TOOLS_LOG, '\n')

    # -- result / rate limit -------------------------------------------------

    def _handle_result(self, event):
        sid = event.get("session_id")
        if sid:
            self.client.session_id = sid
        failed = bool(event.get("is_error") or (event.get("subtype") or "success") != "success")
        if event.get("is_error") or (event.get("subtype") or "success") != "success":
            # O CLI encerra o turno com is_error/subtype quando a execução falhou
            # (erro de API, tool travada, max_turns). Antes o turno simplesmente
            # terminava sem texto e sem explicação.
            self.render.join()
            self._clear_status()
            errors.report(
                "claude", event.get("result") or "turno terminou com erro",
                code=event.get("subtype"), raw=event,
                tmux_srv=getattr(self.client, "tmux_srv", None), render=self.render,
            )
        if "total_cost_usd" in event:
            self.result_cost = event["total_cost_usd"]
        _r_usage = event.get("usage")
        if _r_usage:
            self.result_in_tok  = _r_usage.get("input_tokens")
            self.result_out_tok = _r_usage.get("output_tokens")
            self._emit("usage.updated", {
                "usage": {
                    "scope": "turn",
                    "input_tokens": self.result_in_tok,
                    "output_tokens": self.result_out_tok,
                    "cached_input_tokens": _r_usage.get("cache_read_input_tokens"),
                    "cache_creation_input_tokens": _r_usage.get("cache_creation_input_tokens"),
                    "total_cost_usd": self.result_cost,
                }
            }, raw=event)
        self._emit_deferred_thinking_notice()
        self._emit("turn.failed" if failed else "turn.finished", {
            "status": "failed" if failed else "completed",
            "duration_ms": int((time.time() - self.start_time) * 1000),
            "error": event.get("result") if failed else None,
        }, raw=event)
        # 'result' delimita o fim do turno no protocolo -- em processo persistente
        # (Fase 4) o processo continua vivo e não fecha stdout sozinho depois disso,
        # então o loop de ingestão em run_turn não pode esperar EOF.
        self.turn_done = True

    def _emit_deferred_thinking_notice(self):
        if self._thinking_notice_written or self.thinking_visible or not self._empty_thinking_blocks:
            return
        if self.thinking_tokens_seen == 0:
            note = "— sem thinking visível nesse turno (thinking_tokens=0)"
        else:
            note = "— o modelo omitiu o thinking (display:omitted)"
        _log(THINKING_LOG, f"{CLEAR}{THINKING_TS}[{_ts()}]{RESET}  {DIM}{note}{RESET}\n")
        self._thinking_notice_written = True

    def _handle_rate_limit_event(self, event):
        self.rate_limited  = True
        self.rate_limit_ts = time.time()
        with open(RUNDIR / "rate-limit.log", "a") as _rl:
            _rl.write(json.dumps(event) + "\n")
        self.rate_limit_retry = event.get("retryAfter", event.get("retry_after", 0))
        if self.rate_limit_retry:
            reset_dt = datetime.fromtimestamp(self.rate_limit_ts + self.rate_limit_retry)
            today    = datetime.now().date()
            self.rate_limit_reset_str = reset_dt.strftime("%H:%M:%S" if reset_dt.date() == today else "%d/%m %H:%M:%S")
