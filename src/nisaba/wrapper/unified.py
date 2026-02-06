"""
Unified server that runs both mitmproxy and FastMCP HTTP in same process.

This module provides the unified architecture where:
- mitmproxy runs as proxy on port 1337
- FastMCP HTTP runs as MCP server on port 9973
- Both share the same AugmentManager instance (in-memory)
- Checkpoint-based context compression via shared state
"""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

from mitmproxy import options
from mitmproxy.tools import dump

from nisaba.server.config import NisabaConfig
from nisaba.server.factory import NisabaMCPFactory
from nisaba.wrapper.proxy import AugmentInjector

logger = logging.getLogger(__name__)


class SuppressCancelledErrorFilter(logging.Filter):
    """Filter to suppress CancelledError tracebacks from uvicorn/starlette during shutdown."""

    def filter(self, record):
        # Suppress ERROR logs that contain CancelledError traceback
        if record.levelno == logging.ERROR:
            if 'asyncio.exceptions.CancelledError' in record.getMessage():
                return False
            # Also check exc_info if present
            if record.exc_info and record.exc_info[0] is asyncio.CancelledError:
                return False
        return True


# Install the filter on uvicorn and starlette loggers
logging.getLogger('uvicorn.error').addFilter(SuppressCancelledErrorFilter())
logging.getLogger('starlette').addFilter(SuppressCancelledErrorFilter())


class UnifiedNisabaServer:
    """
    Unified server running both proxy and MCP in single process.

    Architecture:
    - Single asyncio event loop
    - mitmproxy Master with shared event loop
    - FastMCP HTTP server on asyncio
    - Shared AugmentManager for zero-latency state sharing
    """

    def __init__(
        self,
        augments_dir: Path,
        proxy_port: int = 1337,
        mcp_port: int = 9973,
        debug_proxy: bool = False
    ):
        """
        Initialize unified server.

        Args:
            augments_dir: Directory containing augment files
            proxy_port: Port for mitmproxy (default: 1337)
            mcp_port: Port for MCP HTTP server (default: 9973)
            debug_proxy: Show proxy debug output
        """
        self.augments_dir = Path(augments_dir)
        self.proxy_port = proxy_port
        self.mcp_port = mcp_port
        self.debug_proxy = debug_proxy

        # Shared state (single source of truth)
        self.augment_manager: Optional[AugmentManager] = None

        # Component references
        self.proxy_master: Optional[dump.DumpMaster] = None
        self.mcp_factory: Optional[NisabaMCPFactory] = None
        self.mcp_server = None

        # Task references for cleanup
        self.proxy_task: Optional[asyncio.Task] = None
        self.mcp_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """
        Start both proxy and MCP server.

        Initializes shared state and starts both components concurrently.
        """
        logger.info("=" * 60)
        logger.info("🚀 Starting Unified Nisaba Server")
        logger.info("=" * 60)

        # Start both components
        await self._start_proxy()
        await self._start_mcp_server()

        logger.info("=" * 60)
        logger.info(f"✅ Unified server ready:")
        logger.info(f"   • Proxy: http://localhost:{self.proxy_port}")
        logger.info(f"   • MCP HTTP: http://localhost:{self.mcp_port}")
        logger.info("=" * 60)

    async def _start_proxy(self) -> None:
        """Start mitmproxy with shared AugmentManager."""
        logger.info(f"🔌 Starting proxy on port {self.proxy_port}...")

        # Create mitmproxy options
        # allow_hosts: only intercept Anthropic API traffic, pass through
        # everything else (auto-updater, etc.) without TLS interception
        proxy_opts = options.Options(
            listen_port=self.proxy_port,
            mode=["regular"],  # Must be list, not string
            allow_hosts=[r"api\.anthropic\.com"],
        )

        # Create proxy addon with shared AugmentManager
        proxy_addon = AugmentInjector(augment_manager=self.augment_manager)

        # Create DumpMaster with our event loop
        self.proxy_master = dump.DumpMaster(
            options=proxy_opts,
            loop=asyncio.get_event_loop(),
            with_termlog=self.debug_proxy,
            with_dumper=False  # No dumping, just proxying
        )

        # Add our addon
        self.proxy_master.addons.add(proxy_addon)

        # Start proxy as background task
        self.proxy_task = asyncio.create_task(self._run_proxy())

        # Give it a moment to start
        await asyncio.sleep(0.5)

        logger.info(f"✓ Proxy running on port {self.proxy_port}")

    async def _run_proxy(self) -> None:
        """Run proxy (async task)."""
        try:
            await self.proxy_master.run()
        except asyncio.CancelledError:
            # Expected during shutdown - don't re-raise to avoid error logs
            logger.debug("Proxy task cancelled (clean shutdown)")
            return
        except Exception as e:
            logger.error(f"Proxy error: {e}", exc_info=True)
            raise

    async def _start_mcp_server(self) -> None:
        """Start FastMCP HTTP server with shared AugmentManager."""
        logger.info(f"🤖 Starting MCP server on port {self.mcp_port}...")

        # Create config with HTTP transport DISABLED
        # (We manually manage HTTP server in unified mode)
        config = NisabaConfig(
            dev_mode=False,
            enable_http_transport=False,  # Don't auto-start, we manage it
            http_host="localhost",
            http_port=self.mcp_port,
            augments_dir=self.augments_dir
        )

        # Create factory with shared AugmentManager
        self.mcp_factory = NisabaMCPFactory(config)

        # Create MCP server (HTTP transport)
        self.mcp_server = self.mcp_factory.create_mcp_server(
            host=config.http_host,
            port=config.http_port
        )

        # Start MCP server as background task
        self.mcp_task = asyncio.create_task(self._run_mcp_server())

        # Give it a moment to start
        await asyncio.sleep(1)

        # Self-register for discovery (temporarily enable flag to bypass check)
        original_flag = self.mcp_factory.config.enable_http_transport
        try:
            self.mcp_factory.config.enable_http_transport = True
            self.mcp_factory._register_to_discovery()
        finally:
            self.mcp_factory.config.enable_http_transport = original_flag

        logger.info(f"✓ MCP server running on port {self.mcp_port}")

    async def _run_mcp_server(self) -> None:
        """Run MCP server (async task)."""
        try:
            # Run as HTTP transport (SSE)
            await self.mcp_server.run_streamable_http_async()
        except asyncio.CancelledError:
            # Expected during shutdown - don't re-raise to avoid error logs
            logger.debug("MCP server task cancelled (clean shutdown)")
            return
        except Exception as e:
            logger.error(f"MCP server error: {e}", exc_info=True)
            raise

    async def stop(self) -> None:
        """
        Stop both servers gracefully.

        Cancels tasks and performs cleanup.
        """
        logger.info("=" * 60)
        logger.info("🛑 Stopping Unified Nisaba Server")
        logger.info("=" * 60)

        # Cancel MCP server task
        if self.mcp_task and not self.mcp_task.done():
            logger.info("Stopping MCP server...")
            self.mcp_task.cancel()
            try:
                await self.mcp_task
            except asyncio.CancelledError:
                pass

        # Cancel proxy task
        if self.proxy_task and not self.proxy_task.done():
            logger.info("Stopping proxy...")
            self.proxy_task.cancel()
            try:
                await self.proxy_task
            except asyncio.CancelledError:
                pass

        # Shutdown proxy master
        if self.proxy_master:
            self.proxy_master.shutdown()

        logger.info("✓ Unified server stopped")
        logger.info("=" * 60)

    async def run_until_stopped(self) -> None:
        """
        Run until externally stopped.

        Waits for both tasks to complete (or be cancelled).
        """
        await self.start()

        try:
            # Wait for both tasks (they run indefinitely until cancelled)
            await asyncio.gather(
                self.proxy_task,
                self.mcp_task,
                return_exceptions=True
            )
        except asyncio.CancelledError:
            logger.info("Server cancelled")
        finally:
            await self.stop()


async def run_unified_server(
    augments_dir: Path,
    proxy_port: int = 1337,
    mcp_port: int = 9973,
    debug_proxy: bool = False,
    timeout: Optional[float] = None
) -> None:
    """
    Run unified server for a specific duration or until interrupted.

    Args:
        augments_dir: Directory containing augment files
        proxy_port: Port for proxy (default: 1337)
        mcp_port: Port for MCP server (default: 9973)
        debug_proxy: Show proxy debug output
        timeout: Optional timeout in seconds (for testing)
    """
    server = UnifiedNisabaServer(
        augments_dir=augments_dir,
        proxy_port=proxy_port,
        mcp_port=mcp_port,
        debug_proxy=debug_proxy
    )

    try:
        if timeout:
            # Run with timeout (for testing)
            await asyncio.wait_for(server.run_until_stopped(), timeout=timeout)
        else:
            # Run indefinitely
            await server.run_until_stopped()
    except asyncio.TimeoutError:
        logger.info(f"Server timeout after {timeout}s")
    except KeyboardInterrupt:
        logger.info("Server interrupted by user")
    finally:
        await server.stop()
