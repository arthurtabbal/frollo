"""Experimental Codex App Server backend.

This is the Phase 0.5 bridge: it speaks Codex App Server JSONL and renders a
small subset through the existing Frollo panes. The canonical protocol shape is
represented internally as Frollo v0 events, but this is not yet the final adapter
architecture.
"""
import base64
import json
import os
import queue
import re
import select
import subprocess
import sys
import termios
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from ..theme import CHAT_FG, CLEAR, DIM, RESET, THINKING_FG, THINKING_TS, YELLOW
from ..tools import RUNDIR, TOOLS_LOG, _log, _ts, log_tool_call, log_tool_result
from .panes import _window_height, _resize_thinking, THINKING_LOG
from .permissions import _raw_stdin
from .render import RenderQueue
from .stats import _model_ctx_window, _render_ctx_line, _render_quota_line, _render_total_line, _render_turn_line
from .text import reset_col


SCHEMA = "frollo.event.v0"
_CODEX_REASONING_SUMMARY = "detailed"
_CODEX_LINUX_SANDBOX_WARNING = (
    "Codex's Linux sandbox uses bubblewrap and needs access to create user namespaces."
)


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_codex_version(user_agent):
    if not user_agent:
        return None
    match = re.search(r"/([0-9]+(?:\.[0-9]+)+)", user_agent)
    return match.group(1) if match else None


def _text_from_parts(parts):
    return "\n".join(_text_part_texts(parts))


def _text_part_texts(parts):
    if not parts:
        return []
    if isinstance(parts, str):
        return [parts]
    out = []
    for part in parts:
        if isinstance(part, str):
            text = part
        elif isinstance(part, dict):
            text = part.get("text") or ""
        else:
            text = ""
        if text:
            out.append(text)
    return out


def _codex_image_suffix(media_type):
    if media_type == "image/jpeg":
        return ".jpg"
    return ".png"


def _codex_input_items(message, images=None):
    items = []
    if images:
        RUNDIR.mkdir(parents=True, exist_ok=True)
        stamp = int(time.time() * 1000)
        for idx, image in enumerate(images):
            path = image.get("path")
            if not path and image.get("data"):
                suffix = _codex_image_suffix(image.get("media_type"))
                path = RUNDIR / f"codex-image-{stamp}-{idx}{suffix}"
                path.write_bytes(base64.b64decode(image["data"]))
            if path:
                items.append({"type": "localImage", "path": str(Path(path).resolve())})

    text = message.replace("[img]", "").strip() if images else message
    if text or not items:
        items.append({"type": "text", "text": text})
    return items


def _codex_turn_start_params(thread_id, message, images=None):
    return {
        "threadId": thread_id,
        "approvalPolicy": "never",
        "sandboxPolicy": {"type": "dangerFullAccess"},
        "summary": _CODEX_REASONING_SUMMARY,
        "input": _codex_input_items(message, images=images),
    }


class _CodexProcess:
    def __init__(self, cwd):
        self.cwd = cwd
        self.proc = None
        self.events = queue.Queue()
        self.backlog = []
        self.next_id = 1
        self.stderr_log = RUNDIR / "codex-stderr.log"
        self.raw_log = RUNDIR / "codex-app-server.jsonl"
        self.client_log = RUNDIR / "codex-app-client.jsonl"

    def start(self):
        for path in (self.stderr_log, self.raw_log, self.client_log):
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("")
        self.proc = subprocess.Popen(
            ["codex", "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=self.cwd,
        )
        threading.Thread(target=self._stdout_reader, daemon=True).start()
        threading.Thread(target=self._stderr_reader, daemon=True).start()

    def stop(self):
        if self.proc is None or self.proc.poll() is not None:
            return
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()

    def _stdout_reader(self):
        for line in iter(self.proc.stdout.readline, ""):
            raw = line.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                obj = {"_raw": raw}
            _log(self.raw_log, json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
            self.events.put(obj)

    def _stderr_reader(self):
        for line in iter(self.proc.stderr.readline, ""):
            _log(self.stderr_log, line)

    def send(self, method, params=None, request=True):
        msg = {"method": method, "params": params or {}}
        msg_id = None
        if request:
            msg_id = self.next_id
            msg["id"] = msg_id
            self.next_id += 1
        _log(self.client_log, json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.proc.stdin.write(json.dumps(msg, separators=(",", ":")) + "\n")
        self.proc.stdin.flush()
        return msg_id

    def respond(self, msg_id, result):
        msg = {"id": msg_id, "result": result}
        _log(self.client_log, json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.proc.stdin.write(json.dumps(msg, separators=(",", ":")) + "\n")
        self.proc.stdin.flush()

    def wait_for(self, predicate, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                obj = self.events.get(timeout=0.2)
            except queue.Empty:
                continue
            if predicate(obj):
                return obj
            self.backlog.append(obj)
        raise TimeoutError("timeout aguardando resposta do codex app-server")

    def next_event(self, timeout):
        if self.backlog:
            return self.backlog.pop(0)
        return self.events.get(timeout=timeout)

    def alive(self):
        return self.proc is not None and self.proc.poll() is None


class _CodexAdapter:
    def __init__(self, client):
        self.client = client
        self.seq = 0
        self.session_id = None
        self.turn_id = None
        self.provider_version = None
        self.model = None
        self.turn_done = False
        self.inferred_done = False
        self.pending_approvals = {}
        self.resolved_approvals = set()

    def _provider(self):
        return {
            "name": "codex",
            "surface": "app-server",
            "version": self.provider_version,
            "model": self.model,
        }

    def event(self, kind, payload, *, turn_id=None, item_id=None, raw=None):
        self.seq += 1
        return {
            "schema": SCHEMA,
            "kind": kind,
            "ts": _utc_now(),
            "seq": self.seq,
            "provider": self._provider(),
            "session_id": self.session_id,
            "turn_id": turn_id if turn_id is not None else self.turn_id,
            "item_id": item_id,
            "parent_item_id": None,
            "payload": payload,
            "raw": raw,
        }

    def normalize(self, msg):
        out = []
        if "result" in msg and "id" in msg:
            result = msg.get("result") or {}
            if "userAgent" in result:
                self.provider_version = _parse_codex_version(result.get("userAgent"))
            thread = result.get("thread")
            if isinstance(thread, dict):
                self.session_id = thread.get("sessionId") or thread.get("id") or self.session_id
                self.model = result.get("model") or self.model
                self.client.session_id = self.session_id
                self.client.observed_model = self.model or ""
            return out

        method = msg.get("method")
        params = msg.get("params") or {}

        if method == "configWarning":
            summary = params.get("summary") or "Codex config warning"
            code = "codex_linux_sandbox_userns" if summary == _CODEX_LINUX_SANDBOX_WARNING else None
            out.append(self.event("notice", {
                "notice": {"level": "warning", "message": summary, "code": code}
            }, raw=msg))
        elif method == "thread/started":
            thread = params.get("thread") or {}
            self.session_id = thread.get("sessionId") or thread.get("id") or self.session_id
            self.client.session_id = self.session_id
            out.append(self.event("session.started", {"status": "started"}, raw=msg))
        elif method == "turn/started":
            turn = params.get("turn") or {}
            self.turn_id = turn.get("id") or params.get("turnId") or self.turn_id
            out.append(self.event("turn.started", {"status": "in_progress"}, raw=msg))
        elif method == "turn/completed":
            turn = params.get("turn") or {}
            self.turn_id = turn.get("id") or params.get("turnId") or self.turn_id
            status = turn.get("status") or "completed"
            self.turn_done = True
            if status == "interrupted":
                kind = "turn.interrupted"
            elif status in ("failed", "error"):
                kind = "turn.failed"
            else:
                kind = "turn.finished"
            out.append(self.event(kind, {
                "status": status,
                "duration_ms": turn.get("durationMs"),
                "error": turn.get("error"),
            }, raw=msg))
        elif method == "thread/status/changed":
            status = params.get("status") or {}
            flags = status.get("activeFlags") or []
            if "waitingOnApproval" in flags:
                out.append(self.event("notice", {
                    "notice": {"level": "info", "message": "waiting on approval", "code": "waiting_on_approval"}
                }, raw=msg))
        elif method == "item/started":
            out.extend(self._item_started(params.get("item") or {}, params, msg))
        elif method == "item/completed":
            out.extend(self._item_completed(params.get("item") or {}, params, msg))
        elif method == "item/agentMessage/delta":
            out.append(self.event("message.assistant.delta", {
                "role": "assistant",
                "delta": params.get("delta", ""),
            }, turn_id=params.get("turnId"), item_id=params.get("itemId"), raw=msg))
        elif method == "item/reasoning/textDelta":
            out.append(self.event("reasoning.delta", {
                "delta": params.get("delta", ""),
                "visibility": "text",
                "content_index": params.get("contentIndex"),
            }, turn_id=params.get("turnId"), item_id=params.get("itemId"), raw=msg))
        elif method == "item/reasoning/summaryTextDelta":
            out.append(self.event("reasoning.delta", {
                "delta": params.get("delta", ""),
                "visibility": "summary",
                "summary_index": params.get("summaryIndex"),
            }, turn_id=params.get("turnId"), item_id=params.get("itemId"), raw=msg))
        elif method == "item/reasoning/summaryPartAdded":
            out.append(self.event("reasoning.summary.started", {
                "status": "in_progress",
                "summary_index": params.get("summaryIndex"),
                "visibility": "summary",
            }, turn_id=params.get("turnId"), item_id=params.get("itemId"), raw=msg))
        elif method == "item/commandExecution/outputDelta":
            out.append(self.event("command.output.delta", {
                "delta": params.get("delta", ""),
                "command": {"stream": "combined"},
            }, turn_id=params.get("turnId"), item_id=params.get("itemId"), raw=msg))
        elif method == "item/commandExecution/requestApproval":
            request_id = msg.get("id")
            self.pending_approvals[request_id] = params
            out.append(self.event("approval.requested", {
                "approval": {
                    "request_id": request_id,
                    "target_kind": "command",
                    "reason": params.get("reason"),
                    "available_decisions": params.get("availableDecisions"),
                },
                "command": {"command": params.get("command"), "cwd": params.get("cwd")},
            }, turn_id=params.get("turnId"), item_id=params.get("itemId"), raw=msg))
        elif method == "serverRequest/resolved":
            request_id = params.get("requestId")
            request = self.pending_approvals.pop(request_id, {})
            if request_id not in self.resolved_approvals:
                out.append(self.event("approval.resolved", {
                    "approval": {"request_id": request_id, "target_kind": "command"}
                }, turn_id=request.get("turnId"), item_id=request.get("itemId"), raw=msg))
        elif method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage") or {}
            last = usage.get("last") or usage.get("total") or {}
            total = usage.get("total") or {}
            out.append(self.event("usage.updated", {
                "usage": {
                    "scope": "turn",
                    "input_tokens": last.get("inputTokens"),
                    "output_tokens": last.get("outputTokens"),
                    "cached_input_tokens": last.get("cachedInputTokens"),
                    "reasoning_output_tokens": last.get("reasoningOutputTokens"),
                    "total_tokens": last.get("totalTokens"),
                    "session_input_tokens": total.get("inputTokens"),
                    "session_output_tokens": total.get("outputTokens"),
                    "context_window": usage.get("modelContextWindow"),
                }
            }, turn_id=params.get("turnId"), raw=msg))
        elif method == "account/rateLimits/updated":
            limits = params.get("rateLimits") or {}
            primary = limits.get("primary") or {}
            out.append(self.event("quota.updated", {
                "quota": {
                    "scope": "account",
                    "status": limits.get("rateLimitReachedType"),
                    "used_percent": primary.get("usedPercent"),
                    "resets_at": primary.get("resetsAt"),
                }
            }, raw=msg))
        elif method == "turn/diff/updated":
            out.append(self.event("diff.updated", {
                "diff": {"format": "unified", "snapshot": True, "diff": params.get("diff")}
            }, turn_id=params.get("turnId"), raw=msg))
        elif method == "error":
            error = params.get("error") or params
            out.append(self.event("error", {
                "error": {
                    "message": error.get("message") if isinstance(error, dict) else str(error),
                    "code": error.get("code") if isinstance(error, dict) else None,
                    "source": "provider",
                }
            }, raw=msg))
        return out

    def _item_started(self, item, params, raw):
        typ = item.get("type")
        if typ == "userMessage":
            text = "".join(part.get("text", "") for part in item.get("content", []) if part.get("type") == "text")
            return [self.event("message.user", {"role": "user", "text": text},
                               turn_id=params.get("turnId"), item_id=item.get("id"), raw=raw)]
        if typ == "commandExecution":
            return [self.event("command.started", {
                "status": item.get("status"),
                "command": {
                    "command": item.get("command"),
                    "cwd": item.get("cwd"),
                    "source": item.get("source"),
                }
            }, turn_id=params.get("turnId"), item_id=item.get("id"), raw=raw)]
        if typ == "reasoning":
            summary_parts = _text_part_texts(item.get("summary"))
            content_parts = _text_part_texts(item.get("content"))
            return [self.event("reasoning.started", {
                "status": item.get("status") or "in_progress",
                "visibility": "summary" if summary_parts else ("text" if content_parts else "unknown"),
                "summary_text": "\n".join(summary_parts),
                "content_text": "\n".join(content_parts),
                "summary_parts": summary_parts,
                "content_parts": content_parts,
            }, turn_id=params.get("turnId"), item_id=item.get("id"), raw=raw)]
        if typ == "fileChange":
            changes = item.get("changes") or []
            first = changes[0] if changes else {}
            return [self.event("file.change.started", {
                "status": item.get("status"),
                "file": {
                    "path": first.get("path"),
                    "operation": (first.get("kind") or {}).get("type", "unknown"),
                }
            }, turn_id=params.get("turnId"), item_id=item.get("id"), raw=raw)]
        return []

    def _item_completed(self, item, params, raw):
        typ = item.get("type")
        if typ == "agentMessage":
            return [self.event("message.assistant.completed", {
                "role": "assistant",
                "text": item.get("text", ""),
                "phase": item.get("phase"),
            }, turn_id=params.get("turnId"), item_id=item.get("id"), raw=raw)]
        if typ == "reasoning":
            summary_parts = _text_part_texts(item.get("summary"))
            content_parts = _text_part_texts(item.get("content"))
            summary_text = "\n".join(summary_parts)
            content_text = "\n".join(content_parts)
            visibility = "summary" if summary_parts else ("text" if content_parts else "unknown")
            return [self.event("reasoning.completed", {
                "status": "completed",
                "visibility": visibility,
                "summary_text": summary_text,
                "content_text": content_text,
                "summary_parts": summary_parts,
                "content_parts": content_parts,
            }, turn_id=params.get("turnId"), item_id=item.get("id"), raw=raw)]
        if typ == "commandExecution":
            status = item.get("status")
            kind = "command.finished" if status == "completed" else "command.failed"
            return [self.event(kind, {
                "status": status,
                "command": {
                    "command": item.get("command"),
                    "cwd": item.get("cwd"),
                    "source": item.get("source"),
                    "output": item.get("aggregatedOutput"),
                    "exit_code": item.get("exitCode"),
                    "duration_ms": item.get("durationMs"),
                }
            }, turn_id=params.get("turnId"), item_id=item.get("id"), raw=raw)]
        if typ == "fileChange":
            events = []
            for change in item.get("changes") or []:
                events.append(self.event("file.change.finished", {
                    "status": item.get("status"),
                    "file": {
                        "path": change.get("path"),
                        "operation": (change.get("kind") or {}).get("type", "unknown"),
                        "diff": change.get("diff"),
                    }
                }, turn_id=params.get("turnId"), item_id=item.get("id"), raw=raw))
            return events
        return []


class _CodexRenderer:
    def __init__(self, client, cfg, render, start_time):
        self.client = client
        self.cfg = cfg
        self.render = render
        self.start_time = start_time
        self.spinner_shown = False
        self.fire_frame = 0
        self.text_started = False
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.context_window = 258400
        self.reasoning_output_tokens = 0
        self.model_name = ""
        self.quota = None
        self.turn_done = False
        self.turn_status = None
        self.turn_duration_ms = None
        self.thinking_autoresize = cfg.get("thinking_autoresize", True)
        rows = _window_height(client.tmux_srv)
        self._idle_lines = max(8, int(rows * 0.16))
        self._max_think_lines = max(12, rows - int(rows * 0.26) - max(2, int(rows * 0.08)) - 6)
        self.reasoning_header_written = False
        self.reasoning_open = False
        self.reasoning_open_items = set()
        self.reasoning_open_entry_items = set()
        self.reasoning_finished_items = set()
        self.reasoning_items_with_text = set()
        self.assistant_item_id = None
        self.assistant_text_started = False
        self.assistant_last_char = ""

    def show_status(self):
        if self.client._streaming_text:
            return
        elapsed = time.time() - self.start_time
        tok = self.input_tokens + self.output_tokens
        if not self.spinner_shown:
            sys.stdout.write("\n")
            self.spinner_shown = True
        tok_part = f"· {tok/1000:.1f}k tok " if tok >= 1000 else (f"· {tok} tok " if tok else "")
        sys.stdout.write(f"\r\033[2K{DIM}codex pensando…{RESET}  {DIM}{elapsed:.0f}s {tok_part}{RESET}")
        sys.stdout.flush()

    def clear_status(self):
        if self.spinner_shown:
            sys.stdout.write("\r\033[2K\033[1A\r\033[2K")
            self.spinner_shown = False
            sys.stdout.flush()

    def _reasoning_entry_is_open(self, item_id=None):
        if item_id:
            return item_id in self.reasoning_open_entry_items
        return "__anon__" in self.reasoning_open_entry_items

    def _open_thinking(self, item_id=None, force_new=False):
        entry_key = item_id or "__anon__"
        if not force_new and self._reasoning_entry_is_open(item_id):
            return
        if self._reasoning_entry_is_open(item_id):
            self._close_thinking(item_id)
        prefix = CLEAR if not self.reasoning_header_written else ""
        if self.render is not None:
            self.render.join()
        _log(THINKING_LOG, f"{prefix}{THINKING_TS}[{_ts()}]{RESET}\n\033[40m{THINKING_FG}")
        if self.thinking_autoresize:
            _resize_thinking(self.client.tmux_srv, self._max_think_lines)
        self.reasoning_header_written = True
        self.reasoning_open_entry_items.add(entry_key)

    def _close_thinking(self, item_id=None):
        entry_key = item_id or "__anon__"
        if entry_key not in self.reasoning_open_entry_items:
            return
        self.render.join()
        _log(THINKING_LOG, f"{RESET}\n")
        self.reasoning_open_entry_items.discard(entry_key)

    def _start_reasoning(self, payload, item_id=None):
        self.reasoning_open = True
        if item_id:
            self.reasoning_open_items.add(item_id)
        parts = payload.get("summary_parts") or payload.get("content_parts") or []
        if parts:
            self._push_reasoning_parts(parts, item_id, close_entries=True)
            return
        text = payload.get("summary_text") or payload.get("content_text") or ""
        if text:
            self._push_reasoning_text(text, item_id, close_entry=True)

    def _push_reasoning_parts(self, parts, item_id=None, close_entries=False):
        for text in parts:
            self._push_reasoning_text(text, item_id, force_new=True, close_entry=close_entries)

    def _push_reasoning_text(self, text, item_id=None, force_new=False, close_entry=False):
        if not text:
            return
        self._open_thinking(item_id, force_new=force_new)
        if item_id:
            self.reasoning_items_with_text.add(item_id)
        self.render.push_file(
            THINKING_LOG,
            text,
            delay=0.001 if self.cfg.get("typewriter", True) else 0,
            hesitate=False,
        )
        if close_entry:
            self._close_thinking(item_id)

    def _finish_reasoning(self, payload, item_id=None):
        if item_id and item_id in self.reasoning_finished_items:
            return
        parts = payload.get("summary_parts") or payload.get("content_parts") or []
        text = payload.get("summary_text") or payload.get("content_text") or ""
        item_has_text = bool(item_id and item_id in self.reasoning_items_with_text)
        if item_id is None:
            item_has_text = bool(self.reasoning_items_with_text)
        if parts and not item_has_text:
            self._push_reasoning_parts(parts, item_id, close_entries=True)
            item_has_text = True
        elif text and not item_has_text:
            self._push_reasoning_text(text, item_id, close_entry=True)
            item_has_text = True
        elif not item_has_text and not self._reasoning_entry_is_open(item_id):
            self._open_thinking(item_id)
        if self._reasoning_entry_is_open(item_id):
            if not item_has_text:
                _log(
                    THINKING_LOG,
                    f"{DIM}raciocínio registrado pelo Codex, mas sem resumo textual exposto.{RESET}\n",
                )
            self._close_thinking(item_id)
        if self.reasoning_header_written and self.thinking_autoresize:
            _resize_thinking(self.client.tmux_srv, "summary")
        if item_id:
            self.reasoning_finished_items.add(item_id)
            self.reasoning_open_items.discard(item_id)
            self.reasoning_open = bool(self.reasoning_open_items)
        else:
            self.reasoning_open_items.clear()
            self.reasoning_open = False

    def _push_assistant_delta(self, text, item_id=None):
        if not text:
            return
        if (
            self.assistant_text_started
            and item_id
            and self.assistant_item_id
            and item_id != self.assistant_item_id
            and self.assistant_last_char != "\n"
        ):
            self.client._last_response_text += "\n\n"
            self.render.push_stdout(CHAT_FG + "\n\n" + RESET, delay=0)
        self.assistant_item_id = item_id or self.assistant_item_id
        self.assistant_text_started = True
        self.assistant_last_char = text[-1]
        self.client._last_response_text += text
        self.render.push_stdout(
            CHAT_FG + text + RESET,
            delay=0.015 if self.cfg.get("typewriter", True) else 0,
        )

    def handle(self, event):
        kind = event["kind"]
        payload = event.get("payload") or {}
        provider = event.get("provider") or {}
        if provider.get("model"):
            self.model_name = provider["model"]
            self.client.observed_model = self.model_name

        if kind == "message.assistant.delta":
            if self.reasoning_open:
                self._finish_reasoning({})
            self.client._streaming_text = True
            self.text_started = True
            self._push_assistant_delta(payload.get("delta", ""), event.get("item_id"))
        elif kind == "command.started":
            command = (payload.get("command") or {}).get("command") or "command"
            log_tool_call({"name": "Bash", "input": {"command": command, "description": command}},
                          self.client.nvim_pane, self.client.tmux_srv, self.client.editor_bin, self.render)
        elif kind == "command.output.delta":
            log_tool_result({"content": payload.get("delta", "")})
        elif kind in ("command.finished", "command.failed"):
            command = payload.get("command") or {}
            output = command.get("output")
            if output:
                log_tool_result({"content": output})
            if kind == "command.failed" and not output:
                _log(TOOLS_LOG, f"  {YELLOW}{payload.get('status', 'failed')}{RESET}\n\n")
        elif kind == "file.change.finished":
            file_payload = payload.get("file") or {}
            path = file_payload.get("path") or ""
            diff = file_payload.get("diff") or ""
            log_tool_call({"name": "Edit", "input": {"file_path": path, "old_string": ""}},
                          self.client.nvim_pane, self.client.tmux_srv, self.client.editor_bin, self.render)
            if diff:
                log_tool_result({"content": diff})
        elif kind == "reasoning.started":
            self._start_reasoning(payload, event.get("item_id"))
        elif kind == "reasoning.summary.started":
            item_id = event.get("item_id")
            if item_id:
                self.reasoning_open = True
                self.reasoning_open_items.add(item_id)
            self._open_thinking(item_id, force_new=True)
        elif kind == "reasoning.delta":
            self._push_reasoning_text(payload.get("delta", ""), event.get("item_id"))
        elif kind == "reasoning.completed":
            self._finish_reasoning(payload, event.get("item_id"))
        elif kind == "usage.updated":
            usage = payload.get("usage") or {}
            self.input_tokens = usage.get("input_tokens") or self.input_tokens
            self.output_tokens = usage.get("output_tokens") or self.output_tokens
            self.cache_read_tokens = usage.get("cached_input_tokens") or self.cache_read_tokens
            self.reasoning_output_tokens = usage.get("reasoning_output_tokens") or self.reasoning_output_tokens
            self.context_window = usage.get("context_window") or self.context_window
        elif kind == "quota.updated":
            self.quota = payload.get("quota") or self.quota
        elif kind == "notice":
            notice = payload.get("notice") or {}
            message = notice.get("message")
            hidden_codes = {"waiting_on_approval", "codex_linux_sandbox_userns"}
            if message and notice.get("code") not in hidden_codes:
                _log(TOOLS_LOG, f"{DIM}{_ts()}{RESET}  {YELLOW}!{RESET}  {message}\n")
        elif kind == "approval.requested":
            self.render.suspend()
            try:
                return self._ask_approval(payload)
            finally:
                self.render.resume()
        elif kind in ("turn.finished", "turn.failed", "turn.interrupted"):
            self.turn_done = True
            self.turn_status = payload.get("status")
            self.turn_duration_ms = payload.get("duration_ms")
        return None

    def _ask_approval(self, payload):
        approval = payload.get("approval") or {}
        command = payload.get("command") or {}
        sys.stdout.write(f"\n{YELLOW}permissão codex{RESET}  {approval.get('reason') or ''}\n")
        if command.get("command"):
            sys.stdout.write(f"{DIM}{command['command']}{RESET}\n")
        sys.stdout.write(f"{DIM}[y] permitir  [n] negar  [c] cancelar{RESET}  ")
        sys.stdout.flush()
        with _raw_stdin():
            ch = os.read(sys.stdin.fileno(), 1).decode("utf-8", errors="replace").lower()
        if ch == "y":
            sys.stdout.write(f"y  {DIM}(permitido){RESET}\n\n")
            sys.stdout.flush()
            return "accept"
        if ch == "n":
            sys.stdout.write(f"n  {DIM}(negado){RESET}\n\n")
            sys.stdout.flush()
            return "decline"
        sys.stdout.write(f"c  {DIM}(cancelado){RESET}\n\n")
        sys.stdout.flush()
        return "cancel"


def _write_stats(client, renderer, elapsed):
    stats_tty_file = RUNDIR / "stats_tty"
    stats_tty = stats_tty_file.read_text().strip() if stats_tty_file.exists() else ""
    if not stats_tty:
        return
    input_tok = renderer.input_tokens
    output_tok = renderer.output_tokens
    cache_read = renderer.cache_read_tokens
    client._total_input_tokens = getattr(client, "_total_input_tokens", 0) + input_tok
    client._total_output_tokens = getattr(client, "_total_output_tokens", 0) + output_tok
    client._total_elapsed = getattr(client, "_total_elapsed", 0.0) + elapsed
    cost_turn = 0.0
    client._total_cost = getattr(client, "_total_cost", 0.0) + cost_turn

    turn_line = _render_turn_line(_ts(), input_tok, output_tok, elapsed, cost_turn, cache_read)
    total_line = _render_total_line(
        client._total_input_tokens, client._total_output_tokens,
        client._total_elapsed, client._total_cost,
    )
    ctx_max = renderer.context_window or _model_ctx_window(renderer.model_name)
    ctx_line = _render_ctx_line(input_tok + cache_read, ctx_max)
    quota_line = _render_quota_line(_codex_quota_for_stats(renderer.quota))
    try:
        fd = os.open(stats_tty, os.O_WRONLY | os.O_NOCTTY)
        os.write(fd, ("\033[H" + turn_line + "\n" + total_line + "\n" + ctx_line + "\n" + quota_line).encode())
        os.close(fd)
    except OSError:
        pass


def _codex_quota_for_stats(quota):
    if not quota:
        return None
    pct = quota.get("used_percent")
    reset = quota.get("resets_at")
    return {
        "limits": [{
            "label": "codex",
            "pct": pct,
            "severity": None,
            "reset": str(reset) if reset else "",
        }]
    }


def run_codex_turn(client, message, images=None):
    reset_col()
    client._last_response_text = ""

    cfg = config.load()
    proc = getattr(client, "_codex_proc", None)
    adapter = getattr(client, "_codex_adapter", None)
    boot_needed = proc is None or not proc.alive() or getattr(proc, "cwd", None) != client.cwd or adapter is None
    if boot_needed:
        if proc is not None:
            proc.stop()
        proc = _CodexProcess(client.cwd)
        adapter = _CodexAdapter(client)
        client._codex_proc = proc
        client._codex_adapter = adapter
        client._codex_thread_id = None
        try:
            proc.start()
        except FileNotFoundError:
            sys.stdout.write(
                f"\n{YELLOW}codex CLI não encontrado.{RESET}"
                f" {DIM}Instale/configure o Codex CLI para usar --backend codex.{RESET}\n"
            )
            sys.stdout.flush()
            return False
    client.proc = proc.proc

    adapter.turn_done = False
    adapter.inferred_done = False
    adapter.turn_id = None
    adapter.pending_approvals.clear()
    adapter.resolved_approvals.clear()

    renderer = _CodexRenderer(client, cfg, None, time.time())
    render = None

    _fd = sys.stdin.fileno()
    _old_term = termios.tcgetattr(_fd)
    _no_echo = list(_old_term)
    _no_echo[3] &= ~(termios.ECHO | termios.ICANON)
    _no_echo[6] = list(_old_term[6])
    _no_echo[6][termios.VMIN] = 1
    _no_echo[6][termios.VTIME] = 0
    termios.tcsetattr(_fd, termios.TCSADRAIN, _no_echo)

    try:
        render = RenderQueue()
        renderer.render = render
        render.start(
            status_cb=renderer.show_status,
            clear_status_cb=renderer.clear_status,
            is_streaming_cb=lambda: client._streaming_text,
        )

        if boot_needed:
            init_id = proc.send("initialize", {
                "clientInfo": {"name": "frollo", "title": "Frollo", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True, "requestAttestation": False},
            })
            init_result = proc.wait_for(lambda obj: obj.get("id") == init_id, 10)
            for event in adapter.normalize(init_result):
                renderer.handle(event)

            proc.send("initialized", request=False)
            # First POC defaults to permissive execution in both Frollo modes.
            # Approval support is present below, but workspace sandbox is still too
            # noisy in the current local bubblewrap environment to make it default UX.
            thread_id_req = proc.send("thread/start", {
                "cwd": client.cwd,
                "approvalPolicy": "never",
                "sandbox": "danger-full-access",
            })
            thread_result = proc.wait_for(lambda obj: obj.get("id") == thread_id_req, 10)
            for event in adapter.normalize(thread_result):
                renderer.handle(event)
            client._codex_thread_id = thread_result["result"]["thread"]["id"]

        thread_id = client._codex_thread_id
        proc.send("turn/start", _codex_turn_start_params(thread_id, message, images=images))

        saw_idle = False
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                msg = proc.next_event(0.15)
            except queue.Empty:
                continue
            for event in adapter.normalize(msg):
                decision = renderer.handle(event)
                if decision is not None and event["kind"] == "approval.requested":
                    request_id = event["payload"]["approval"]["request_id"]
                    proc.respond(request_id, {"decision": decision})
                    adapter.resolved_approvals.add(request_id)
                    resolved = adapter.event("approval.resolved", {
                        "approval": {
                            "request_id": request_id,
                            "target_kind": "command",
                            "decision": decision,
                        }
                    }, turn_id=event["turn_id"], item_id=event["item_id"],
                       raw={"client_response": {"id": request_id, "result": {"decision": decision}}})
                    renderer.handle(resolved)
            if msg.get("method") == "thread/status/changed":
                saw_idle = (msg.get("params") or {}).get("status", {}).get("type") == "idle"
            if adapter.turn_done:
                break

        if saw_idle and not adapter.turn_done:
            renderer.handle(adapter.event("turn.finished", {
                "status": "completed",
                "inferred": True,
                "reason": "thread returned to idle without turn/completed",
            }))

        render.stop()
        client._streaming_text = False
        if renderer.text_started and renderer.spinner_shown:
            sys.stdout.write("\n")
            sys.stdout.flush()
        renderer.clear_status()
        elapsed = time.time() - renderer.start_time
        _write_stats(client, renderer, elapsed)
        client.first_turn = False
        return False
    finally:
        if render is not None:
            render.cancel()
        client._streaming_text = False
        try:
            termios.tcsetattr(_fd, termios.TCSADRAIN, _old_term)
        except Exception:
            pass
