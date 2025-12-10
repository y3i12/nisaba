"""
Context compression for checkpoint-based narrative continuity.

When augments are deactivated, removes tool_use blocks from prior conversation
while preserving assistant's descriptive outputs (insights).
"""

import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


def compress_context(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compress context by removing tool_use blocks before current checkpoint.

    Preserves:
    - User messages (all)
    - Assistant text blocks (insights/analysis)
    - Assistant thinking blocks

    Removes:
    - Assistant tool_use blocks
    - Tool result blocks

    This implements "narrative continuity" - we keep the insights but
    remove the scaffolding (tool calls) that was used to build them.

    Args:
        body: Request body dict with 'messages' array

    Returns:
        Modified body dict with compressed context
    """
    if "messages" not in body:
        logger.debug("No messages array to compress")
        return body

    messages = body["messages"]
    compressed_count = 0
    blocks_removed = 0

    for message in messages:
        if message.get("role") != "assistant":
            # Keep all user messages unchanged
            continue

        # Process assistant message content
        content = message.get("content", [])
        if not isinstance(content, list):
            continue

        # Filter out tool_use blocks
        original_len = len(content)
        filtered_content = [
            block for block in content
            if block.get("type") != "tool_use"
        ]
        new_len = len(filtered_content)

        if original_len != new_len:
            message["content"] = filtered_content
            compressed_count += 1
            blocks_removed += original_len - new_len

    # Also remove tool_result messages (user role with tool results)
    original_msg_count = len(messages)
    body["messages"] = [
        msg for msg in messages
        if not _is_tool_result_message(msg)
    ]
    new_msg_count = len(body["messages"])
    tool_results_removed = original_msg_count - new_msg_count

    if compressed_count > 0 or tool_results_removed > 0:
        logger.info(
            f"Context compressed: {compressed_count} assistant messages, "
            f"{blocks_removed} tool_use blocks, "
            f"{tool_results_removed} tool_result messages removed"
        )
    else:
        logger.debug("No compression needed (no tool blocks found)")

    return body


def _is_tool_result_message(message: Dict[str, Any]) -> bool:
    """
    Check if message is a tool result.

    Tool results appear as user role messages with content blocks
    containing tool results.

    Args:
        message: Message dict

    Returns:
        True if this is a tool result message
    """
    if message.get("role") != "user":
        return False

    content = message.get("content", [])
    if not isinstance(content, list):
        return False

    # Check if any content block is a tool_result
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            return True

    return False


def estimate_compression_savings(body: Dict[str, Any]) -> Dict[str, int]:
    """
    Estimate how much compression would save (without modifying).

    Args:
        body: Request body dict

    Returns:
        Dict with statistics: {
            'tool_use_blocks': int,
            'tool_result_messages': int,
            'compressible_assistant_messages': int
        }
    """
    if "messages" not in body:
        return {
            'tool_use_blocks': 0,
            'tool_result_messages': 0,
            'compressible_assistant_messages': 0
        }

    tool_use_blocks = 0
    tool_result_messages = 0
    compressible_assistant_messages = 0

    for message in body["messages"]:
        # Count tool results
        if _is_tool_result_message(message):
            tool_result_messages += 1
            continue

        # Count assistant tool_use blocks
        if message.get("role") == "assistant":
            content = message.get("content", [])
            if isinstance(content, list):
                has_tool_use = False
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_use_blocks += 1
                        has_tool_use = True

                if has_tool_use:
                    compressible_assistant_messages += 1

    return {
        'tool_use_blocks': tool_use_blocks,
        'tool_result_messages': tool_result_messages,
        'compressible_assistant_messages': compressible_assistant_messages
    }
