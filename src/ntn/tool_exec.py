"""Shared tool execution helpers.

This module exists to avoid duplicating tool execution logic between the main
agent loop and resume/recovery paths.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping, MutableMapping, Sequence


ToolUse = Mapping[str, Any]
ToolResult = MutableMapping[str, Any]


def execute_tool_uses(
    *,
    tool_uses: Sequence[ToolUse],
    tool_map: Mapping[str, Any],
    describe: Callable[[str, Mapping[str, Any]], tuple[str, str | None]],
    print_line: Callable[[str, str | None], None],
    prefix: str = "",
) -> list[ToolResult]:
    """Execute a list of normalized tool_use blocks.

    Args:
        tool_uses: Iterable of blocks shaped like {type:'tool_use', id, name, input}.
        tool_map: Mapping tool_name -> tool instance with .execute(**kwargs).
        describe: Function producing (description, path) tuple.
        print_line: Output function for UI, accepts (line, path).
        prefix: Optional prefix shown in UI.

    Returns:
        List of tool_result blocks ready to be placed into a user message.
    """
    results: list[ToolResult] = []

    for block in tool_uses:
        if block.get("type") != "tool_use":
            continue

        tool_name = str(block.get("name"))
        tool_input = block.get("input") or {}

        description, path = describe(tool_name, tool_input)
        print_line(f"{prefix}{description}", path)

        tool = tool_map[tool_name]
        result = tool.execute(**tool_input)

        if result is None:
            result = {"error": "Tool returned None"}

        results.append(
            {
                "type": "tool_result",
                "tool_use_id": block.get("id"),
                "content": json.dumps(result),
            }
        )

    return results
