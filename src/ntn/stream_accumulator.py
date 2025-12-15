"""Streaming event accumulator.

Turns unified provider stream events into a normalized content_list matching the
non-streaming response format.

If show_thinking_content is enabled, thinking deltas are also printed as they
stream (same color as thinking).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from colorama import Style


@dataclass
class StreamAccumulator:
    print_text: bool
    assistant_prefix: str
    get_assistant_color: Any
    show_think_content: bool = False

    content_list: list[dict[str, Any]] = field(default_factory=list)
    _current_text: str = ""
    _current_thinking: str = ""
    _current_signature: str = ""
    _tool_use: dict[str, Any] | None = None
    _tool_input_json: str = ""
    _thinking_shown: bool = False
    _text_started: bool = False

    def on_event(self, event: Any) -> None:
        et = event.type

        if et == "thinking_start":
            self._current_thinking = ""
            self._current_signature = ""
            if self.print_text and not self._thinking_shown:
                print(f"{self.get_assistant_color('thinking')}Thinking...{Style.RESET_ALL}")
                self._thinking_shown = True
            return

        if et == "text_start":
            if self.print_text:
                # Add blank line after thinking content if thinking was shown
                if self._thinking_shown:
                    print("\n")
                print(
                    f"{self.get_assistant_color('assistant')}{self.assistant_prefix} ",
                    end="",
                    flush=True,
                )
            self._text_started = True
            return

        if et == "tool_use_start":
            self._tool_use = {"id": event.data["id"], "name": event.data["name"], "input": {}}
            self._tool_input_json = ""
            return

        if et == "thinking_delta":
            self._current_thinking += event.data
            if self.print_text and self.show_think_content:
                print(f"{self.get_assistant_color('thinking')}{event.data}{Style.RESET_ALL}", end="", flush=True)
            return

        if et == "signature_delta":
            self._current_signature += event.data
            return

        if et == "text_delta":
            self._current_text += event.data
            if self.print_text:
                print(f"{self.get_assistant_color('assistant')}{event.data}", end="", flush=True)
            return

        if et == "tool_input_delta":
            self._tool_input_json += event.data
            return

        if et == "content_block_stop":
            self._flush_block()
            return

    def _flush_block(self) -> None:
        if self._current_thinking or self._current_signature:
            thinking_block: dict[str, Any] = {"type": "thinking", "thinking": self._current_thinking}
            if self._current_signature:
                thinking_block["signature"] = self._current_signature
            self.content_list.append(thinking_block)
            self._current_thinking = ""
            self._current_signature = ""

        if self._current_text:
            self.content_list.append({"type": "text", "text": self._current_text})
            if self.print_text and self._text_started:
                print()  # newline
            self._current_text = ""
            self._text_started = False

        if self._tool_use:
            try:
                self._tool_use["input"] = json.loads(self._tool_input_json) if self._tool_input_json else {}
            except json.JSONDecodeError:
                self._tool_use["input"] = {}

            self.content_list.append(
                {
                    "type": "tool_use",
                    "id": self._tool_use["id"],
                    "name": self._tool_use["name"],
                    "input": self._tool_use["input"],
                }
            )
            self._tool_use = None
            self._tool_input_json = ""
