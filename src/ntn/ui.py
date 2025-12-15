"""UI helpers (printing, colors, dividers).

Keeps formatting centralized so agent logic stays lean.
"""

from __future__ import annotations

from dataclasses import dataclass

from colorama import Fore, Style


@dataclass(frozen=True)
class UiConfig:
    divider_width: int


def divider_line(width: int) -> str:
    return '─' * width


def print_divider(width: int) -> None:
    print(f"{Style.DIM}{divider_line(width)}{Style.RESET_ALL}")


def format_k(count: int) -> str:
    if count >= 1000:
        return f"{count/1000:.0f}K"
    return str(count)


def print_status_box(
    *,
    divider_width: int,
    run_cost: float,
    session_cost: float,
    rate_limit_info: dict | None,
    context_tokens: int,
    effective_limit: int,
) -> None:
    inner_width = divider_width - 2

    cost_line = f"💰 Cost: ${run_cost:.4f} (total: ${session_cost:.4f})"

    if rate_limit_info and rate_limit_info.get("request_limit") and rate_limit_info.get("request_remaining") is not None:
        rl = rate_limit_info
        req_used = rl["request_limit"] - rl["request_remaining"]
        req_pct = (req_used / rl["request_limit"]) * 100
        req_str = f"{req_used}/{rl['request_limit']} ({req_pct:.0f}%)"
    else:
        req_str = "N/A"

    if rate_limit_info and rate_limit_info.get("input_limit") and rate_limit_info.get("input_remaining") is not None:
        rl = rate_limit_info
        input_used = rl["input_limit"] - rl["input_remaining"]
        input_pct = (input_used / rl["input_limit"]) * 100
        input_str = f"{format_k(input_used)}/{format_k(rl['input_limit'])} ({input_pct:.0f}%)"
    else:
        input_str = "N/A"

    if rate_limit_info and rate_limit_info.get("output_limit") and rate_limit_info.get("output_remaining") is not None:
        rl = rate_limit_info
        output_used = rl["output_limit"] - rl["output_remaining"]
        output_pct = (output_used / rl["output_limit"]) * 100
        output_str = f"{format_k(output_used)}/{format_k(rl['output_limit'])} ({output_pct:.0f}%)"
    else:
        output_str = "N/A"

    rate_line = f"📊 Requests: {req_str} | Input: {input_str} | Output: {output_str}"

    context_pct = (context_tokens / effective_limit) * 100 if effective_limit else 0.0
    context_line = f"🧠 Context: {context_tokens:,}/{effective_limit:,} ({context_pct:.0f}%)"

    print()
    print_divider(divider_width)
    print(f"{Style.DIM}╭{'─' * inner_width}╮{Style.RESET_ALL}")
    print(f"{Style.DIM}│{Style.RESET_ALL}{Fore.CYAN} {cost_line.ljust(inner_width - 1)}{Style.DIM}│{Style.RESET_ALL}")
    print(f"{Style.DIM}│{Style.RESET_ALL}{Fore.CYAN} {rate_line.ljust(inner_width - 1)}{Style.DIM}│{Style.RESET_ALL}")
    print(f"{Style.DIM}│{Style.RESET_ALL}{Fore.CYAN} {context_line.ljust(inner_width - 1)}{Style.DIM}│{Style.RESET_ALL}")
    print(f"{Style.DIM}╰{'─' * inner_width}╯{Style.RESET_ALL}")
    print()
