"""NTN - Minimal AI coding agent"""

try:
    from ._version import __version__
except ImportError:
    __version__ = "0.1.0"  # Fallback for editable installs

__author__ = "NTN"

from .agent import CodingAgent, ContainerManager, print_divider
from .tools import TerminalTool, WebSearchTool, FetchWebTool, DockerSandboxTool

__all__ = [
    "CodingAgent",
    "ContainerManager", 
    "print_divider",
    "TerminalTool",
    "WebSearchTool",
    "FetchWebTool",
    "DockerSandboxTool",
]
