"""Abstract factory for MCP server creation."""

from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import AsyncIterator, Iterator, Any, Optional, Callable, TYPE_CHECKING, Dict, List
from pathlib import Path
import logging
import inspect
import asyncio

from mcp.server.fastmcp import FastMCP
from typing_extensions import Annotated
from pydantic import Field

from nisaba.config import MCPConfig
from nisaba.registry import ToolRegistry
from nisaba.tools.base_tool import BaseTool

if TYPE_CHECKING:
    from nisaba.templates import InstructionsTemplateEngine

logger = logging.getLogger(__name__)


class MCPFactory(ABC):
    """
    Abstract factory for creating MCP servers.

    Subclasses must implement:
    - _get_tool_base_class(): Return tool base class for discovery
    - _get_module_prefix(): Return module prefix for tool filtering
    - _iter_tools(): Return iterator of tool instances
    - _get_initial_instructions(): Return server instructions
    - server_lifespan(): Manage server lifecycle
    """

    def __init__(self, config: MCPConfig):
        """
        Initialize factory with configuration.

        Args:
            config: MCPConfig instance
        """
        self.config = config

        # Initialize registry with subclass-specific tool base
        tool_base_class = self._get_tool_base_class()
        module_prefix = self._get_module_prefix()
        self.registry = ToolRegistry(
            tool_base_class=tool_base_class,
            module_prefix=module_prefix
        )

        # Setup logging
        log_level = getattr(logging, config.log_level.upper(), logging.INFO)
        logging.basicConfig(level=log_level)

        if config.dev_mode:
            logger.setLevel(logging.DEBUG)
            logger.info("=' Running in DEVELOPMENT MODE")

        # Dual transport infrastructure
        self._tool_locks: Dict[str, asyncio.Lock] = {}
        self._http_server_task: Optional[asyncio.Task] = None
        self._tool_instances_cache: Optional[List[BaseTool]] = None

        # MCP server discovery registry
        self._server_id: Optional[str] = None

    @abstractmethod
    def _get_tool_base_class(self) -> type:
        """
        Get the tool base class for registry discovery.

        Returns:
            Tool base class (e.g., NabuTool, SerenaTool)
        """
        pass

    @abstractmethod
    def _get_module_prefix(self) -> str:
        """
        Get the module prefix for tool filtering.

        Returns:
            Module prefix string (e.g., "nabu.mcp.tools")
        """
        pass

    @abstractmethod
    def _iter_tools(self) -> Iterator[BaseTool]:
        """
        Iterate over enabled tool instances.

        Returns:
            Iterator of tool instances
        """
        pass

    def _get_tool_lock(self, tool_name: str) -> asyncio.Lock:
        """
        Get or create lock for tool.

        Ensures thread-safe tool execution across transports.

        Args:
            tool_name: Tool name to get lock for

        Returns:
            asyncio.Lock for the tool
        """
        if tool_name not in self._tool_locks:
            self._tool_locks[tool_name] = asyncio.Lock()
        return self._tool_locks[tool_name]

    def _filter_enabled_tools(self) -> list[str]:
        """
        Get list of enabled tool names based on context.

        Returns:
            List of enabled tool names
        """
        return self.registry.filter_tools_by_context(
            enabled_tools=self.config.context.enabled_tools,
            disabled_tools=self.config.context.disabled_tools,
            dev_mode=self.config.dev_mode
        )

    def _load_template_engine(
        self,
        template_path: Optional[Path] = None,
        runtime_context: Optional[dict] = None
    ) -> "InstructionsTemplateEngine":
        """
        Load template engine for instructions.

        Args:
            template_path: Path to template file (relative to subclass)
            runtime_context: Runtime context for conditional includes (e.g., {'dev_mode': True})

        Returns:
            Configured template engine
        """
        from nisaba.templates import InstructionsTemplateEngine
        return InstructionsTemplateEngine(
            template_path=template_path,
            runtime_context=runtime_context
        )

    @abstractmethod
    def _get_initial_instructions(self) -> str:
        """
        Get initial instructions for AI assistants.

        These instructions explain what the MCP server does
        and how to use it effectively.

        Returns:
            Markdown-formatted instructions string
        """
        pass

    def create_mcp_server(
        self,
        host: str = "0.0.0.0",
        port: int = 8000
    ) -> FastMCP:
        """
        Create an MCP server instance.

        Args:
            host: Host to bind to
            port: Port to bind to

        Returns:
            Configured FastMCP instance
        """
        instructions = self._get_initial_instructions()

        mcp = FastMCP(
            name=self.config.server_name,
            lifespan=self.server_lifespan,
            host=host,
            port=port,
            instructions=instructions
        )

        return mcp

    def _create_typed_wrapper(self, tool_instance: BaseTool, tool_schema: dict) -> Any:
        """
        Create a typed wrapper function for a tool with async locking.

        This enables FastMCP to introspect the function signature
        and generate proper parameter schemas for LLM clients.

        Wraps execution with tool-level lock for safe concurrent access.

        Args:
            tool_instance: The tool instance to wrap
            tool_schema: The tool schema from get_tool_schema()

        Returns:
            Async function with proper type annotations and locking
        """
        # Extract parameter information from schema
        params_schema = tool_schema.get("parameters", {})
        properties = params_schema.get("properties", {})
        required = set(params_schema.get("required", []))

        # Build function parameters dynamically
        param_defs = []
        param_annotations = {}

        for param_name, param_info in properties.items():
            json_type = param_info.get("type", "string")
            param_desc = param_info.get("description", "")
            default_value = param_info.get("default", inspect.Parameter.empty)

            # Map JSON types to Python types
            type_mapping = {
                "string": str,
                "integer": int,
                "number": float,
                "boolean": bool,
                "array": list,
                "object": dict
            }
            python_type = type_mapping.get(json_type, str)

            # Build parameter with annotation
            if param_desc:
                param_annotations[param_name] = Annotated[
                    python_type,
                    Field(description=param_desc)
                ]
            else:
                param_annotations[param_name] = python_type

            # Build parameter definition string
            if param_name in required:
                param_defs.append(f"{param_name}")
            else:
                if default_value == inspect.Parameter.empty:
                    param_defs.append(f"{param_name}=None")
                else:
                    param_defs.append(f"{param_name}={repr(default_value)}")

        # Create the wrapper function using exec
        param_list = ", ".join(param_defs)
        param_names = list(properties.keys())

        # Build kwargs dict from parameters
        kwargs_build = "{" + ", ".join([f"'{p}': {p}" for p in param_names]) + "}"

        # Get tool name for lock
        tool_name = tool_instance.get_name()

        # this is the entry point for mcp execute() method in nisaba
        func_code = f"""
async def typed_wrapper({param_list}):
    from dataclasses import asdict
    
    kwargs = {kwargs_build}
    tool_lock = factory._get_tool_lock("{tool_name}")
    
    async with tool_lock:
        response = await tool_instance.execute_tool(**kwargs)
        return asdict(response) if not isinstance(response, dict) else response
"""

        # Execute to create the function
        local_namespace = {
            "tool_instance": tool_instance,
            "factory": self,
        }
        exec(func_code, local_namespace)
        wrapper = local_namespace["typed_wrapper"]

        # Set annotations manually
        wrapper.__annotations__ = param_annotations

        return wrapper

    def _register_tools(self, mcp: FastMCP):
        """
        Register tools with MCP server.

        Conditionally applies OpenAI schema sanitization based on context configuration.
        Caches tool instances for reuse across transports.

        Args:
            mcp: FastMCP server instance
        """
        tool_count = 0

        # Cache tool instances on first call for reuse across transports
        if self._tool_instances_cache is None:
            self._tool_instances_cache = list(self._iter_tools())

        tool_instances = self._tool_instances_cache

        for tool in tool_instances:
            tool_name = tool.get_name()

            # Get tool schema for description and metadata
            tool_schema = tool.get_tool_schema()

            # Apply OpenAI sanitization if context requires it
            if self.config.context.openai_compatible:
                from nisaba.schema_utils import sanitize_for_openai_tools
                tool_schema = sanitize_for_openai_tools(tool_schema)

            tool_description = tool_schema.get("description", "")

            # Create a typed wrapper function
            typed_wrapper = self._create_typed_wrapper(tool, tool_schema)

            # Register with MCP using official API
            mcp.tool(name=tool_name, description=tool_description)(typed_wrapper)

            tool_count += 1

        logger.info(f"Registered {tool_count} tools with MCP server")

    async def _run_http_server(self):
        """
        Run HTTP transport server in background.

        Creates separate FastMCP instance sharing same tool instances.
        Logs to stderr to avoid STDIO pollution.
        """
        import sys

        # Redirect HTTP logs to stderr to avoid STDIO pollution
        if self.config.http_log_to_stderr:
            http_handler = logging.StreamHandler(sys.stderr)
            http_handler.setFormatter(logging.Formatter(
                '%(asctime)s [HTTP] %(levelname)s: %(message)s'
            ))
            logging.getLogger().addHandler(http_handler)

        logger.info(
            f"Starting HTTP transport on {self.config.http_host}:{self.config.http_port}"
        )

        # Create HTTP FastMCP instance
        mcp_http = FastMCP(
            name=f"{self.config.server_name}_http",
            lifespan=self._http_lifespan,
            instructions=self._get_initial_instructions(),
            host=self.config.http_host,
            port=self.config.http_port
        )

        # Register same tool instances (share locks via factory)
        self._register_tools(mcp_http)

        logger.info("HTTP transport ready")

        # Run as streamable HTTP (blocks until cancelled)
        await mcp_http.run_streamable_http_async()

    @asynccontextmanager
    async def _http_lifespan(self, mcp_server: FastMCP):
        """
        Minimal lifespan for HTTP server.

        Tools already initialized by STDIO server, just yield.
        """
        logger.info("HTTP server lifespan starting")
        yield
        logger.info("HTTP server lifespan ending")

    async def _start_http_transport_if_enabled(self):
        """
        Start HTTP transport as background task if enabled.

        Called by subclass server_lifespan() implementations.
        """
        if not self.config.enable_http_transport:
            return

        logger.info("Dual transport enabled - starting HTTP server")
        self._http_server_task = asyncio.create_task(self._run_http_server())

        # Wait briefly for server to start, then register
        await asyncio.sleep(0.5)  # Give HTTP server time to bind
        self._register_to_discovery()

    async def _stop_http_transport(self):
        """
        Stop HTTP transport gracefully.

        Called by subclass server_lifespan() implementations.
        """
        if self._http_server_task is None:
            return

        # Unregister from discovery before stopping
        self._unregister_from_discovery()

        logger.info("Stopping HTTP transport")
        self._http_server_task.cancel()

        try:
            await self._http_server_task
        except asyncio.CancelledError:
            pass

        logger.info("HTTP transport stopped")

    def _get_registry_path(self) -> Path:
        """Get registry file path (.nisaba/mcp_servers.json in cwd)."""
        cwd = Path.cwd()
        return cwd / ".nisaba" / "mcp_servers.json"

    def _register_to_discovery(self) -> None:
        """Register this MCP server to discovery registry."""
        if not self.config.enable_http_transport:
            return

        try:
            from nisaba.mcp_registry import MCPServerRegistry
            import os
            from datetime import datetime

            registry_path = self._get_registry_path()
            registry = MCPServerRegistry(registry_path)

            pid = os.getpid()
            self._server_id = f"{self.config.server_name}_{pid}"

            server_info = {
                "name": self.config.server_name,
                "pid": pid,
                "stdio_active": True,
                "http": {
                    "enabled": True,
                    "host": self.config.http_host,
                    "port": self.config.http_port,
                    "url": f"http://localhost:{self.config.http_port}"
                },
                "started_at": datetime.utcnow().isoformat() + "Z",
                "cwd": str(Path.cwd())
            }

            registry.register_server(self._server_id, server_info)
            logger.info(f"Registered to discovery registry: {registry_path}")

        except Exception as e:
            logger.warning(f"Failed to register to discovery: {e}")

    def _unregister_from_discovery(self) -> None:
        """Unregister this MCP server from discovery registry."""
        if not self._server_id:
            return

        try:
            from nisaba.mcp_registry import MCPServerRegistry

            registry_path = self._get_registry_path()
            registry = MCPServerRegistry(registry_path)
            registry.unregister_server(self._server_id)
            logger.info("Unregistered from discovery registry")

        except Exception as e:
            logger.warning(f"Failed to unregister from discovery: {e}")

    @asynccontextmanager
    @abstractmethod
    async def server_lifespan(self, mcp_server: FastMCP) -> AsyncIterator[None]:
        """
        Manage server lifecycle.

        Must handle:
        - Resource initialization
        - Tool registration
        - Cleanup on shutdown

        Args:
            mcp_server: FastMCP server instance

        Yields:
            None during server runtime
        """
        yield
