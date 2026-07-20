"""Shared assistant text rendering for provider adapters.

This keeps streaming chat behavior after provider-specific event normalization:
raw snapshot text, markdown buffering, item separators and final line cleanup.
"""

from ..theme import CHAT_FG, RESET, MdBuffer
from .text import col_is_mid_line


class AssistantTextRenderer:
    def __init__(self, client, cfg, render):
        self.client = client
        self.cfg = cfg
        self.render = render
        self.md_buf = MdBuffer()
        self.block_count = 0
        self.item_id = None
        self.text_started = False
        self.last_char = ""

    def start_block(self):
        """Start a visible assistant text block."""
        self.client._streaming_text = True
        self.text_started = True
        prefix = "\n" if self.block_count > 0 else ""
        self.block_count += 1
        if prefix:
            self.render.push_stdout(CHAT_FG + prefix + RESET, delay=0)

    def push_delta(self, text, item_id=None, suppress=False):
        if not text:
            return
        if self._starts_new_item(item_id):
            self._flush_markdown()
            if self.last_char != "\n":
                self._push_raw_separator("\n\n")

        self.item_id = item_id or self.item_id
        self.client._streaming_text = True
        self.text_started = True
        self.last_char = text[-1]
        self.client._last_response_text += text

        if suppress:
            return

        rendered = self.md_buf.feed(text)
        if rendered:
            self.render.push_stdout(CHAT_FG + rendered + RESET, delay=self._delay())

    def finish(self, add_newline_if_mid_line=False):
        if self.text_started:
            remainder = self._flush_markdown()
            if not remainder:
                self.render.push_stdout(RESET, delay=0)
            self.render.join()
            self.client._streaming_text = False
            if add_newline_if_mid_line and col_is_mid_line():
                self.render.push_stdout("\n", delay=0)
                self.render.join()

    def _starts_new_item(self, item_id):
        return bool(
            self.text_started
            and item_id
            and self.item_id
            and item_id != self.item_id
        )

    def _push_raw_separator(self, text):
        self.client._last_response_text += text
        self.last_char = text[-1]
        self.render.push_stdout(CHAT_FG + text + RESET, delay=0)

    def _flush_markdown(self):
        remainder = self.md_buf.flush()
        if remainder:
            self.render.push_stdout(CHAT_FG + remainder + RESET, delay=self._delay())
        return remainder

    def _delay(self):
        return 0.015 if self.cfg.get("typewriter", True) else 0
