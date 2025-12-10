"""Generic MCP server framework."""

from nisaba.agent import Agent
from nisaba.factory import MCPFactory
from nisaba.registry import ToolRegistry, RegisteredTool
from nisaba.config import MCPConfig, MCPContext
from nisaba.markers import ToolMarker, ToolMarkerOptional, ToolMarkerDevOnly, ToolMarkerMutating
from nisaba.schema_utils import sanitize_for_openai_tools
from nisaba.cli_utils import (
    AutoRegisteringGroup,
    OutputFormat,
    ToolsCommandGroup,
    ContextCommandGroup,
    PromptCommandGroup,
    format_tool_list,
    format_context_list,
    validate_file_or_exit,
    validate_dir_or_exit,
)
__version__ = "0.1.0"

__all__ = [
    "Agent",
    "MCPFactory",
    "ToolRegistry",
    "RegisteredTool",
    "MCPConfig",
    "MCPContext",
    "ToolMarker",
    "ToolMarkerOptional",
    "ToolMarkerDevOnly",
    "ToolMarkerMutating",
    "sanitize_for_openai_tools",
    "AutoRegisteringGroup",
    "OutputFormat",
    "ToolsCommandGroup",
    "ContextCommandGroup",
    "PromptCommandGroup",
    "format_tool_list",
    "format_context_list",
    "validate_file_or_exit",
    "validate_dir_or_exit"
]
