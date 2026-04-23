"""
Transcript extraction and session resolution.

Shared by `nisaba compact` CLI command and the legacy precompact-extract script.

Discovery model: at startup `nisaba claude` writes its instance pointer (containing
just the startup timestamp). At compact time we scan the CC project directory for
the active interactive JSONL — the one whose first entry's cwd matches our cwd,
that is not a sidechain (subagent), and whose mtime is newer than the instance's
startup time. The most-recently-modified survivor wins.

This bypasses the proxy entirely for session discovery — `metadata.user_id` no
longer contains the JSONL session UUID in modern CC versions, and the JSONL
itself is the only reliable source of truth.
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from nisaba.structured_file import StructuredFileCache


ACTIVE_DIR = Path(".nisaba/sessions/.active")
TRANSCRIPT_CACHE = Path(".nisaba/base_files/compacted_transcript.md")


class CompactError(Exception):
    """Raised when the compact command cannot resolve or execute."""


def write_active_pointer(instance_id: str) -> None:
    """`nisaba claude` writes this at startup. Stores the startup epoch time."""
    ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    (ACTIVE_DIR / instance_id).write_text(str(time.time()))


def clear_active_pointer(instance_id: str) -> None:
    """`nisaba claude` shutdown calls this."""
    pointer = ACTIVE_DIR / instance_id
    if pointer.exists():
        pointer.unlink()


def jsonl_path_for_session(session_id: str) -> Path:
    """Claude CLI stores sessions at ~/.claude/projects/<cwd-with-/-as-dashes>/<uuid>.jsonl."""
    cwd_normalized = str(Path.cwd()).replace("/", "-")
    return Path.home() / ".claude" / "projects" / cwd_normalized / f"{session_id}.jsonl"


def _read_first_entry(path: Path) -> Optional[dict]:
    """Read just the first line of a jsonl as JSON, or None on failure."""
    try:
        with open(path) as f:
            line = f.readline()
        return json.loads(line) if line.strip() else None
    except (OSError, json.JSONDecodeError):
        return None


def _instance_startup_time(instance_id: Optional[str]) -> float:
    """Return the recorded startup epoch for this instance, or 0.0 if unknown."""
    if not instance_id:
        return 0.0
    pointer = ACTIVE_DIR / instance_id
    if not pointer.exists():
        return 0.0
    try:
        return float(pointer.read_text().strip())
    except ValueError:
        return 0.0


def discover_active_jsonl(instance_id: Optional[str]) -> Path:
    """
    Find the JSONL of the active interactive session for current cwd.

    Filters:
      • first-entry cwd == os.getcwd()  (CC writes the launch cwd in every entry)
      • first-entry isSidechain == false (filters subagents)
      • mtime > instance startup time   (filters previous sessions in same cwd)

    Multi-instance same-cwd is undefined here — the most-recently-modified
    JSONL wins. Use --session <uuid> to override.
    """
    cwd = str(Path.cwd())
    cwd_normalized = cwd.replace("/", "-")
    project_dir = Path.home() / ".claude" / "projects" / cwd_normalized

    if not project_dir.exists():
        raise CompactError(
            f"No Claude Code project dir for {cwd} ({project_dir}). "
            f"Did you start `nisaba claude` from this directory?"
        )

    startup_time = _instance_startup_time(instance_id)

    candidates: List[Tuple[float, Path]] = []
    for jsonl in project_dir.glob("*.jsonl"):
        try:
            mtime = jsonl.stat().st_mtime
        except OSError:
            continue
        if mtime < startup_time:
            continue
        entry = _read_first_entry(jsonl)
        if not entry:
            continue
        if entry.get("isSidechain", False):
            continue
        if entry.get("cwd") != cwd:
            continue
        candidates.append((mtime, jsonl))

    if not candidates:
        msg = f"No interactive session JSONL found in {project_dir}"
        if instance_id and startup_time > 0:
            msg += (
                f" newer than instance {instance_id} startup "
                f"({datetime.fromtimestamp(startup_time).isoformat()})"
            )
        msg += ". Send at least one message in your `nisaba claude` session first."
        raise CompactError(msg)

    candidates.sort(reverse=True)
    return candidates[0][1]


def resolve_session(
    explicit_instance: Optional[str] = None,
    explicit_session: Optional[str] = None,
) -> Tuple[str, Path]:
    """
    Resolve (session_id, jsonl_path).

    Priority:
      1. --session <uuid>            → direct path
      2. --instance / NISABA_INSTANCE_ID → discovery filtered by that instance's startup time
      3. Plain discovery (no startup floor — picks newest matching JSONL in cwd)
    """
    if explicit_session:
        jsonl_path = jsonl_path_for_session(explicit_session)
        if not jsonl_path.exists():
            raise CompactError(f"JSONL not found: {jsonl_path}")
        return explicit_session, jsonl_path

    instance_id = explicit_instance or os.environ.get("NISABA_INSTANCE_ID")
    jsonl_path = discover_active_jsonl(instance_id)
    return jsonl_path.stem, jsonl_path


def extract_transcript(jsonl_path: Path, session_id: str) -> str:
    """
    Extract conversational text from a Claude Code JSONL.

    Keeps: user text, assistant text, thinking blocks, Task/WebSearch tool
    invocations + results. Drops: all other tool_use / tool_result blocks.
    """
    if not jsonl_path.exists():
        return ""

    transcript_lines = [
        "---",
        f"Session: {session_id} - {jsonl_path} - {datetime.now().isoformat()}",
        "---",
    ]

    last_role = ""
    tasks: dict = {}
    searches: dict = {}

    with open(jsonl_path, "r") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = entry.get("type")
            message = entry.get("message", {})
            content = message.get("content", [])
            role = message.get("role", msg_type)

            if isinstance(content, str):
                text = content.strip()
                if text:
                    if last_role != role:
                        transcript_lines.append(f"# {role.upper()}")
                        transcript_lines.append("")
                        last_role = role
                    transcript_lines.append(text)
                    transcript_lines.append("")
                continue

            if not isinstance(content, list):
                continue

            text_parts = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")

                if block_type == "text":
                    text = block.get("text", "").strip()
                    if text:
                        text_parts.append(text)
                elif block_type == "thinking":
                    thinking = block.get("thinking", "").strip()
                    if thinking:
                        text_parts.append(f"<thinking>\n{thinking}\n</thinking>")
                elif block_type == "tool_use":
                    if block.get("name") == "Task" and "input" in block:
                        tasks[block.get("id")] = block.get("input")
                    elif block.get("name") == "WebSearch" and "input" in block:
                        searches[block.get("id")] = block.get("input")
                elif block_type == "tool_result":
                    task_input = tasks.get(block.get("tool_use_id"))
                    search_input = searches.get(block.get("tool_use_id"))
                    text = ""

                    if task_input or search_input:
                        role = "assistant"
                        tool_u_content = block.get("content")
                        if isinstance(tool_u_content, list):
                            for cb in tool_u_content:
                                if isinstance(cb, dict) and "text" in cb:
                                    text += "\n" + cb.get("text", "")
                        if isinstance(tool_u_content, str):
                            text = "\n" + tool_u_content

                    if task_input:
                        text_parts.append(
                            f"<task>\n<description>{task_input.get('description', '')}</description>\n"
                            f"<prompt>\n{task_input.get('prompt', '')}\n</prompt>\n\n"
                            f"<result>\n{text}\n</result>\n</task>"
                        )
                    elif search_input:
                        text_parts.append(
                            f"<web_search>\n<query>{search_input.get('query', '')}</query>\n"
                            f"<result>\n{text}\n</result>\n</web_search>"
                        )

            if text_parts:
                if last_role != role:
                    transcript_lines.append(f"# {role.upper()}")
                    transcript_lines.append("")
                    last_role = role
                for part in text_parts:
                    transcript_lines.append(part)
                    transcript_lines.append("")

    return "\n".join(transcript_lines)


def append_to_cache(transcript: str) -> None:
    """Append a fresh transcript block to the compacted_transcript.md cache."""
    cache = StructuredFileCache(
        file_path=TRANSCRIPT_CACHE,
        name="transcript",
        tag="COMPACTED_TRANSCRIPT",
    )
    cache.load()
    existing = cache.content + "\n\n" if cache.content else ""
    cache.write(existing + transcript)
