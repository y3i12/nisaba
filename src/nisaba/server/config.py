"""Configuration for nisaba MCP server."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from nisaba.config import MCPConfig, MCPContext


@dataclass
class NisabaConfig(MCPConfig):
    """
    Configuration for nisaba MCP server.

    Minimal config - just server settings and augments path.
    """

    # Override server name
    server_name: str = "nisaba"

    # Augments directory (default: cwd/.nisaba/augments)
    augments_dir: Optional[Path] = None

    # LEGACY: Composed augments file path (standalone mode only)
    # In unified mode, augments are session-specific via WorkspaceFiles
    # Default: cwd/.nisaba/tui/augment_view.md (for backward compatibility)
    composed_augments_file: Optional[Path] = None

    def __post_init__(self):
        """Set defaults for augments paths if not provided."""
        if self.augments_dir is None:
            self.augments_dir = Path.cwd() / ".nisaba" / "augments"