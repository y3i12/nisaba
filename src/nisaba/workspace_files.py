"""
Centralized registry for shared workspace files.

Provides session-keyed singleton access to StructuredFileCache instances for
workspace coordination files that appear in Claude's system prompt.

Files are split into:
- SHARED: Global files shared across all sessions (.nisaba/tui/)
- PER-SESSION: Session-specific files (.nisaba/request_cache/{session_id}/tui/)
"""

from pathlib import Path
from typing import Dict, Optional
from nisaba.structured_file import StructuredFileCache, JsonStructuredFile


class WorkspaceFiles:
    """
    Session-keyed singleton registry for workspace files.

    Manages StructuredFileCache instances for files that are:
    - Written by multiple components (tools, managers, proxy)
    - Read by proxy for system prompt injection
    - Visible in Claude's workspace sections

    Files are categorized as:
    - SHARED: Global across all sessions (system_prompt, compacted_transcript)
    - PER-SESSION: Isolated per session (augments, todos, status_bar, etc.)
    """

    _instances: Dict[str, 'WorkspaceFiles'] = {}

    def __init__(self, session_id: str):
        """Initialize workspace files for a specific session.

        Args:
            session_id: Session identifier (required, must not be empty)

        Raises:
            ValueError: If session_id is empty or None
        """
        if not session_id:
            raise ValueError("session_id is required for WorkspaceFiles initialization")

        self.session_id = session_id

        # Session-specific directory
        session_tui = Path(f".nisaba/request_cache/{session_id}/tui")
        session_tui.mkdir(parents=True, exist_ok=True)

        # === PER-SESSION FILES (isolated per session) ===

        self.augments = StructuredFileCache(
            file_path=session_tui / "augment_view.md",
            name="augments",
            tag="AUGMENTS"
        )

        self.core_system_prompt = StructuredFileCache(
            file_path=session_tui / "core_system_prompt.md",
            name="core system prompt",
            tag="CORE_SYSTEM_PROMPT"
        )

        self.structural_view = StructuredFileCache(
            file_path=session_tui / "structural_view.md",
            name="structural view",
            tag="STRUCTURAL_VIEW"
        )

        self.todos = StructuredFileCache(
            file_path=session_tui / "todo_view.md",
            name="todos",
            tag="TODOS"
        )

        self.notifications = StructuredFileCache(
            file_path=session_tui / "notification_view.md",
            name="notifications",
            tag="NOTIFICATIONS"
        )

        self.notification_state = JsonStructuredFile(
            file_path=session_tui / "notification_state.json",
            name="notification state",
            default_factory=lambda: {
                "session_id": "",
                "last_tool_id_seen": ""
            }
        )

        # === SHARED FILES (global across all sessions) ===

        self.system_prompt = StructuredFileCache(
            file_path=Path(".nisaba/base_files/system_prompt.md"),
            name="system prompt",
            tag="USER_SYSTEM_PROMPT_INJECTION"
        )

        self.transcript = StructuredFileCache(
            file_path=Path(".nisaba/base_files/compacted_transcript.md"),
            name="transcript",
            tag="COMPACTED_TRANSCRIPT"
        )

        self.mcp_servers = JsonStructuredFile(
            file_path=Path(".nisaba/mcp_servers.json"),
            name="mcp servers",
            default_factory=lambda: {
                "version": "1.0",
                "servers": {}
            }
        )

    @classmethod
    def instance(cls, session_id: str) -> 'WorkspaceFiles':
        """
        Get or create WorkspaceFiles instance for session.

        Args:
            session_id: Session identifier (required)

        Returns:
            WorkspaceFiles instance for this session

        Raises:
            ValueError: If session_id is empty or None
        """
        if not session_id:
            raise ValueError("session_id is required")

        if session_id not in cls._instances:
            cls._instances[session_id] = cls(session_id)
        return cls._instances[session_id]

    @classmethod
    def reset_instance(cls, session_id: Optional[str] = None) -> None:
        """Reset singleton instance(s) (primarily for testing).

        Args:
            session_id: If provided, reset only this session. Otherwise reset all.
        """
        if session_id:
            cls._instances.pop(session_id, None)
        else:
            cls._instances.clear()
