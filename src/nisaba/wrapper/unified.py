"""
Unified server that runs both the augment relay and FastMCP HTTP in the same process.

Ports are passed in by the caller (auto-allocated by the CLI wrapper, or
pinned via --proxy-port / --mcp-port). Both components share the same
AugmentManager instance in-memory via a single asyncio event loop (same thread),
which means session_context.set_current_session() in the relay is immediately
visible to MCP tool handlers.
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from nisaba.server.factory import create_nisaba_server
from nisaba.wrapper.relay import AugmentRelay

logger = logging.getLogger(__name__)


class SuppressCancelledErrorFilter(logging.Filter):
    """Filter to suppress CancelledError tracebacks from uvicorn/starlette during shutdown."""

    def filter(self, record):
        if record.levelno == logging.ERROR:
            if 'asyncio.exceptions.CancelledError' in record.getMessage():
                return False
            if record.exc_info and record.exc_info[0] is asyncio.CancelledError:
                return False
        return True


logging.getLogger('uvicorn.error').addFilter(SuppressCancelledErrorFilter())
logging.getLogger('starlette').addFilter(SuppressCancelledErrorFilter())


class UnifiedNisabaServer:
    """
    Unified server running both the augment relay and MCP in a single process.

    Architecture:
    - Single asyncio event loop (single OS thread)
    - AugmentRelay HTTP server: receives API calls from claude via ANTHROPIC_BASE_URL,
      injects augments, forwards to api.anthropic.com
    - FastMCP HTTP server: exposes augment management tools to claude
    - Shared AugmentManager for zero-latency state sharing
    """

    def __init__(
        self,
        augments_dir: Path,
        proxy_port: int,
        mcp_port: int,
        debug_proxy: bool = False,
        upstream_url: str = "https://api.anthropic.com",
    ):
        self.augments_dir = Path(augments_dir)
        self.relay_port = proxy_port
        self.mcp_port = mcp_port
        self.debug_proxy = debug_proxy
        self.upstream_url = upstream_url

        self.relay: Optional[AugmentRelay] = None
        self.mcp_server = None

        self.relay_task: Optional[asyncio.Task] = None
        self.mcp_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        logger.info("=" * 60)
        logger.info("🚀 Starting Unified Nisaba Server")
        logger.info("=" * 60)

        await self._start_relay()
        await self._start_mcp_server()

        logger.info("=" * 60)
        logger.info(f"✅ Unified server ready:")
        logger.info(f"   • Relay: http://127.0.0.1:{self.relay_port}")
        logger.info(f"   • MCP HTTP: http://localhost:{self.mcp_port}")
        logger.info("=" * 60)

    async def _start_relay(self) -> None:
        logger.info(f"🔌 Starting relay on port {self.relay_port}...")

        self.relay = AugmentRelay(upstream_url=self.upstream_url)
        self.relay_task = asyncio.create_task(self.relay.start(self.relay_port))

        await asyncio.sleep(0.5)

        logger.info(f"✓ Relay running on port {self.relay_port}")

    async def _start_mcp_server(self) -> None:
        logger.info(f"🤖 Starting MCP server on port {self.mcp_port}...")

        self.mcp_server = create_nisaba_server(
            host="localhost",
            port=self.mcp_port,
        )

        self.mcp_task = asyncio.create_task(self._run_mcp_server())

        await asyncio.sleep(1)

        logger.info(f"✓ MCP server running on port {self.mcp_port}")

    async def _run_mcp_server(self) -> None:
        try:
            await self.mcp_server.run_streamable_http_async()
        except asyncio.CancelledError:
            logger.debug("MCP server task cancelled (clean shutdown)")
            return
        except Exception as e:
            logger.error(f"MCP server error: {e}", exc_info=True)
            raise

    async def stop(self) -> None:
        logger.info("=" * 60)
        logger.info("🛑 Stopping Unified Nisaba Server")
        logger.info("=" * 60)

        if self.mcp_task and not self.mcp_task.done():
            logger.info("Stopping MCP server...")
            self.mcp_task.cancel()
            try:
                await self.mcp_task
            except asyncio.CancelledError:
                pass

        if self.relay_task and not self.relay_task.done():
            logger.info("Stopping relay...")
            self.relay_task.cancel()
            try:
                await self.relay_task
            except asyncio.CancelledError:
                pass

        logger.info("✓ Unified server stopped")
        logger.info("=" * 60)

    async def run_until_stopped(self) -> None:
        await self.start()

        try:
            await asyncio.gather(
                self.relay_task,
                self.mcp_task,
                return_exceptions=True
            )
        except asyncio.CancelledError:
            logger.info("Server cancelled")
        finally:
            await self.stop()
