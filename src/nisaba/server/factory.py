"""Nisaba MCP server factory."""

from contextlib import asynccontextmanager
from typing import AsyncIterator, Iterator
from pathlib import Path
import logging

from mcp.server.fastmcp import FastMCP
from nisaba import MCPFactory
from nisaba.server.config import NisabaConfig
from nisaba.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)


class NisabaMCPFactory(MCPFactory):
    """Factory for nisaba MCP server - augments management only."""

    def __init__(self, config: NisabaConfig):
        """Initialize nisaba factory."""
        super().__init__(config)

        # Tool instances cache
        self._tool_instances = None

    def _get_tool_base_class(self) -> type:
        """Return NisabaTool as base class."""
        return BaseTool

    def _get_module_prefix(self) -> str:
        """Return nisaba tools module prefix."""
        return "nisaba.tools"

    def _iter_tools(self) -> Iterator[BaseTool]:
        """
        Iterate over enabled tool instances.

        Lazily instantiates tools on first call.
        """
        if self._tool_instances is None:
            self._instantiate_tools()

        return iter(self._tool_instances)

    def _instantiate_tools(self):
        """Create tool instances for enabled tools."""
        enabled_tool_names = self._filter_enabled_tools()

        self._tool_instances = []

        for tool_name in enabled_tool_names:
            try:
                tool_class = self.registry.get_tool_class(tool_name)
                tool_instance = tool_class(factory=self)
                self._tool_instances.append(tool_instance)
            except Exception as e:
                logger.error(f"Failed to instantiate tool {tool_name}: {e}")

        logger.info(f"Instantiated {len(self._tool_instances)} tools: {enabled_tool_names}")

    def _get_initial_instructions(self) -> str:
        try:
            # Load template using nisaba's engine
            # instructions_path = Path(__file__).parent / "resources" / "instructions_template.md"
            # engine = self._load_template_engine(
            #     template_path=instructions_path,
            #     runtime_context={'dev_mode': self.config.dev_mode}
            # )

            # # Generate dynamic sections
            # logger.info("Generating MCP instructions...")

            # # Render with placeholders and clear unused ones
            # instructions = engine.render_and_clear()

            # logger.info(f"Generated instructions ({len(instructions)} chars)")
            # return instructions
            return ""

        except Exception as e:
            logger.error(f"Failed to generate instructions: {e}", exc_info=True)
            return ""

    @asynccontextmanager
    async def server_lifespan(self, mcp_server: FastMCP) -> AsyncIterator[None]:
        """Manage nisaba server lifecycle."""
        logger.info("=" * 60)
        logger.info("Nisaba MCP Server - Lifecycle Starting")
        logger.info("=" * 60)

        # Register tools
        self._register_tools(mcp_server)

        # Start HTTP transport if enabled
        await self._start_http_transport_if_enabled()

        logger.info("Nisaba MCP Server - Ready")
        logger.info("=" * 60)

        yield  # Server runs here

        # SHUTDOWN
        logger.info("=" * 60)
        logger.info("Nisaba MCP Server - Lifecycle Shutdown")
        logger.info("=" * 60)

        # Stop HTTP transport
        await self._stop_http_transport()

        logger.info("Nisaba MCP Server - Shutdown Complete")
        logger.info("=" * 60)
