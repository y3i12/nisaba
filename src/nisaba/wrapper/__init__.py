"""
Nisaba wrapper module for MCP client command wrappers.

Provides infrastructure for wrapping LLM CLI tools with context injection
capabilities via HTTP proxy interception.
"""

from nisaba.wrapper.claude import create_claude_wrapper_command

__all__ = [
    "create_claude_wrapper_command",
]
