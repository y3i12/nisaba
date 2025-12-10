"""
MCP server discovery registry.

Manages .nisaba/mcp_servers.json for server registration and discovery.
Thread-safe registry for tracking running MCP server instances.
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime

from nisaba.workspace_files import WorkspaceFiles
from nisaba import session_context

logger = logging.getLogger(__name__)


class MCPServerRegistry:
    """
    Thread-safe registry for MCP server instances.

    Manages .nisaba/mcp_servers.json in project root for server discovery.
    Automatically cleans up stale entries (dead processes).

    Uses WorkspaceFiles singleton for shared cache consistency across components.
    """

    REGISTRY_VERSION = "1.0"

    def __init__(self, registry_path: Optional[Path] = None):
        """
        Initialize registry.

        Note: MCP registry is GLOBAL (not session-specific) because it tracks
        running server processes across all sessions.

        Args:
            registry_path: Path to registry JSON file (legacy parameter).
        """
        # MCP server registry is global - use hardcoded path
        # (Servers are process-level, not session-level)
        from nisaba.structured_file import JsonStructuredFile
        global_registry_path = Path.cwd() / ".nisaba" / "mcp_servers.json"

        self._file = JsonStructuredFile(
            file_path=global_registry_path,
            name="mcp_servers",
            default_factory=lambda: {"version": self.REGISTRY_VERSION, "servers": {}}
        )
        self.registry_path = self._file.file_path

        # Validate registry_path if provided (backward compatibility)
        if registry_path is not None and Path(registry_path) != self.registry_path:
            logger.warning(
                f"Custom registry_path {registry_path} ignored, "
                f"using global path: {self.registry_path}"
            )

        logger.debug(f"MCPServerRegistry initialized (global): {self.registry_path}")

    def register_server(self, server_id: str, server_info: dict) -> None:
        """
        Register or update a server entry.

        Atomically updates registry file with new server information.
        Cleans up stale entries before writing.

        Args:
            server_id: Unique server identifier (e.g., "nabu_12345")
            server_info: Server metadata dict with keys:
                - name: Server name
                - pid: Process ID
                - stdio_active: Whether STDIO transport is active
                - http: HTTP transport info (enabled, host, port, url)
                - started_at: ISO timestamp
                - cwd: Working directory
        """
        try:
            def update_registry(data: dict) -> dict:
                """Modifier function for atomic update."""
                # Ensure structure
                if "servers" not in data:
                    data["servers"] = {}
                if "version" not in data:
                    data["version"] = self.REGISTRY_VERSION
                
                # Cleanup stale entries
                data["servers"] = self._cleanup_stale_entries(data["servers"])
                
                # Add/update this server
                data["servers"][server_id] = server_info
                
                return data
            
            # Atomic update using JsonStructuredFile
            self._file.atomic_update_json(update_registry)
            
            logger.info(f"Registered server: {server_id}")

        except Exception as e:
            logger.error(f"Failed to register server {server_id}: {e}", exc_info=True)
            raise

    def unregister_server(self, server_id: str) -> None:
        """
        Remove a server entry from registry.

        Uses atomic transaction to prevent races.

        Args:
            server_id: Server identifier to remove
        """
        try:
            if not self.registry_path.exists():
                logger.debug(f"Registry does not exist, nothing to unregister: {server_id}")
                return

            def remove_server(data: dict) -> dict:
                """Modifier function for atomic removal."""
                if "servers" in data and server_id in data["servers"]:
                    del data["servers"][server_id]
                    logger.info(f"Unregistered server: {server_id}")
                else:
                    logger.debug(f"Server not in registry: {server_id}")
                return data
            
            # Atomic update using JsonStructuredFile
            self._file.atomic_update_json(remove_server)

        except Exception as e:
            logger.error(f"Failed to unregister server {server_id}: {e}", exc_info=True)
            raise

    def list_servers(self) -> Dict[str, dict]:
        """
        List all active servers.

        Filters out entries for dead processes.

        Returns:
            Dict mapping server_id -> server_info for active servers
        """
        try:
            if not self.registry_path.exists():
                return {}

            data = self._file.load_json()
            servers = data.get("servers", {})

            # Filter to only alive processes
            return self._cleanup_stale_entries(servers)

        except Exception as e:
            logger.error(f"Failed to list servers: {e}", exc_info=True)
            return {}

    def _is_process_alive(self, pid: int) -> bool:
        """
        Check if process with given PID is alive.

        Uses os.kill(pid, 0) which doesn't send a signal but checks existence.

        Args:
            pid: Process ID to check

        Returns:
            True if process exists, False otherwise
        """
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
        except Exception as e:
            logger.warning(f"Error checking PID {pid}: {e}")
            return False

    def _cleanup_stale_entries(self, servers: Dict[str, dict]) -> Dict[str, dict]:
        """
        Remove entries for dead processes.

        Args:
            servers: Dict of server_id -> server_info

        Returns:
            Filtered dict with only alive servers
        """
        alive = {}

        for server_id, info in servers.items():
            pid = info.get("pid")

            if pid is None:
                logger.warning(f"Server entry missing PID: {server_id}")
                continue

            if self._is_process_alive(pid):
                alive[server_id] = info
            else:
                logger.debug(f"Removing stale entry for dead process: {server_id} (PID {pid})")

        return alive
