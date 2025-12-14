"""
Centralized configuration for the ntn agent.

Loads configuration from config.yaml bundled with the package.
API keys are read from environment variables (ANTHROPIC_API_KEY, OPENAI_API_KEY).

Usage:
    from ntn.config import config, get_color

    model_id = config.models.aliases["gpt"]
    limits = config.models.get_limits(model_id)
    provider = config.models.get_provider(model_id)
    color = get_color("assistant")  # Returns Fore.GREEN
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import yaml
from colorama import Fore, Style


@dataclass(frozen=True)
class ModelPricing:
    """Pricing per 1M tokens for a specific model."""

    input: float
    output: float
    cache_write: float
    cache_read: float


@dataclass(frozen=True)
class ModelLimits:
    """Per-model context and output limits."""

    max_context_tokens: int
    max_output_tokens: int


@dataclass(frozen=True)
class ModelsConfig:
    """Model-related configuration."""

    aliases: Dict[str, str]
    providers: Dict[str, str]
    default: str
    limits: Dict[str, ModelLimits]
    thinking_budget: Dict[str, int]
    pricing: Dict[str, ModelPricing]
    cache_requirements: Dict[str, int]

    def get_model_id(self, short_name: str) -> str:
        """Resolve short name to full model ID."""
        return self.aliases.get(short_name, self.aliases[self.default])

    def get_provider(self, model_id: str) -> str:
        """Get provider name for a model ID."""
        return self.providers.get(model_id, "anthropic")

    def get_limits(self, model_id: str) -> Optional[ModelLimits]:
        """Get context/output limits for a model."""
        return self.limits.get(model_id)

    def get_thinking_budget(self, model_id: str) -> int:
        """Get thinking budget for a model."""
        return self.thinking_budget.get(model_id, 63999)


@dataclass(frozen=True)
class DockerConfig:
    """Docker/container configuration."""

    default_image: str


@dataclass(frozen=True)
class PrefixesConfig:
    """Display prefixes for roles."""

    user: str
    assistant: str


@dataclass(frozen=True)
class ColorsConfig:
    """Color names for different elements."""

    user: str
    assistant: str
    thinking: str
    tool: str
    error: str
    warning: str
    system: str
    success: str


@dataclass(frozen=True)
class UIConfig:
    """UI display configuration."""

    divider_width: int
    show_compact_content: bool
    show_drop_indicator: bool
    prefixes: PrefixesConfig
    colors: ColorsConfig


@dataclass(frozen=True)
class AgentConfig:
    """Agent behavior configuration."""

    max_turns: int
    max_retries: int



@dataclass(frozen=True)
class WebSearchConfig:
    """Web search tool configuration."""

    max_results: int
    region: str


@dataclass(frozen=True)
class WebFetchConfig:
    """Web fetch tool configuration."""

    timeout: int
    content_limit: int


@dataclass(frozen=True)
class ToolsConfig:
    """Tool-related configuration."""

    dangerous_commands: frozenset
    web_search: WebSearchConfig
    web_fetch: WebFetchConfig


@dataclass(frozen=True)
class CacheConfig:
    """Cache testing configuration."""

    message_overhead: int


@dataclass(frozen=True)
class Config:
    """Root configuration object."""

    models: ModelsConfig
    docker: DockerConfig
    ui: UIConfig
    agent: AgentConfig
    tools: ToolsConfig
    cache: CacheConfig


# Color name to colorama mapping
_COLOR_MAP = {
    "BLACK": Fore.BLACK,
    "RED": Fore.RED,
    "GREEN": Fore.GREEN,
    "YELLOW": Fore.YELLOW,
    "BLUE": Fore.BLUE,
    "MAGENTA": Fore.MAGENTA,
    "CYAN": Fore.CYAN,
    "WHITE": Fore.WHITE,
    "RESET": Style.RESET_ALL,
}


def _load_config() -> Config:
    """Load configuration from YAML file bundled with the package."""
    config_path = Path(__file__).parent / "config.yaml"

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # Build nested config objects
    models = ModelsConfig(
        aliases=data["models"]["aliases"],
        providers=data["models"]["providers"],
        default=data["models"]["default"],
        limits={
            model: ModelLimits(**limits)
            for model, limits in data["models"]["limits"].items()
        },
        thinking_budget=data["models"]["thinking_budget"],
        pricing={
            model: ModelPricing(**pricing)
            for model, pricing in data["models"]["pricing"].items()
        },
        cache_requirements=data["models"]["cache_requirements"],
    )

    tools = ToolsConfig(
        dangerous_commands=frozenset(data["tools"]["dangerous_commands"]),
        web_search=WebSearchConfig(**data["tools"]["web_search"]),
        web_fetch=WebFetchConfig(**data["tools"]["web_fetch"]),
    )

    ui = UIConfig(
        divider_width=data["ui"]["divider_width"],
        show_compact_content=data["ui"]["show_compact_content"],
        show_drop_indicator=data["ui"]["show_drop_indicator"],
        prefixes=PrefixesConfig(**data["ui"]["prefixes"]),
        colors=ColorsConfig(**data["ui"]["colors"]),
    )

    return Config(
        models=models,
        docker=DockerConfig(**data["docker"]),
        ui=ui,
        agent=AgentConfig(**data["agent"]),
        tools=tools,
        cache=CacheConfig(**data["cache"]),
    )


# Module-level singleton - loaded once on first import
config = _load_config()


def get_color(role: str) -> str:
    """Get colorama color code for a role.

    Args:
        role: One of 'user', 'assistant', 'thinking', 'tool', 'error', 'warning', 'system', 'success'

    Returns:
        Colorama color code (e.g., Fore.GREEN)
    """
    color_name = getattr(config.ui.colors, role, "RESET")
    return _COLOR_MAP.get(color_name.upper(), Style.RESET_ALL)
