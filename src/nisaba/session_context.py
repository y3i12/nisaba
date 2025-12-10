"""
Thread-safe session context for MCP tools.

Provides global session tracking so MCP tools can access the current session_id
without explicit parameter passing. Used by proxy to set context, tools to read it.

Architecture:
- Proxy extracts session_id from request metadata
- Proxy sets session_id via set_current_session()
- MCP tools read session_id via get_current_session()
- WorkspaceFiles uses session_id to access session-specific files

Thread-safety:
- Uses threading.local() for thread-local storage
- Each thread (request) has isolated session context
- Safe for concurrent requests (though not expected in agent swarm)
"""

import threading
from typing import Optional

# Thread-local storage for session context
_session_context = threading.local()


def set_current_session(session_id: str) -> None:
    """
    Set current session ID for this thread.

    Called by proxy after extracting session_id from request metadata.

    Args:
        session_id: Session identifier from request metadata

    Raises:
        ValueError: If session_id is empty or None
    """
    if not session_id:
        raise ValueError("session_id must not be empty")

    _session_context.session_id = session_id


def get_current_session() -> Optional[str]:
    """
    Get current session ID for this thread.

    Called by MCP tools to determine which session's files to access.

    Returns:
        Session ID if set, None otherwise
    """
    return getattr(_session_context, 'session_id', None)


def clear_current_session() -> None:
    """
    Clear session context for this thread.

    Optional cleanup, mainly for testing. Thread-local storage is
    automatically cleaned up when thread terminates.
    """
    if hasattr(_session_context, 'session_id'):
        del _session_context.session_id
