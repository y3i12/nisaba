"""
Unified server that runs both the local reverse-proxy and FastMCP HTTP in
the same process. Ports are passed in by the caller (auto-allocated by the
CLI wrapper, or pinned via --proxy-port / --mcp-port). Both components
share the same AugmentInjector / AugmentManager state in-memory.
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from nisaba.server.factory import create_nisaba_server
from nisaba.wrapper.injector import AugmentInjector
from nisaba.wrapper.reverse_proxy import ReverseProxyServer

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
    Unified server running both reverse-proxy and MCP in a single process.

    Architecture:
    - Single asyncio event loop
    - Reverse-proxy (Starlette + httpx) on `proxy_port`
    - FastMCP streamable HTTP on `mcp_port`
    - Shared AugmentInjector for zero-latency state
    """

    def __init__(
        self,
        augments_dir: Path,
        proxy_port: int,
        mcp_port: int,
        debug_proxy: bool = False,
    ):
        self.augments_dir = Path(augments_dir)
        self.proxy_port = proxy_port
        self.mcp_port = mcp_port
        self.debug_proxy = debug_proxy

        self.injector: Optional[AugmentInjector] = None
        self.proxy_server: Optional[ReverseProxyServer] = None
        self.mcp_server = None

        self.proxy_task: Optional[asyncio.Task] = None
        self.mcp_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        logger.info("=" * 60)
        logger.info("🚀 Starting Unified Nisaba Server")
        logger.info("=" * 60)

        await self._start_proxy()
        await self._start_mcp_server()

        logger.info("=" * 60)
        logger.info(f"✅ Unified server ready:")
        logger.info(f"   • Proxy:    http://localhost:{self.proxy_port}")
        logger.info(f"   • Upstream: {self.proxy_server.upstream_base}")
        logger.info(f"   • MCP HTTP: http://localhost:{self.mcp_port}")
        logger.info("=" * 60)

    async def _start_proxy(self) -> None:
        logger.info(f"🔌 Starting reverse proxy on port {self.proxy_port}...")

        self.injector = AugmentInjector()
        self.proxy_server = ReverseProxyServer(
            listen_port=self.proxy_port,
            injector=self.injector,
        )
        self.proxy_task = await self.proxy_server.start()

        # Give uvicorn a moment to bind before we launch claude
        await asyncio.sleep(0.5)

        logger.info(
            f"✓ Reverse proxy on {self.proxy_port} → {self.proxy_server.upstream_base}"
        )

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

        if self.proxy_server:
            logger.info("Stopping reverse proxy...")
            await self.proxy_server.stop()
        if self.proxy_task and not self.proxy_task.done():
            self.proxy_task.cancel()
            try:
                await self.proxy_task
            except asyncio.CancelledError:
                pass

        logger.info("✓ Unified server stopped")
        logger.info("=" * 60)

    async def run_until_stopped(self) -> None:
        await self.start()

        try:
            await asyncio.gather(
                self.proxy_task,
                self.mcp_task,
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            logger.info("Server cancelled")
        finally:
            await self.stop()
