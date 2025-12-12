"""
Provider abstraction layer for LLM APIs.

This module provides a unified interface for Anthropic and OpenAI APIs,
handling the differences in message formats, streaming, and tool schemas.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Generator, List, Optional
import json
import os


@dataclass
class Usage:
    """Unified token usage across providers."""

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class StreamEvent:
    """Unified streaming event."""

    type: str  # "thinking_start", "thinking_delta", "signature_delta", "text_start",
    # "text_delta", "tool_use_start", "tool_input_delta", "content_block_stop"
    data: Any = None


@dataclass
class APIResponse:
    """Unified API response."""

    content: List[Dict]  # Normalized content blocks
    stop_reason: str  # "end_turn", "tool_use", "max_tokens"
    usage: Usage
    raw_response: Any  # Original response for rate limit headers


class BaseProvider(ABC):
    """Base class for LLM providers."""

    @abstractmethod
    def create(
        self,
        messages: List[Dict],
        system: str,
        tools: List[Dict],
        max_tokens: int,
        thinking_config: Optional[Dict] = None,
    ) -> APIResponse:
        """Make a non-streaming API call."""
        pass

    @abstractmethod
    def stream(
        self,
        messages: List[Dict],
        system: str,
        tools: List[Dict],
        max_tokens: int,
        thinking_config: Optional[Dict] = None,
    ) -> Generator[StreamEvent, None, APIResponse]:
        """Make a streaming API call. Yields events, returns final response."""
        pass

    @abstractmethod
    def count_tokens(
        self, messages: List[Dict], system: str, tools: List[Dict]
    ) -> int:
        """Count tokens for the given messages."""
        pass

    @abstractmethod
    def convert_tools(self, tools: List[Dict]) -> List[Dict]:
        """Convert tool schemas to provider-specific format."""
        pass

    @abstractmethod
    def get_rate_limit_info(self, response: Any) -> Dict:
        """Extract rate limit info from response headers."""
        pass


class AnthropicProvider(BaseProvider):
    """Anthropic Claude API provider."""

    def __init__(self, model: str):
        import anthropic

        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model = model

    def create(
        self,
        messages: List[Dict],
        system: str,
        tools: List[Dict],
        max_tokens: int,
        thinking_config: Optional[Dict] = None,
    ) -> APIResponse:
        """Make non-streaming Anthropic API call."""
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": self._format_system(system),
            "tools": self.convert_tools(tools),
            "messages": messages,
        }
        if thinking_config:
            kwargs["thinking"] = thinking_config

        response = self.client.messages.create(**kwargs)
        return self._normalize_response(response)

    def stream(
        self,
        messages: List[Dict],
        system: str,
        tools: List[Dict],
        max_tokens: int,
        thinking_config: Optional[Dict] = None,
    ) -> Generator[StreamEvent, None, APIResponse]:
        """Stream Anthropic API response."""
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": self._format_system(system),
            "tools": self.convert_tools(tools),
            "messages": messages,
        }
        if thinking_config:
            kwargs["thinking"] = thinking_config

        with self.client.messages.stream(**kwargs) as stream:
            for event in stream:
                converted = self._convert_stream_event(event)
                if converted:
                    yield converted
            final_response = stream.get_final_message()
            raw_response = stream.response

        return self._normalize_response(final_response, raw_response)

    def count_tokens(
        self, messages: List[Dict], system: str, tools: List[Dict]
    ) -> int:
        """Count tokens using Anthropic API."""
        response = self.client.messages.count_tokens(
            model=self.model,
            system=self._format_system(system),
            tools=self.convert_tools(tools),
            messages=messages,
        )
        return response.input_tokens

    def convert_tools(self, tools: List[Dict]) -> List[Dict]:
        """Anthropic tools use input_schema - just add cache_control to last."""
        if not tools:
            return []
        result = [tool.copy() for tool in tools]
        result[-1]["cache_control"] = {"type": "ephemeral"}
        return result

    def _format_system(self, system: str) -> List[Dict]:
        """Format system prompt with cache control."""
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

    def _convert_stream_event(self, event) -> Optional[StreamEvent]:
        """Convert Anthropic stream event to unified format."""
        if event.type == "content_block_start":
            if hasattr(event, "content_block") and hasattr(event.content_block, "type"):
                if event.content_block.type == "thinking":
                    return StreamEvent("thinking_start")
                elif event.content_block.type == "text":
                    return StreamEvent("text_start")
                elif event.content_block.type == "tool_use":
                    return StreamEvent(
                        "tool_use_start",
                        {"id": event.content_block.id, "name": event.content_block.name},
                    )
        elif event.type == "content_block_delta":
            if hasattr(event, "delta") and hasattr(event.delta, "type"):
                if event.delta.type == "thinking_delta":
                    return StreamEvent("thinking_delta", event.delta.thinking)
                elif event.delta.type == "signature_delta":
                    return StreamEvent("signature_delta", event.delta.signature)
                elif event.delta.type == "text_delta":
                    return StreamEvent("text_delta", event.delta.text)
                elif event.delta.type == "input_json_delta":
                    return StreamEvent("tool_input_delta", event.delta.partial_json)
        elif event.type == "content_block_stop":
            return StreamEvent("content_block_stop")

        return None

    def _normalize_response(
        self, response, raw_response=None
    ) -> APIResponse:
        """Convert Anthropic response to unified format."""
        content = []
        for block in response.content:
            if block.type == "thinking":
                thinking_block = {"type": "thinking", "thinking": block.thinking}
                if hasattr(block, "signature") and block.signature:
                    thinking_block["signature"] = block.signature
                content.append(thinking_block)
            elif hasattr(block, "text"):
                content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                content.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )

        usage = Usage(
            input_tokens=getattr(response.usage, "input_tokens", 0) or 0,
            output_tokens=getattr(response.usage, "output_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(
                response.usage, "cache_creation_input_tokens", 0
            )
            or 0,
            cache_read_input_tokens=getattr(
                response.usage, "cache_read_input_tokens", 0
            )
            or 0,
        )

        return APIResponse(
            content=content,
            stop_reason=response.stop_reason,
            usage=usage,
            raw_response=raw_response or response,
        )

    def get_rate_limit_info(self, response: Any) -> Dict:
        """Extract Anthropic rate limit headers."""
        headers = {}
        if hasattr(response, "headers"):
            headers = response.headers
        elif hasattr(response, "_raw_response") and response._raw_response:
            headers = response._raw_response.headers

        def get_int(key):
            val = headers.get(key)
            return int(val) if val else None

        return {
            "request_limit": get_int("anthropic-ratelimit-requests-limit"),
            "request_remaining": get_int("anthropic-ratelimit-requests-remaining"),
            "input_limit": get_int("anthropic-ratelimit-input-tokens-limit"),
            "input_remaining": get_int("anthropic-ratelimit-input-tokens-remaining"),
            "output_limit": get_int("anthropic-ratelimit-output-tokens-limit"),
            "output_remaining": get_int("anthropic-ratelimit-output-tokens-remaining"),
        }


class OpenAIProvider(BaseProvider):
    """OpenAI GPT API provider."""

    def __init__(self, model: str):
        import openai
        import tiktoken

        self.client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = model
        # Use cl100k_base encoding for GPT-4 and later models
        self.encoding = tiktoken.get_encoding("cl100k_base")

    def create(
        self,
        messages: List[Dict],
        system: str,
        tools: List[Dict],
        max_tokens: int,
        thinking_config: Optional[Dict] = None,
    ) -> APIResponse:
        """Make non-streaming OpenAI API call."""
        kwargs = self._build_kwargs(messages, system, tools, max_tokens, thinking_config)
        response = self.client.chat.completions.create(**kwargs)
        return self._normalize_response(response)

    def stream(
        self,
        messages: List[Dict],
        system: str,
        tools: List[Dict],
        max_tokens: int,
        thinking_config: Optional[Dict] = None,
    ) -> Generator[StreamEvent, None, APIResponse]:
        """Stream OpenAI API response."""
        kwargs = self._build_kwargs(messages, system, tools, max_tokens, thinking_config)
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}

        current_tool_call = None
        final_usage = None
        http_response = None
        text_started = False
        finish_reason = None

        response_stream = self.client.chat.completions.create(**kwargs)

        # Capture HTTP response for headers (rate limits)
        if hasattr(response_stream, "response"):
            http_response = response_stream.response

        for chunk in response_stream:

            # Handle usage in final chunk
            if hasattr(chunk, "usage") and chunk.usage:
                final_usage = chunk.usage

            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

            # Handle text content
            if delta.content:
                if not text_started:
                    yield StreamEvent("text_start")
                    text_started = True
                yield StreamEvent("text_delta", delta.content)

            # Handle tool calls
            if delta.tool_calls:
                for tool_call_delta in delta.tool_calls:
                    if tool_call_delta.id:  # New tool call starting
                        if current_tool_call:
                            yield StreamEvent("content_block_stop")
                        current_tool_call = {
                            "id": tool_call_delta.id,
                            "name": tool_call_delta.function.name
                            if tool_call_delta.function
                            else "",
                        }
                        yield StreamEvent("tool_use_start", current_tool_call)

                    if tool_call_delta.function and tool_call_delta.function.arguments:
                        yield StreamEvent(
                            "tool_input_delta", tool_call_delta.function.arguments
                        )

            # Check for finish reason
            if chunk.choices[0].finish_reason:
                finish_reason = chunk.choices[0].finish_reason
                if text_started:
                    yield StreamEvent("content_block_stop")
                    text_started = False
                if current_tool_call:
                    yield StreamEvent("content_block_stop")
                    current_tool_call = None

        # Build final response
        # OpenAI: prompt_tokens INCLUDES cached tokens, so subtract to get non-cached
        cached_tokens = (
            getattr(
                getattr(final_usage, "prompt_tokens_details", None), "cached_tokens", 0
            )
            if final_usage
            else 0
        ) or 0
        prompt_tokens = final_usage.prompt_tokens if final_usage else 0
        usage = Usage(
            input_tokens=prompt_tokens - cached_tokens,  # Non-cached input only
            output_tokens=final_usage.completion_tokens if final_usage else 0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=cached_tokens,
        )

        # Map OpenAI finish_reason to unified stop_reason
        stop_reason_map = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
        }
        stop_reason = stop_reason_map.get(finish_reason, finish_reason or "end_turn")

        return APIResponse(
            content=[],  # Content built by streaming handler
            stop_reason=stop_reason,
            usage=usage,
            raw_response=http_response,  # HTTP response for rate limit headers
        )

    def count_tokens(
        self, messages: List[Dict], system: str, tools: List[Dict]
    ) -> int:
        """Count tokens using tiktoken."""
        token_count = 0

        # Count system message
        token_count += len(self.encoding.encode(system))

        # Count messages
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                token_count += len(self.encoding.encode(content))
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        token_count += len(self.encoding.encode(json.dumps(item)))

        # Count tools (rough estimate)
        if tools:
            token_count += len(self.encoding.encode(json.dumps(tools)))

        # Add overhead for message formatting
        token_count += len(messages) * 4

        return token_count

    def convert_tools(self, tools: List[Dict]) -> List[Dict]:
        """Convert Anthropic tool format to OpenAI function format."""
        openai_tools = []
        for tool in tools:
            openai_tool = {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            }
            openai_tools.append(openai_tool)
        return openai_tools

    def _build_kwargs(
        self,
        messages: List[Dict],
        system: str,
        tools: List[Dict],
        max_tokens: int,
        thinking_config: Optional[Dict],
    ) -> Dict:
        """Build OpenAI API kwargs."""
        # Convert messages format and prepend system message
        openai_messages = [{"role": "system", "content": system}]
        openai_messages.extend(self._convert_messages(messages))

        kwargs = {
            "model": self.model,
            "max_completion_tokens": max_tokens,
            "messages": openai_messages,
        }

        converted_tools = self.convert_tools(tools)
        if converted_tools:
            kwargs["tools"] = converted_tools

        # gpt-5.2 has built-in reasoning, use reasoning_effort for control
        if thinking_config:
            budget = thinking_config.get("budget_tokens", 0)
            if budget > 50000:
                kwargs["reasoning_effort"] = "high"
            elif budget > 20000:
                kwargs["reasoning_effort"] = "medium"
            else:
                kwargs["reasoning_effort"] = "low"

        return kwargs

    def _convert_messages(self, messages: List[Dict]) -> List[Dict]:
        """Convert messages from Anthropic to OpenAI format."""
        openai_messages = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")

            if role == "user":
                if isinstance(content, str):
                    openai_messages.append({"role": "user", "content": content})
                elif isinstance(content, list):
                    # Check if tool_result
                    if content and content[0].get("type") == "tool_result":
                        for item in content:
                            openai_messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": item.get("tool_use_id"),
                                    "content": str(item.get("content", "")),
                                }
                            )
                    else:
                        # Other structured content - stringify
                        text_parts = []
                        for item in content:
                            if isinstance(item, dict) and item.get("type") == "text":
                                text_parts.append(item.get("text", ""))
                            else:
                                text_parts.append(json.dumps(item))
                        openai_messages.append(
                            {"role": "user", "content": "\n".join(text_parts)}
                        )

            elif role == "assistant":
                if isinstance(content, str):
                    openai_messages.append({"role": "assistant", "content": content})
                elif isinstance(content, list):
                    # Extract text and tool_use from content blocks
                    text_parts = []
                    tool_calls = []

                    for block in content:
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            tool_calls.append(
                                {
                                    "id": block.get("id"),
                                    "type": "function",
                                    "function": {
                                        "name": block.get("name"),
                                        "arguments": json.dumps(block.get("input", {})),
                                    },
                                }
                            )
                        # Skip thinking blocks - OpenAI doesn't need them

                    assistant_msg = {"role": "assistant"}
                    if text_parts:
                        assistant_msg["content"] = "\n".join(text_parts)
                    if tool_calls:
                        assistant_msg["tool_calls"] = tool_calls

                    if text_parts or tool_calls:
                        openai_messages.append(assistant_msg)

        return openai_messages

    def _normalize_response(self, response) -> APIResponse:
        """Convert OpenAI response to unified format."""
        content = []
        choice = response.choices[0]
        message = choice.message

        # Add text content
        if message.content:
            content.append({"type": "text", "text": message.content})

        # Add tool calls
        if message.tool_calls:
            for tc in message.tool_calls:
                content.append(
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.function.name,
                        "input": json.loads(tc.function.arguments)
                        if tc.function.arguments
                        else {},
                    }
                )

        # Normalize stop reason
        stop_reason_map = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
        }
        stop_reason = stop_reason_map.get(choice.finish_reason, choice.finish_reason)

        # Handle usage
        # OpenAI: prompt_tokens INCLUDES cached tokens, so subtract to get non-cached
        cached_tokens = (
            getattr(
                getattr(response.usage, "prompt_tokens_details", None), "cached_tokens", 0
            )
            if response.usage
            else 0
        ) or 0
        prompt_tokens = response.usage.prompt_tokens if response.usage else 0
        usage = Usage(
            input_tokens=prompt_tokens - cached_tokens,  # Non-cached input only
            output_tokens=response.usage.completion_tokens if response.usage else 0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=cached_tokens,
        )

        return APIResponse(
            content=content,
            stop_reason=stop_reason,
            usage=usage,
            raw_response=response,
        )

    def get_rate_limit_info(self, response: Any) -> Dict:
        """Extract OpenAI rate limit headers."""
        headers = {}
        if hasattr(response, "headers"):
            headers = response.headers

        def get_int(key):
            val = headers.get(key)
            return int(val) if val else None

        return {
            "request_limit": get_int("x-ratelimit-limit-requests"),
            "request_remaining": get_int("x-ratelimit-remaining-requests"),
            "input_limit": get_int("x-ratelimit-limit-tokens"),
            "input_remaining": get_int("x-ratelimit-remaining-tokens"),
            "output_limit": None,
            "output_remaining": None,
        }


def create_provider(model_id: str, provider_name: str) -> BaseProvider:
    """Factory function to create the appropriate provider."""
    if provider_name == "openai":
        return OpenAIProvider(model_id)
    else:
        return AnthropicProvider(model_id)
