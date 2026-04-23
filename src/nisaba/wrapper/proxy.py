"""
Augments injection proxy using mitmproxy.

Intercepts POST /v1/messages requests to the Anthropic API and appends
augments content (user system prompt + active augments + compacted transcript)
to the last system block.
"""

import json
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Optional

from mitmproxy import http

from nisaba import session_context
from nisaba.augments import get_augment_manager
from nisaba.structured_file import StructuredFileCache
from nisaba.workspace_files import WorkspaceFiles

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

log_dir = Path(".nisaba/logs")
log_dir.mkdir(parents=True, exist_ok=True)

if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
    file_handler = RotatingFileHandler(
        log_dir / "proxy.log",
        maxBytes=1 * 1024 * 1024,
        backupCount=3,
    )
    file_handler.setFormatter(
        logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    )
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    logger.info("Proxy logging initialized to .nisaba/logs/proxy.log")


_UUID_RE = re.compile(
    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
    re.IGNORECASE,
)


class AugmentInjector:
    """
    mitmproxy addon that appends augments to Anthropic `/v1/messages` requests.

    Injects user system prompt, active augments, and compacted transcript
    as a suffix to the last system block of the request body.
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

    def request(self, flow: http.HTTPFlow) -> None:
        if not self._is_anthropic_request(flow):
            return

        logger.debug(f"Intercepted: {flow.request.method} {flow.request.path}")

        try:
            body = json.loads(flow.request.content)

            metadata = body.get('metadata', {}) or {}
            session_id = self._extract_session_id(metadata)
            if session_id and session_id != self.current_session_id:
                self.current_session_id = session_id
                session_context.set_current_session(session_id)
                logger.info(f"Session ID set: {session_id}")

            if not self._is_messages_endpoint(flow):
                return

            if self._inject_augments(body):
                flow.request.content = json.dumps(body).encode('utf-8')

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse request JSON: {e}")
        except Exception as e:
            logger.error(f"Error processing request: {e}")

    def response(self, flow: http.HTTPFlow) -> None:
        if not self._is_anthropic_request(flow):
            return
        if flow.response and flow.response.status_code >= 400:
            body = flow.response.content[:500] if flow.response.content else b'empty'
            logger.error(
                f"API error {flow.response.status_code} on "
                f"{flow.request.method} {flow.request.path} - response: {body}"
            )

    def _is_anthropic_request(self, flow: http.HTTPFlow) -> bool:
        return (
            flow.request.method == "POST"
            and "api.anthropic.com" in flow.request.pretty_host
        )

    def _is_messages_endpoint(self, flow: http.HTTPFlow) -> bool:
        # Strip query string (e.g. ?beta=true) — only match /v1/messages exactly,
        # never /v1/messages/count_tokens or /v1/messages/batches.
        path = flow.request.path.split("?")[0].rstrip("/")
        return path == "/v1/messages"

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
                # Bootstrap AugmentManager: loads pinned augments, writes augments.md
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

            # Append to the LAST system block rather than replacing or inserting.
            # CC sends multiple system blocks (billing, CC identifier, main prompt)
            # and the API validates their structure — replacement causes 500s.
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
