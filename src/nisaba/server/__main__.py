"""Entry point for nisaba MCP server."""

import sys
import logging
from pathlib import Path

from nisaba.server.config import NisabaConfig
from nisaba.server.factory import NisabaMCPFactory

logger = logging.getLogger(__name__)


def main():
    """Run nisaba MCP server."""
    # Parse basic args (--dev-mode, --augments-dir, etc.)
    # For now, use defaults
    config = NisabaConfig(dev_mode=False)

    # Create factory
    factory = NisabaMCPFactory(config)

    # Create and run server (STDIO by default)
    mcp_server = factory.create_mcp_server()

    logger.info("Starting nisaba MCP server on STDIO...")
    mcp_server.run()


if __name__ == "__main__":
    main()
