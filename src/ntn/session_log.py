"""Session logging for resume/debug.

Keeps the log format identical to the previous inline implementation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class SessionLogger:
    debug_file: str

    def _write(self, message: str) -> None:
        Path(self.debug_file).parent.mkdir(parents=True, exist_ok=True)
        with open(self.debug_file, "a", encoding="utf-8") as f:
            f.write(message + "\n")

    def session_start(self, workspace_dir: str, container_name: str) -> None:
        self._write("=== SESSION START ===")
        self._write(f"Timestamp: {datetime.now().isoformat()}")
        self._write(f"Workspace: {workspace_dir}")
        self._write(f"Container: {container_name}")

    def resume(self) -> None:
        self._write("\n=== RESUME ===")
        self._write(f"Timestamp: {datetime.now().isoformat()}")

    def turn_start(self, turn_num: int, user_input: Any) -> None:
        self._write(f"\n=== TURN {turn_num} ===")
        self._write("--- USER ---")
        if isinstance(user_input, str):
            self._write(user_input)
        else:
            self._write(json.dumps(user_input, indent=2, ensure_ascii=False))

    def assistant(self, content_list: Any) -> None:
        self._write("\n--- ASSISTANT ---")
        self._write(json.dumps(content_list, indent=2, ensure_ascii=False))

    def req_usage(self, model_short: str, last_usage: dict[str, Any] | None) -> None:
        if last_usage:
            usage_with_model = {"model": model_short, **last_usage}
            self._write(f"--- USAGE: {json.dumps(usage_with_model)} ---")

    def tool_results(self, tool_results: Any) -> None:
        self._write("\n--- TOOL_RESULT ---")
        self._write(json.dumps(tool_results, indent=2, ensure_ascii=False))

    def end_turn(self) -> None:
        self._write("\n--- END_TURN ---")

    def drop_turn_marker(self) -> None:
        self._write("--- DROP_TURN ---")

    def compaction(self, reason: str, removed_turns: str, summary_content: str) -> None:
        self._write("\n=== COMPACTION EVENT ===")
        self._write(f"Reason: {reason}")
        self._write(f"Removed turns: {removed_turns}")
        self._write(f"Summary content:\n{summary_content}")

    def container_info(self, info: dict[str, Any]) -> None:
        self._write("\n=== CONTAINER INFO ===")
        self._write(json.dumps(info))

    def raw(self, message: str) -> None:
        self._write(message)
