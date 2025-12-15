"""Core agent implementation.

This file hosts the CodingAgent class extracted from agent.py.
agent.py can remain as a thin compatibility layer/import.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime

from colorama import Fore, Style, init

from .config import config, get_color
from .docker_manager import ContainerManager
from . import providers
from .prompts import get_system_prompt, get_mount_section_text, get_no_mount_section_text
from .session_log import SessionLogger
from .tools import get_tool_description
from .ui import print_divider, print_status_box

# Initialize colorama
init(autoreset=True)


class CodingAgent:
    """Minimal AI agent for coding workspace."""

    MODELS = config.models.aliases

    SUMMARIZATION_TEMPLATE = """Summarize the conversation above.

Preserve:
- Key decisions and rationale
- Important file contents and tool outputs
- Important code snippets and file paths
- Some agent mistakes to avoid

You may use formatting (headers, code blocks, lists) to organize the summary."""

    def __init__(
        self,
        tools,
        workspace_dir,
        debug_file=None,
        container_info=None,
        stream=False,
        think=True,
        model="gpt",
    ):
        self.model_short = model
        self.model = self.MODELS.get(model, self.MODELS["gpt"])

        self.provider_name = config.models.get_provider(self.model)
        limits = config.models.get_limits(self.model)
        self.max_context_tokens = limits.max_context_tokens if limits else 200000
        self.max_output_tokens = limits.max_output_tokens if limits else 64000

        self.provider = providers.create_provider(self.model, self.provider_name)

        self.workspace_dir = workspace_dir
        self.messages = []
        self.tools = tools
        self.tool_map = {tool.get_schema()["name"]: tool for tool in tools}
        self.display_history = []
        self.stream = stream
        self.think = think
        self.thinking_budget = config.models.get_thinking_budget(self.model)

        self.rate_limit_info = None
        self.context_tokens = 0

        self.session_cost = 0.0
        self.run_cost = 0.0
        self.last_req_cost = 0.0
        self.last_usage = None

        self._dropped_turns_this_turn = False

        if debug_file:
            self.debug_file = debug_file
            self.logger = SessionLogger(self.debug_file)

            if container_info and container_info.get("container_name"):
                container_name = container_info["container_name"]
                working_dirs = container_info.get("working_dirs", [])
            else:
                debug_basename = os.path.basename(debug_file)
                session_name = debug_basename.replace("debug_", "").replace(".txt", "")
                container_name = f"agent_{session_name}"
                working_dirs = []

            if workspace_dir not in working_dirs:
                working_dirs.append(workspace_dir)

            self.container_manager = ContainerManager(container_name=container_name, working_dirs=working_dirs)
            self._last_logged_container_info = container_info
            self.logger.resume()
        else:
            debug_dir = os.path.join(workspace_dir, "debug")
            os.makedirs(debug_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.debug_file = os.path.join(debug_dir, f"debug_{timestamp}.txt")
            self.logger = SessionLogger(self.debug_file)

            self.container_manager = ContainerManager(
                container_name=f"agent_{timestamp}",
                working_dirs=[workspace_dir],
            )
            self._last_logged_container_info = None
            self.logger.session_start(self.workspace_dir, self.container_manager.container_name)

        self._ensure_container_started()
        self._update_system_message()


    def _is_openai_model(self) -> bool:
        """True if current model uses the OpenAI provider."""
        return self.provider_name == "openai"

    # ---- container + prompt ----

    def _ensure_container_started(self):
        if self.container_manager.working_dirs:
            print(f"{Fore.YELLOW}🐳 Starting Docker container...{Style.RESET_ALL}")
            result = self.container_manager.start()
            if "error" in result:
                print(f"{Fore.RED}⚠️  Docker error: {result['error']}{Style.RESET_ALL}")
                print(f"{Fore.YELLOW}   File operations will use Windows commands as fallback.{Style.RESET_ALL}")
            elif result.get("status") == "already_running":
                print(f"{Fore.GREEN}🐳 Container already running: {self.container_manager.container_name}{Style.RESET_ALL}")
            else:
                print(f"{Fore.GREEN}🐳 Container ready: {self.container_manager.container_name}{Style.RESET_ALL}")
                self._log_container_info()

    def _update_system_message(self):
        mount_info = self.container_manager.get_mount_info()
        if mount_info:
            mount_section = get_mount_section_text(self.container_manager.container_name, mount_info)
        else:
            mount_section = get_no_mount_section_text()
        self.system_prompt = get_system_prompt(self.workspace_dir, mount_section)

    def add_working_directory(self, path):
        needs_restart = self.container_manager.add_working_dir(path)
        mount_path = ContainerManager.convert_path(path, lowercase=True)
        if not needs_restart:
            return {"status": "already_mounted", "mount_path": mount_path}

        result = (
            self.container_manager.restart_with_new_mounts()
            if self.container_manager.container_running()
            else self.container_manager.start()
        )

        self._update_system_message()
        self._log_container_info()
        result["mount_path"] = mount_path
        return result

    def _log_container_info(self):
        info = {
            "container_name": self.container_manager.container_name,
            "working_dirs": self.container_manager.working_dirs,
        }
        if info != self._last_logged_container_info:
            self.logger.container_info(info)
            self._last_logged_container_info = info

    def stop_container(self):
        if self.container_manager.container_exists():
            self.container_manager.stop()
            print(f"{Fore.CYAN}🐳 Container stopped: {self.container_manager.container_name}{Style.RESET_ALL}")

    def cleanup_empty_session(self):
        has_user_message = any(
            msg.get("role") == "user" and isinstance(msg.get("content"), str) for msg in self.messages
        )
        if has_user_message:
            return False

        self.container_manager.remove()
        if os.path.exists(self.debug_file):
            os.remove(self.debug_file)
            print(f"{Fore.YELLOW}🧹 Cleaned up empty session{Style.RESET_ALL}")
        return True

    # ---- token counting + context mgmt ----

    def _count_tokens(self, messages):
        try:
            tool_schemas = [tool.get_schema() for tool in self.tools]
            return self.provider.count_tokens(messages=messages, system=self.system_prompt, tools=tool_schemas)
        except Exception:
            total_chars = len(self.system_prompt)
            for msg in messages:
                c = msg.get("content")
                if isinstance(c, str):
                    total_chars += len(c)
                elif isinstance(c, list):
                    for item in c:
                        if isinstance(item, dict):
                            total_chars += len(json.dumps(item))
            return total_chars // 4

    def _get_turns(self):
        turns = []
        current_turn = []
        for msg in self.messages:
            if msg["role"] == "user" and current_turn:
                content = msg.get("content", "")
                if (
                    isinstance(content, list)
                    and content
                    and isinstance(content[0], dict)
                    and content[0].get("type") == "tool_result"
                ):
                    current_turn.append(msg)
                else:
                    turns.append(current_turn)
                    current_turn = [msg]
            else:
                current_turn.append(msg)
        if current_turn:
            turns.append(current_turn)
        return turns

    def _is_context_error(self, error):
        error_str = str(error).lower()
        return any(
            x in error_str
            for x in [
                "context_length_exceeded",
                "max_tokens",
                "too many tokens",
                "too long",
                "maximum context length",
                "context window",
                "exceed",
            ]
        )

    def _parse_context_error(self, error):
        error_str = str(error)
        anthropic_match = re.search(r"(\d+) tokens > (\d+) maximum", error_str)
        if anthropic_match:
            return int(anthropic_match.group(1)), int(anthropic_match.group(2))

        used_match = re.search(r"resulted in (\d+) tokens", error_str)
        if not used_match:
            used_match = re.search(r"(\d+) tokens.*(?:exceed|over)", error_str, re.IGNORECASE)

        limit_match = re.search(r"maximum context length is (\d+)", error_str)
        if not limit_match:
            limit_match = re.search(r"max.*?(\d+) tokens", error_str, re.IGNORECASE)

        tokens_used = int(used_match.group(1)) if used_match else None
        tokens_limit = int(limit_match.group(1)) if limit_match else None
        return tokens_used, tokens_limit

    def _estimate_tokens_per_turn(self, total_tokens=None):
        turns = self._get_turns()
        if len(turns) < 2:
            return 10000
        if total_tokens is None:
            total_tokens = self._count_tokens(self.messages)
        return max(1, total_tokens // len(turns))

    def _drop_multiple_oldest_turns(self, tokens_to_remove=None, tokens_used=None):
        turns = self._get_turns()
        if len(turns) < 2:
            return 0

        if tokens_to_remove is None or tokens_to_remove <= 0:
            first_turn_len = len(turns[0])
            self.messages = self.messages[first_turn_len:]
            self._dropped_turns_this_turn = True
            self.logger.drop_turn_marker()
            if config.ui.show_drop_indicator:
                print(f"{Fore.YELLOW}📦 Compacting...{Style.RESET_ALL}")
            return 1

        avg_tokens_per_turn = self._estimate_tokens_per_turn(tokens_used)
        tokens_with_buffer = int(tokens_to_remove * 1.5)
        turns_to_drop = max(1, -(-tokens_with_buffer // avg_tokens_per_turn))
        turns_to_drop = min(turns_to_drop, len(turns) - 1)

        messages_to_remove = sum(len(turns[i]) for i in range(turns_to_drop))
        self.messages = self.messages[messages_to_remove:]
        self._dropped_turns_this_turn = True

        self.logger.drop_turn_marker()
        if config.ui.show_drop_indicator:
            print(f"{Fore.YELLOW}📦 Compacting...{Style.RESET_ALL}")
        return turns_to_drop

    def _drop_oldest_turn(self, tokens_to_remove=None, tokens_used=None):
        return self._drop_multiple_oldest_turns(tokens_to_remove, tokens_used) > 0

    # ---- provider/cost ----

    def _capture_rate_limit_info(self, response):
        if response and response.raw_response:
            self.rate_limit_info = self.provider.get_rate_limit_info(response.raw_response)
        else:
            self.rate_limit_info = {}

        if response and response.usage:
            usage = response.usage
            self.context_tokens = usage.input_tokens + usage.cache_creation_input_tokens + usage.cache_read_input_tokens
            self._calculate_cost(usage)
            self._set_last_thought_tokens(response)

    @staticmethod
    def calculate_cost_from_usage(usage, model="claude-opus-4-5"):
        pricing = config.models.pricing.get(model)
        if not pricing:
            return 0.0
        return (
            usage.get("input", 0) * pricing.input / 1_000_000
            + usage.get("output", 0) * pricing.output / 1_000_000
            + usage.get("cache_write", 0) * pricing.cache_write / 1_000_000
            + usage.get("cache_read", 0) * pricing.cache_read / 1_000_000
        )

    def _calculate_cost(self, usage):
        self.last_usage = {
            "input": usage.input_tokens,
            "output": usage.output_tokens,
            "cache_write": usage.cache_creation_input_tokens,
            "cache_read": usage.cache_read_input_tokens,
            "reasoning": getattr(usage, "reasoning_tokens", 0),
        }
        self.last_req_cost = self.calculate_cost_from_usage(self.last_usage, self.model)
        self.run_cost += self.last_req_cost
        self.session_cost += self.last_req_cost

    
    def _set_last_thought_tokens(self, response) -> None:
        """Compute last thought token count for UI.

        OpenAI: uses response.usage.reasoning_tokens (from providers.OpenAIProvider).
        Anthropic: no separate thinking token count, so we estimate from thinking text
        length if a thinking block exists.
        """
        self._last_thought_tokens = 0

        if response and getattr(response, "usage", None):
            self._last_thought_tokens = int(getattr(response.usage, "reasoning_tokens", 0) or 0)

        if self._last_thought_tokens <= 0 and response and getattr(response, "content", None):
            for b in response.content:
                if isinstance(b, dict) and b.get("type") == "thinking":
                    t = b.get("thinking", "") or ""
                    self._last_thought_tokens = max(1, len(t) // 4)
                    return
# ---- UI ----

    def print_status(self):
        effective_limit = self.max_context_tokens - self.max_output_tokens
        if self.messages:
            self.context_tokens = self._count_tokens(self.messages)

        print_status_box(
            divider_width=config.ui.divider_width,
            run_cost=self.run_cost,
            session_cost=self.session_cost,
            rate_limit_info=self.rate_limit_info,
            context_tokens=self.context_tokens,
            effective_limit=effective_limit,
        )

    # ---- API calls ----

    def _request_llm_once(self, *, tool_schemas, thinking_config, print_text: bool):
        if self.stream:
            return self._call_api_streaming(print_text=print_text, thinking_config=thinking_config)

        response = self.provider.create(
            messages=self.messages,
            system=self.system_prompt,
            tools=tool_schemas,
            max_tokens=self.max_output_tokens,
            thinking_config=thinking_config,
        )
        self._capture_rate_limit_info(response)
        return response, response.content

    def _call_api(self, print_text=True):
        max_retries = config.agent.max_retries
        tool_schemas = [tool.get_schema() for tool in self.tools]

        thinking_config = None
        if self.think:
            thinking_config = {"type": "enabled", "budget_tokens": self.thinking_budget}

        while True:
            for attempt in range(max_retries + 1):
                try:
                    response, content_list = self._request_llm_once(
                        tool_schemas=tool_schemas,
                        thinking_config=thinking_config,
                        print_text=print_text,
                    )
                    if print_text and not self.stream:
                            self._print_content(content_list)
                    return response, content_list

                except Exception as e:
                    error_str = str(e).lower()

                    if self._is_context_error(e):
                        tokens_used, _tokens_limit = self._parse_context_error(e)
                        tokens_to_remove = None
                        if tokens_used:
                            effective_limit = self.max_context_tokens - self.max_output_tokens
                            tokens_to_remove = tokens_used - effective_limit
                        if not self._drop_oldest_turn(tokens_to_remove, tokens_used):
                            print(f"{Fore.RED}Context limit exceeded but cannot drop more turns{Style.RESET_ALL}")
                            raise
                        break

                    if "rate" in error_str or "429" in error_str:
                        if attempt == max_retries:
                            print(
                                f"{Fore.RED}Rate limit exceeded after {max_retries + 1} attempts{Style.RESET_ALL}"
                            )
                            raise

                        retry_after = None
                        if hasattr(e, "response") and e.response is not None:
                            retry_after = getattr(e.response.headers, "get", lambda _x: None)("retry-after")
                        delay = int(retry_after) if retry_after else 30
                        print(
                            f"{Fore.YELLOW}⏳ Rate limited, waiting {delay}s before retry ({attempt + 1}/{max_retries})...{Style.RESET_ALL}"
                        )
                        time.sleep(delay)
                    else:
                        raise
            else:
                break

    def _call_api_streaming(self, print_text=True, thinking_config=None):
        from .stream_accumulator import StreamAccumulator

        tool_schemas = [tool.get_schema() for tool in self.tools]

        while True:
            try:
                stream_gen = self.provider.stream(
                    messages=self.messages,
                    system=self.system_prompt,
                    tools=tool_schemas,
                    max_tokens=self.max_output_tokens,
                    thinking_config=thinking_config,
                )

                acc = StreamAccumulator(
                    print_text=print_text,
                    assistant_prefix=config.ui.prefixes.assistant,
                    get_assistant_color=get_color,
                    show_think_content=config.ui.show_think_content,
                )

                final_response = None
                try:
                    while True:
                        acc.on_event(next(stream_gen))
                except StopIteration as e:
                    final_response = e.value

                content_list = acc.content_list

                if final_response:
                    final_response.content = content_list
                    self._capture_rate_limit_info(final_response)
                return final_response, content_list

            except Exception as e:
                if self._is_context_error(e):
                    tokens_used, _tokens_limit = self._parse_context_error(e)
                    tokens_to_remove = None
                    if tokens_used:
                        effective_limit = self.max_context_tokens - self.max_output_tokens
                        tokens_to_remove = tokens_used - effective_limit
                    if not self._drop_oldest_turn(tokens_to_remove, tokens_used):
                        print(f"{Fore.RED}Context limit exceeded but cannot drop more turns{Style.RESET_ALL}")
                        raise
                    continue
                raise

    def chat(self, message):
        self.messages.append({"role": "user", "content": message})
        response, content_list = self._call_api()
        self.messages.append({"role": "assistant", "content": content_list})
        return response

    def _print_content(self, content_list):
        for block in content_list:
            block_type = block.get("type")
            if block_type == "thinking":
                print(f"{Fore.MAGENTA}Thinking...{Style.RESET_ALL}")
                if config.ui.show_think_content:
                    t = block.get('thinking', '')
                    if t:
                        print(f"{get_color('thinking')}{t}{Style.RESET_ALL}")
            elif block_type == "text":
                text = block.get("text", "")
                if text:
                    print(f"{Fore.GREEN}Agent:{Style.RESET_ALL} {text}")

    # ---- tool execution + response processing ----

    def _ensure_thinking_blocks(self):
        for msg in self.messages:
            if msg.get("role") == "assistant":
                content = msg.get("content", [])
                if isinstance(content, list):
                    msg["content"] = [
                        block
                        for block in content
                        if block.get("type") != "thinking" or block.get("signature")
                    ]

    def _execute_tools(self, response, prefix=""):
        from .tool_exec import execute_tool_uses

        def _print(line: str, path: str | None = None) -> None:
            if path:
                tool_color = get_color('tool')
                path_color = get_color('tool_path')
                print(f"{tool_color}{line}{Style.RESET_ALL} {path_color}(In {path}){Style.RESET_ALL}")
            else:
                print(f"{get_color('tool')}{line}{Style.RESET_ALL}")

        tool_results = execute_tool_uses(
            tool_uses=response.content,
            tool_map=self.tool_map,
            describe=get_tool_description,
            print_line=_print,
            prefix=prefix,
        )

        for block in response.content:
            if block.get("type") == "tool_use":
                description, path = get_tool_description(block["name"], block["input"])
                # Store description with path for display history
                full_desc = f"{description} (In {path})" if path else description
                self.display_history.append(("tool", full_desc))

        for tr in tool_results:
            try:
                parsed = json.loads(tr["content"])
            except Exception:
                parsed = {"error": "Tool returned non-JSON"}
                tr["content"] = json.dumps(parsed)
            if isinstance(parsed, dict) and "error" in parsed:
                print(f"{Fore.RED}  \u2717 Error: {parsed['error']}{Style.RESET_ALL}")

        return tool_results

    def _process_response(self, content_list):
        agent_texts = []
        found_thinking = False
        for block in content_list:
            if block.get("type") == "thinking":
                found_thinking = True
                self.display_history.append(("thinking", "🧠 Thinking..."))
                if not self.stream:
                    print(f"{Fore.MAGENTA}🧠 Thinking...{Style.RESET_ALL}")
            elif block.get("type") == "text":
                if not self.stream:
                    print(f"{Fore.GREEN}Agent: {block['text']}{Style.RESET_ALL}")
                agent_texts.append(block["text"])
        return agent_texts

    # ---- compaction ----

    def _compact_after_turn(self):
        print()
        print_divider(config.ui.divider_width)
        print()
        print(f"{Fore.YELLOW}📦 Compacting...{Style.RESET_ALL}")

        sr_message = {"role": "user", "content": self.SUMMARIZATION_TEMPLATE}
        messages_with_sr = self.messages + [sr_message]

        try:
            tool_schemas = [tool.get_schema() for tool in self.tools]
            stream_gen = self.provider.stream(
                messages=messages_with_sr,
                system=self.system_prompt,
                tools=tool_schemas,
                max_tokens=self.max_output_tokens,
            )

            summary_text = ""
            final_response = None
            try:
                while True:
                    event = next(stream_gen)
                    if event.type == "text_delta":
                        summary_text += event.data
            except StopIteration as e:
                final_response = e.value

            if final_response:
                self._capture_rate_limit_info(final_response)

            num_turns = len(self._get_turns())
            self.logger.compaction(
                "Post-turn compaction after context overflow",
                f"1-{num_turns}",
                summary_text,
            )
            self.logger.raw("")
            self.logger.req_usage(self.model_short, self.last_usage)

            self.messages = [{"role": "user", "content": summary_text}]
            self.messages.append(
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "I understand the context and will continue helping with your request.",
                        }
                    ],
                }
            )

            if config.ui.show_compact_content:
                print(f"{get_color('system')}{summary_text}{Style.RESET_ALL}")

        except Exception as e:
            print(f"{Fore.RED}⚠️  Compaction failed: {e}{Style.RESET_ALL}")
            self.logger.raw(f"Compaction error: {e}")

    # ---- run loop ----

    def run(self, user_input, max_turns=None, initial_messages=None, display_history=None):
        self._dropped_turns_this_turn = False

        if initial_messages is not None:
            self.messages = initial_messages
        if display_history is not None:
            self.display_history = display_history

        self.display_history.append(("user", user_input))

        if self.think and self.messages:
            self._ensure_thinking_blocks()

        turn_num = len([h for h in self.display_history if h[0] == "user"])
        self.logger.turn_start(turn_num, user_input)

        if max_turns is None:
            max_turns = config.agent.max_turns

        response = self._agent_loop(user_input, max_turns)

        if self._dropped_turns_this_turn:
            self._compact_after_turn()
            self._dropped_turns_this_turn = False

        return response

    def _agent_loop(self, initial_input, max_turns):
        current_input = initial_input
        response = None

        for _ in range(max_turns):
            if current_input is not None:
                response = self.chat(current_input)
                content_list = self.messages[-1]["content"]
            else:
                response, content_list = self._call_api()
                self.messages.append({"role": "assistant", "content": content_list})

            self.logger.assistant(content_list)
            self.logger.req_usage(self.model_short, self.last_usage)
            agent_texts = self._process_response(content_list)

            if response.stop_reason == "tool_use":
                tool_results = self._execute_tools(response)
                self.logger.tool_results(tool_results)
                current_input = tool_results
            else:
                self.logger.end_turn()
                if agent_texts:
                    self.display_history.append(("assistant", "\n".join(agent_texts)))
                return response

        print(f"{Fore.RED}Max turns reached{Style.RESET_ALL}")
        self.logger.raw("Max turns reached")
        return response

    def continue_incomplete_turn(self, incomplete_turn, max_turns=None):
        if incomplete_turn["type"] == "execute_tools":
            class _Tmp:
                def __init__(self, content):
                    self.content = content

            tmp_response = _Tmp(incomplete_turn.get("tool_uses", []))
            tool_results = self._execute_tools(tmp_response, prefix="(Resuming) ")
            self.messages.append({"role": "user", "content": tool_results})
            self.logger.tool_results(tool_results)

        elif incomplete_turn["type"] != "continue":
            print(f"{Fore.RED}Unknown incomplete turn type: {incomplete_turn['type']}{Style.RESET_ALL}")
            return None

        if self.think:
            self._ensure_thinking_blocks()

        if max_turns is None:
            max_turns = config.agent.max_turns

        return self._agent_loop(None, max_turns)

    def get_state_for_resume(self):
        return {
            "messages": self.messages,
            "display_history": self.display_history,
            "container_info": {
                "container_name": self.container_manager.container_name,
                "working_dirs": self.container_manager.working_dirs,
            },
        }

    def _log_end_turn(self):
        """Mark current turn as ended (e.g., due to interruption)."""
        self.logger.end_turn()
