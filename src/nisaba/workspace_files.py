"""
Session-keyed registry for the active augments file.

Each session gets a `.nisaba/sessions/{session_id}/` directory holding the
composed `augments.md` that the proxy injects into the Claude system prompt.
"""

from pathlib import Path
from typing import Dict, Optional

from nisaba.structured_file import StructuredFileCache


class WorkspaceFiles:
    """Session-keyed singleton holding the per-session augments cache."""

    _instances: Dict[str, 'WorkspaceFiles'] = {}

    def __init__(self, session_id: str):
        if not session_id:
            raise ValueError("session_id is required for WorkspaceFiles initialization")

        self.session_id = session_id

        session_dir = Path(f".nisaba/sessions/{session_id}")
        session_dir.mkdir(parents=True, exist_ok=True)

        self.augments = StructuredFileCache(
            file_path=session_dir / "augments.md",
            name="augments",
            tag="AUGMENTS",
        )

    @classmethod
    def instance(cls, session_id: str) -> 'WorkspaceFiles':
        if not session_id:
            raise ValueError("session_id is required")
        if session_id not in cls._instances:
            cls._instances[session_id] = cls(session_id)
        return cls._instances[session_id]

    @classmethod
    def reset_instance(cls, session_id: Optional[str] = None) -> None:
        if session_id:
            cls._instances.pop(session_id, None)
        else:
            cls._instances.clear()
