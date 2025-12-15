"""Provider shared types.

Separated into its own module to avoid circular imports when providers are split
across files.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Generator, List, Optional


@dataclass
class Usage:
    """Unified token usage across providers."""

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass
class StreamEvent:
    """Unified streaming event."""

    type: str
    data: Any = None


@dataclass
class APIResponse:
    """Unified API response."""

    content: List[Dict]
    stop_reason: str
    usage: Usage
    raw_response: Any


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
        pass

    @abstractmethod
    def count_tokens(self, messages: List[Dict], system: str, tools: List[Dict]) -> int:
        pass

    @abstractmethod
    def convert_tools(self, tools: List[Dict]) -> List[Dict]:
        pass

    @abstractmethod
    def get_rate_limit_info(self, response: Any) -> Dict:
        pass
