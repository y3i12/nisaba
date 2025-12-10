"""Tool registry with auto-discovery."""

from dataclasses import dataclass
from typing import Dict, List, Set, Type
import logging

logger = logging.getLogger(__name__)


@dataclass
class RegisteredTool:
    """Metadata for a registered tool."""
    tool_class: Type
    tool_name: str
    is_optional: bool
    is_dev_only: bool


class ToolRegistry:
    """
    Registry for MCP tools with auto-discovery.

    Automatically discovers all subclasses of the specified tool base class
    and provides methods for filtering and accessing them.
    """

    def __init__(self, tool_base_class: Type, module_prefix: str):
        """
        Initialize registry with auto-discovery.

        Args:
            tool_base_class: Base class for tool discovery
            module_prefix: Module prefix for tool filtering (e.g., "nabu.mcp.tools")
        """
        self.tool_base_class = tool_base_class
        self.module_prefix = module_prefix
        self._tools: Dict[str, RegisteredTool] = {}
        self._discover_tools()

    def _discover_tools(self):
        """Discover all tool subclasses recursively."""
        # Get all subclasses recursively
        def iter_subclasses(cls):
            for subclass in cls.__subclasses__():
                yield subclass
                yield from iter_subclasses(subclass)

        for tool_class in iter_subclasses(self.tool_base_class):
            # Skip the base class itself
            if tool_class == self.tool_base_class:
                continue

            # Only include tools from specified module prefix
            if not tool_class.__module__.startswith(self.module_prefix):
                logger.debug(f"Skipping tool outside module prefix: {tool_class.__module__}")
                continue

            # Get tool name
            tool_name = tool_class.get_name_from_cls()

            if tool_name in self._tools:
                logger.warning(f"Duplicate tool name: {tool_name}")
                continue

            # Register tool
            self._tools[tool_name] = RegisteredTool(
                tool_class=tool_class,
                tool_name=tool_name,
                is_optional=tool_class.is_optional(),
                is_dev_only=tool_class.is_dev_only()
            )

        logger.debug(f"Discovered {len(self._tools)} tools: {list(self._tools.keys())}")

    def get_tool_class(self, tool_name: str) -> Type:
        """
        Get tool class by name.

        Args:
            tool_name: Name of the tool

        Returns:
            Tool class

        Raises:
            ValueError: If tool name not found
        """
        if tool_name not in self._tools:
            raise ValueError(f"Unknown tool: {tool_name}")
        return self._tools[tool_name].tool_class

    def get_all_tool_names(self) -> List[str]:
        """Get all registered tool names."""
        return list(self._tools.keys())

    def get_default_enabled_tool_names(self) -> List[str]:
        """Get names of tools enabled by default (not optional)."""
        return [
            name for name, tool in self._tools.items()
            if not tool.is_optional
        ]

    def get_optional_tool_names(self) -> List[str]:
        """Get names of optional tools."""
        return [
            name for name, tool in self._tools.items()
            if tool.is_optional
        ]

    def get_dev_only_tool_names(self) -> List[str]:
        """Get names of development-only tools."""
        return [
            name for name, tool in self._tools.items()
            if tool.is_dev_only
        ]

    def filter_tools_by_context(
        self,
        enabled_tools: Set[str],
        disabled_tools: Set[str],
        dev_mode: bool = False
    ) -> List[str]:
        """
        Filter tools based on context configuration.

        Logic:
        - If enabled_tools is non-empty, use it as exclusive list (override defaults)
        - If enabled_tools is empty, use all default-enabled tools
        - Dev-only tools are included if: (dev_mode=True) OR (explicitly in enabled_tools)
        - disabled_tools always takes precedence and removes tools from final list

        Args:
            enabled_tools: Explicitly enabled tools (if non-empty, overrides defaults)
            disabled_tools: Explicitly disabled tools
            dev_mode: Whether to auto-include all dev-only tools

        Returns:
            List of enabled tool names (sorted)
        """
        result = set()

        # Determine base tool set
        if enabled_tools:
            # If explicit enabled list provided, use it exclusively (override defaults)
            result.update(enabled_tools)
        else:
            # Otherwise, use all default-enabled tools
            result.update(self.get_default_enabled_tool_names())

        # Handle dev-only tools
        dev_tools = set(self.get_dev_only_tool_names())
        
        if dev_mode:
            # In dev mode, include all dev-only tools
            result.update(dev_tools)
        else:
            # Not in dev mode: remove dev tools EXCEPT those explicitly requested
            result -= (dev_tools - enabled_tools)

        # Remove explicitly disabled tools (disabled_tools takes precedence)
        result -= disabled_tools

        return sorted(result)

    def is_valid_tool_name(self, tool_name: str) -> bool:
        """Check if tool name is registered."""
        return tool_name in self._tools

    def _iter_tool_classes(self):
        """Iterate over registered tool classes (for testing)."""
        for registered_tool in self._tools.values():
            yield registered_tool.tool_class
