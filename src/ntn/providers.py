"""Provider abstraction layer for LLM APIs."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Generator, List, Optional

from .provider_types import APIResponse, BaseProvider, StreamEvent, Usage


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
    """OpenAI GPT API provider using Responses API.

    This enables:
    - reasoning token accounting via usage.output_tokens_details.reasoning_tokens
    - optional reasoning summaries ("thinking content") when requested
    - tool calling via output items of type "function_call"
    - streaming deltas for both assistant text and reasoning summaries
    """

    def __init__(self, model: str):
        import openai
        import tiktoken

        self.client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = model
        self.encoding = tiktoken.get_encoding("cl100k_base")

    def create(
        self,
        messages: List[Dict],
        system: str,
        tools: List[Dict],
        max_tokens: int,
        thinking_config: Optional[Dict] = None,
    ) -> APIResponse:
        """Make non-streaming OpenAI API call using Responses API."""
        reasoning = self._build_reasoning(thinking_config)
        resp = self.client.responses.create(
            model=self.model,
            instructions=system,
            input=self._convert_messages(messages),
            tools=self.convert_tools(tools),
            tool_choice="auto" if tools else "none",
            reasoning=reasoning,
            max_output_tokens=max_tokens,
        )
        return self._normalize_response(resp)

    def stream(
        self,
        messages: List[Dict],
        system: str,
        tools: List[Dict],
        max_tokens: int,
        thinking_config: Optional[Dict] = None,
    ) -> Generator[StreamEvent, None, APIResponse]:
        """Stream OpenAI API response using Responses API."""
        reasoning = self._build_reasoning(thinking_config)
        stream = self.client.responses.create(
            model=self.model,
            instructions=system,
            input=self._convert_messages(messages),
            tools=self.convert_tools(tools),
            tool_choice="auto" if tools else "none",
            reasoning=reasoning,
            max_output_tokens=max_tokens,
            stream=True,
        )

        import openai.types.responses as r

        text_started = False
        thinking_started = False
        final_response = None

        for ev in stream:
            if isinstance(ev, r.ResponseCompletedEvent):
                final_response = ev.response
                continue

            # Reasoning summary stream -> thinking
            if isinstance(ev, r.ResponseReasoningSummaryPartAddedEvent):
                if not thinking_started:
                    thinking_started = True
                    yield StreamEvent("thinking_start")
                continue

            if isinstance(ev, r.ResponseReasoningSummaryTextDeltaEvent):
                if not thinking_started:
                    thinking_started = True
                    yield StreamEvent("thinking_start")
                yield StreamEvent("thinking_delta", ev.delta)
                continue

            if isinstance(ev, r.ResponseReasoningSummaryTextDoneEvent):
                if thinking_started:
                    thinking_started = False
                    yield StreamEvent("content_block_stop")
                continue

            # Visible assistant text
            if isinstance(ev, r.ResponseTextDeltaEvent):
                if not text_started:
                    text_started = True
                    yield StreamEvent("text_start")
                yield StreamEvent("text_delta", ev.delta)
                continue

            if isinstance(ev, r.ResponseTextDoneEvent):
                if text_started:
                    text_started = False
                    yield StreamEvent("content_block_stop")
                continue

            # Tool calling
            if isinstance(ev, r.ResponseOutputItemAddedEvent):
                item = ev.item
                if getattr(item, "type", None) == "function_call":
                    yield StreamEvent("tool_use_start", {"id": item.call_id, "name": item.name})
                continue

            if isinstance(ev, r.ResponseFunctionCallArgumentsDeltaEvent):
                yield StreamEvent("tool_input_delta", ev.delta)
                continue

            if isinstance(ev, r.ResponseFunctionCallArgumentsDoneEvent):
                yield StreamEvent("content_block_stop")
                continue

        # Close any open block
        if thinking_started or text_started:
            yield StreamEvent("content_block_stop")

        if final_response is None:
            usage = Usage(0, 0, 0, 0, 0)
            return APIResponse(content=[], stop_reason="end_turn", usage=usage, raw_response=getattr(stream, "response", None))

        return self._normalize_response(final_response)

    def count_tokens(self, messages: List[Dict], system: str, tools: List[Dict]) -> int:
        """Approximate token count using chat-style formula."""
        num_tokens = 0
        num_tokens += 3
        num_tokens += len(self.encoding.encode(system))
        for message in messages:
            num_tokens += 3
            for key, value in message.items():
                if isinstance(value, str):
                    num_tokens += len(self.encoding.encode(value))
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            if "text" in item:
                                num_tokens += len(self.encoding.encode(item["text"]))
                            elif "content" in item:
                                num_tokens += len(self.encoding.encode(str(item["content"])))
                            else:
                                num_tokens += len(self.encoding.encode(json.dumps(item)))
                if key == "name":
                    num_tokens += 1
        num_tokens += 3
        return num_tokens

    def convert_tools(self, tools: List[Dict]) -> List[Dict]:
        """Convert Anthropic tool format to Responses API function format."""
        converted = []
        for tool in tools:
            converted.append(
                {
                    "type": "function",
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                }
            )
        return converted

    def get_rate_limit_info(self, response: Any) -> Dict:
        """Extract OpenAI rate limit headers."""
        headers = {}
        if hasattr(response, "headers"):
            headers = response.headers
        elif hasattr(response, "response") and getattr(response, "response") is not None:
            headers = response.response.headers

        def get_int(key: str):
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

    def _build_reasoning(self, thinking_config: Optional[Dict]) -> Dict:
        """Build reasoning configuration for Responses API."""
        if not thinking_config:
            return {"effort": "low"}

        budget = thinking_config.get("budget_tokens", 0)
        if budget > 50000:
            effort = "high"
        elif budget > 20000:
            effort = "medium"
        else:
            effort = "low"

        reasoning = {"effort": effort}
        # Request summary for thinking content display
        reasoning["summary"] = "auto"
        return reasoning

    def _convert_messages(self, messages: List[Dict]) -> List[Dict]:
        """Convert messages from Anthropic to Responses API format."""
        items = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")

            if role == "user":
                if isinstance(content, str):
                    items.append({"role": "user", "content": content})
                elif isinstance(content, list) and content and content[0].get("type") == "tool_result":
                    for tr in content:
                        items.append(
                            {
                                "type": "function_call_output",
                                "call_id": tr.get("tool_use_id"),
                                "output": str(tr.get("content", "")),
                            }
                        )
                else:
                    items.append({"role": "user", "content": json.dumps(content)})

            elif role == "assistant":
                if isinstance(content, str):
                    items.append({"role": "assistant", "content": content})
                elif isinstance(content, list):
                    for block in content:
                        if block.get("type") == "tool_use":
                            items.append(
                                {
                                    "type": "function_call",
                                    "call_id": block.get("id"),
                                    "name": block.get("name"),
                                    "arguments": json.dumps(block.get("input", {})),
                                }
                            )
                        elif block.get("type") == "text":
                            items.append({"role": "assistant", "content": block.get("text", "")})
                        # thinking blocks omitted

        return items

    def _normalize_response(self, response: Any) -> APIResponse:
        """Convert Responses API response to unified format."""
        content = []

        # 1) thinking summary (if any)
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) == "reasoning":
                summary_parts = getattr(item, "summary", None) or []
                summary_texts = []
                for part in summary_parts:
                    if isinstance(part, dict):
                        if part.get("type") == "summary_text":
                            summary_texts.append(part.get("text", ""))
                    else:
                        if getattr(part, "type", None) == "summary_text":
                            summary_texts.append(getattr(part, "text", ""))
                if summary_texts:
                    content.append(
                        {
                            "type": "thinking",
                            "thinking": "\n".join(summary_texts),
                            "signature": "openai_summary",
                        }
                    )

        # 2) tool calls
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) == "function_call":
                try:
                    args = json.loads(getattr(item, "arguments", "") or "{}")
                except Exception:
                    args = {}
                content.append(
                    {
                        "type": "tool_use",
                        "id": getattr(item, "call_id", ""),
                        "name": getattr(item, "name", ""),
                        "input": args,
                    }
                )

        # 3) assistant text
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) == "message" and getattr(item, "role", None) == "assistant":
                for part in getattr(item, "content", []) or []:
                    if getattr(part, "type", None) == "output_text":
                        content.append({"type": "text", "text": getattr(part, "text", "")})

        usage_obj = getattr(response, "usage", None)
        cached_tokens = (
            getattr(getattr(usage_obj, "input_tokens_details", None), "cached_tokens", 0) if usage_obj else 0
        )
        reasoning_tokens = (
            getattr(getattr(usage_obj, "output_tokens_details", None), "reasoning_tokens", 0) if usage_obj else 0
        )

        usage = Usage(
            input_tokens=getattr(usage_obj, "input_tokens", 0) or 0,
            output_tokens=getattr(usage_obj, "output_tokens", 0) or 0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=cached_tokens or 0,
            reasoning_tokens=reasoning_tokens or 0,
        )

        stop_reason = "tool_use" if any(b.get("type") == "tool_use" for b in content) else "end_turn"
        return APIResponse(content=content, stop_reason=stop_reason, usage=usage, raw_response=response)


def create_provider(model_id: str, provider_name: str) -> BaseProvider:
    """Factory function to create the appropriate provider."""
    if provider_name == "openai":
        return OpenAIProvider(model_id)
    else:
        return AnthropicProvider(model_id)
