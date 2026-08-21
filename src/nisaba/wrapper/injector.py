"""
Augments injection for Anthropic `/v1/messages` request bodies.

Transport-agnostic: given a raw request body, mutates it in place to
append the user system prompt, active augments, and compacted transcript
to the last system block. Also tracks the current session id from
request metadata so the MCP side knows whose augments to serve.
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, Optional

from nisaba import session_context
from nisaba.augments import get_augment_manager
from nisaba.structured_file import StructuredFileCache
from nisaba.workspace_files import WorkspaceFiles

# Logging is centrally configured by nisaba.logging_setup.setup_logging(),
# called at CLI startup. Just take our logger and rely on the root handler.
logger = logging.getLogger(__name__)


_UUID_RE = re.compile(
    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
    re.IGNORECASE,
)


class AugmentInjector:
    """
    Transport-agnostic augments injector.

    process_request() takes raw request bytes and returns the (possibly
    mutated) bytes to forward upstream. Session id extraction runs on
    any POST with a JSON body; injection only runs on POST /v1/messages.
    """

    FILTERED_TOOLS = {"TodoWrite"}

    def __init__(self) -> None:
        self._workspace_files_cache: Dict[str, WorkspaceFiles] = {}
        self.current_session_id: Optional[str] = None

        self._shared_system_prompt = StructuredFileCache(
            file_path=Path(".nisaba/base_files/system_prompt.md"),
            name="system prompt",
            tag="USER_SYSTEM_PROMPT_INJECTION",
        )
        self._shared_transcript = StructuredFileCache(
            file_path=Path(".nisaba/base_files/compacted_transcript.md"),
            name="transcript",
            tag="COMPACTED_TRANSCRIPT",
        )
        self._shared_system_prompt.load()
        self._shared_transcript.load()

    def process_request(
        self,
        method: str,
        path: str,
        body_bytes: bytes,
    ) -> bytes:
        """Return the (possibly mutated) body to forward upstream."""
        if method != "POST" or not body_bytes:
            return body_bytes

        try:
            body = json.loads(body_bytes)
        except json.JSONDecodeError:
            return body_bytes

        metadata = body.get('metadata', {}) or {}
        session_id = self._extract_session_id(metadata)
        if session_id and session_id != self.current_session_id:
            self.current_session_id = session_id
            session_context.set_current_session(session_id)
            logger.info(f"Session ID set: {session_id}")

        if not self._is_messages_endpoint(path):
            return body_bytes

        if self._inject_augments(body):
            return json.dumps(body).encode('utf-8')
        return body_bytes

    def note_error_response(
        self,
        status_code: int,
        method: str,
        path: str,
        preview: bytes,
    ) -> None:
        if status_code >= 400:
            logger.error(
                f"API error {status_code} on {method} {path} - response: {preview!r}"
            )

    @staticmethod
    def _is_messages_endpoint(path: str) -> bool:
        # Strip query string (e.g. ?beta=true) — only match /v1/messages exactly,
        # never /v1/messages/count_tokens or /v1/messages/batches.
        p = path.split("?")[0].rstrip("/")
        return p == "/v1/messages"

    def _extract_session_id(self, metadata: dict) -> Optional[str]:
        user_id = metadata.get('user_id')

        if user_id and '_session_' in user_id:
            return user_id.split('_session_')[1]

        session_id = metadata.get('session_id')
        if session_id:
            return session_id

        if user_id:
            match = _UUID_RE.search(user_id)
            if match:
                return match.group(0)
            logger.warning(f"Could not extract session_id from user_id: {user_id!r}")

        return None

    def _get_workspace_files(self, session_id: str) -> WorkspaceFiles:
        if session_id not in self._workspace_files_cache:
            self._workspace_files_cache[session_id] = WorkspaceFiles.instance(session_id)
            try:
                get_augment_manager(session_id)
            except Exception as e:
                logger.warning(f"Failed to bootstrap AugmentManager for {session_id}: {e}")
        return self._workspace_files_cache[session_id]

    def _inject_augments(self, body: dict) -> bool:
        if not self.current_session_id:
            logger.warning("No valid session_id, skipping augment injection")
            return False

        try:
            workspace = self._get_workspace_files(self.current_session_id)
        except Exception as e:
            logger.error(f"Failed to get workspace files for {self.current_session_id}: {e}")
            return False

        if "tools" in body:
            body["tools"] = [
                t for t in body["tools"]
                if t.get("name") not in self.FILTERED_TOOLS
            ]

        if "system" in body:
            injected = (
                f"\n{self._shared_system_prompt.load()}"
                f"\n{workspace.augments.load()}"
                f"\n{self._shared_transcript.load()}"
            )

            # CC sends multiple system blocks and the API validates their
            # structure — append to the last one rather than replacing.
            last_idx = len(body["system"]) - 1
            if last_idx >= 0 and "text" in body["system"][last_idx]:
                body["system"][last_idx]["text"] += injected
            else:
                body["system"].append({
                    "type": "text",
                    "text": injected,
                    "cache_control": {"type": "ephemeral"},
                })

        return "tools" in body or "system" in body
