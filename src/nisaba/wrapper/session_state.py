"""
In-memory session state management using claude-code-log models.

This module provides a structured, type-safe way to maintain session state
by leveraging claude-code-log's Pydantic models instead of manual dict tracking.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from claude_code_log.models import (
    AssistantTranscriptEntry,
    SummaryTranscriptEntry,
    SystemTranscriptEntry,
    TranscriptEntry,
    UserTranscriptEntry,
    parse_transcript_entry,
)
from claude_code_log.parser import load_transcript


class SessionState:
    """
    In-memory representation of a Claude Code session.

    Uses claude-code-log's Pydantic models to maintain structured,
    type-safe session state with support for:
    - Loading existing JSONL transcripts
    - Appending new entries from API requests/responses
    - Tracking tool result visibility
    - Querying conversation data
    - Computing token usage statistics

    Example:
        >>> session = SessionState()
        >>> session.load_from_jsonl(Path("session.jsonl"))
        >>> session.hide_tool_results(["toolu_123", "toolu_456"])
        >>> visible_tools = session.get_visible_tool_results()
        >>> usage = session.get_token_usage()
    """

    def __init__(self):
        """Initialize empty session state."""
        self.entries: List[TranscriptEntry] = []
        self.tool_visibility: Dict[str, bool] = {}  # tool_use_id -> visible
        self._session_id: Optional[str] = None

    def load_from_jsonl(self, jsonl_path: Path, silent: bool = True) -> int:
        """
        Load session from JSONL file using claude-code-log parser.

        Args:
            jsonl_path: Path to JSONL transcript file
            silent: Suppress parser output (default: True)

        Returns:
            Number of entries loaded
        """
        self.entries = load_transcript(jsonl_path, silent=silent)
        self._init_tool_visibility()
        self._extract_session_id()
        return len(self.entries)

    def append_entry(self, entry_dict: Dict[str, Any]) -> TranscriptEntry:
        """
        Append a new entry from API request/response dict.

        Args:
            entry_dict: Raw dictionary from API (user message, assistant response, etc.)

        Returns:
            Parsed TranscriptEntry object
        """
        entry = parse_transcript_entry(entry_dict)
        self.entries.append(entry)

        # Update tool visibility tracking if needed
        if isinstance(entry, UserTranscriptEntry):
            self._track_tool_results_in_user_message(entry)

        return entry

    def _init_tool_visibility(self):
        """Extract all tool IDs and initialize visibility state."""
        for entry in self.entries:
            if isinstance(entry, UserTranscriptEntry):
                self._track_tool_results_in_user_message(entry)

    def _track_tool_results_in_user_message(self, entry: UserTranscriptEntry):
        """Extract tool_use_ids from user message tool_result content."""
        if isinstance(entry.message.content, list):
            for item in entry.message.content:
                if hasattr(item, 'type') and item.type == "tool_result":
                    # Default to visible if not already tracked
                    if item.tool_use_id not in self.tool_visibility:
                        self.tool_visibility[item.tool_use_id] = True

    def _extract_session_id(self):
        """Extract session ID from first entry that has one."""
        for entry in self.entries:
            if hasattr(entry, 'sessionId'):
                self._session_id = entry.sessionId
                return

    # --- Visibility Management ---

    def hide_tool_results(self, tool_ids: List[str]) -> int:
        """
        Mark tool results as hidden.

        Args:
            tool_ids: List of tool_use_ids to hide

        Returns:
            Number of tools actually hidden
        """
        count = 0
        for tool_id in tool_ids:
            if tool_id in self.tool_visibility:
                self.tool_visibility[tool_id] = False
                count += 1
        return count

    def show_tool_results(self, tool_ids: List[str]) -> int:
        """
        Mark tool results as visible.

        Args:
            tool_ids: List of tool_use_ids to show

        Returns:
            Number of tools actually shown
        """
        count = 0
        for tool_id in tool_ids:
            if tool_id in self.tool_visibility:
                self.tool_visibility[tool_id] = True
                count += 1
        return count

    def hide_all_tool_results(self) -> int:
        """
        Hide all tool results.

        Returns:
            Number of tools hidden
        """
        count = 0
        for tool_id in self.tool_visibility:
            if self.tool_visibility[tool_id]:
                self.tool_visibility[tool_id] = False
                count += 1
        return count

    def get_visible_tool_ids(self) -> Set[str]:
        """Get set of currently visible tool IDs."""
        return {tid for tid, visible in self.tool_visibility.items() if visible}

    def get_hidden_tool_ids(self) -> Set[str]:
        """Get set of currently hidden tool IDs."""
        return {tid for tid, visible in self.tool_visibility.items() if not visible}

    # --- Query Methods ---

    def get_visible_tool_results(self) -> List[Tuple[str, Any]]:
        """
        Get tool results that should be visible in workspace.

        Returns:
            List of (tool_use_id, content) tuples for visible tools
        """
        results = []
        for entry in self.entries:
            if isinstance(entry, UserTranscriptEntry):
                if isinstance(entry.message.content, list):
                    for item in entry.message.content:
                        if hasattr(item, 'type') and item.type == "tool_result":
                            tool_id = item.tool_use_id
                            if self.tool_visibility.get(tool_id, True):
                                results.append((tool_id, item.content))
        return results

    def get_token_usage(self) -> Dict[str, int]:
        """
        Calculate total token usage from all assistant messages.

        Returns:
            Dictionary with input_tokens, output_tokens, cache_read_input_tokens, etc.
        """
        total = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }

        for entry in self.entries:
            if isinstance(entry, AssistantTranscriptEntry):
                usage = entry.message.usage
                if usage:
                    total["input_tokens"] += usage.input_tokens or 0
                    total["output_tokens"] += usage.output_tokens or 0
                    total["cache_read_input_tokens"] += usage.cache_read_input_tokens or 0
                    total["cache_creation_input_tokens"] += usage.cache_creation_input_tokens or 0

        return total

    def get_conversation_pairs(self) -> List[Tuple[str, str]]:
        """
        Extract user-assistant conversation pairs (text only).

        Returns:
            List of (role, content) tuples
        """
        pairs = []
        for entry in self.entries:
            if isinstance(entry, UserTranscriptEntry):
                content = entry.message.content
                if isinstance(content, str):
                    pairs.append(("user", content))
            elif isinstance(entry, AssistantTranscriptEntry):
                for item in entry.message.content:
                    if item.type == "text":
                        pairs.append(("assistant", item.text))
        return pairs

    def get_user_entries(self) -> List[UserTranscriptEntry]:
        """Get all user message entries."""
        return [e for e in self.entries if isinstance(e, UserTranscriptEntry)]

    def get_assistant_entries(self) -> List[AssistantTranscriptEntry]:
        """Get all assistant message entries."""
        return [e for e in self.entries if isinstance(e, AssistantTranscriptEntry)]

    def get_summary_entries(self) -> List[SummaryTranscriptEntry]:
        """Get all summary entries."""
        return [e for e in self.entries if isinstance(e, SummaryTranscriptEntry)]

    def get_system_entries(self) -> List[SystemTranscriptEntry]:
        """Get all system entries."""
        return [e for e in self.entries if isinstance(e, SystemTranscriptEntry)]

    # --- Properties ---

    @property
    def session_id(self) -> Optional[str]:
        """Get session ID if available."""
        return self._session_id

    @property
    def entry_count(self) -> int:
        """Total number of entries."""
        return len(self.entries)

    @property
    def tool_count(self) -> int:
        """Total number of tracked tools."""
        return len(self.tool_visibility)

    @property
    def visible_tool_count(self) -> int:
        """Number of visible tools."""
        return sum(1 for v in self.tool_visibility.values() if v)

    @property
    def hidden_tool_count(self) -> int:
        """Number of hidden tools."""
        return sum(1 for v in self.tool_visibility.values() if not v)

    def __repr__(self) -> str:
        return (
            f"SessionState(entries={self.entry_count}, "
            f"tools={self.tool_count} ({self.visible_tool_count} visible), "
            f"session_id={self.session_id})"
        )
