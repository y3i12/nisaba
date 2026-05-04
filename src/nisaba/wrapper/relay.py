"""
Augment injection relay for the Anthropic API.

Starts a local HTTP server pointed at by ANTHROPIC_BASE_URL=http://127.0.0.1:{port}.
Injects active augments into POST /v1/messages requests and forwards everything
else transparently to api.anthropic.com via HTTPS.

No TLS interception needed — the relay receives plain HTTP from the claude binary
and speaks HTTPS to the upstream API.
"""

import json
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Optional

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

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
    logger.info("Relay logging initialized to .nisaba/logs/proxy.log")


_UUID_RE = re.compile(
    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
    re.IGNORECASE,
)

# Headers that must not be forwarded to the upstream API
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host",
    "content-length",  # httpx will recalculate from the body
})

# Headers that must not be forwarded in the downstream response
_HOP_BY_HOP_RESPONSE = frozenset({
    "transfer-encoding", "connection",
})

FILTERED_TOOLS = {"TodoWrite"}


class AugmentRelay:
    """
    Local HTTP relay that injects augments into Anthropic API requests.

    Receives requests from the claude binary (via ANTHROPIC_BASE_URL=http://127.0.0.1:{port})
    over plain HTTP, injects augments into /v1/messages, and forwards everything to
    the configured upstream (default: api.anthropic.com, or an enterprise proxy URL).
    """

    def __init__(self, upstream_url: str = "https://api.anthropic.com") -> None:
        self.upstream_url = upstream_url.rstrip("/")
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

    # ------------------------------------------------------------------ #
    # Session / augment helpers                                            #
    # ------------------------------------------------------------------ #

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
            logger.warning("No session_id yet, skipping augment injection")
            return False

        try:
            workspace = self._get_workspace_files(self.current_session_id)
        except Exception as e:
            logger.error(f"Failed to get workspace files for {self.current_session_id}: {e}")
            return False

        if "tools" in body:
            body["tools"] = [
                t for t in body["tools"]
                if t.get("name") not in FILTERED_TOOLS
            ]

        if "system" in body:
            injected = (
                f"\n{self._shared_system_prompt.load()}"
                f"\n{workspace.augments.load()}"
                f"\n{self._shared_transcript.load()}"
            )

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

    # ------------------------------------------------------------------ #
    # Request handler                                                      #
    # ------------------------------------------------------------------ #

    async def handle(self, request: Request) -> Response:
        path = request.url.path
        if request.url.query:
            path = f"{path}?{request.url.query}"

        body_bytes = await request.body()

        is_messages = (
            request.method == "POST"
            and request.url.path.split("?")[0].rstrip("/") == "/v1/messages"
        )

        if is_messages:
            try:
                body = json.loads(body_bytes)
                metadata = body.get('metadata', {}) or {}
                session_id = self._extract_session_id(metadata)
                if session_id and session_id != self.current_session_id:
                    self.current_session_id = session_id
                    session_context.set_current_session(session_id)
                    logger.info(f"Session ID set: {session_id}")
                if self._inject_augments(body):
                    body_bytes = json.dumps(body).encode('utf-8')
                    logger.info("Augments injected into /v1/messages")
            except Exception as e:
                logger.error(f"Augment injection error: {e}", exc_info=True)

        forward_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in _HOP_BY_HOP
        }

        client = httpx.AsyncClient(
            base_url=self.upstream_url,
            timeout=httpx.Timeout(connect=10.0, read=600.0, write=60.0, pool=10.0),
        )
        try:
            upstream_request = client.build_request(
                method=request.method,
                url=path,
                content=body_bytes,
                headers=forward_headers,
            )
            upstream_response = await client.send(upstream_request, stream=True)
        except Exception:
            await client.aclose()
            raise

        if upstream_response.status_code >= 400:
            logger.error(
                f"API error {upstream_response.status_code} on "
                f"{request.method} {request.url.path}"
            )

        response_headers = {
            k: v for k, v in upstream_response.headers.items()
            if k.lower() not in _HOP_BY_HOP_RESPONSE
        }

        async def stream_body():
            try:
                async for chunk in upstream_response.aiter_raw():
                    yield chunk
            finally:
                await upstream_response.aclose()
                await client.aclose()

        return StreamingResponse(
            stream_body(),
            status_code=upstream_response.status_code,
            headers=response_headers,
        )

    # ------------------------------------------------------------------ #
    # Server lifecycle                                                     #
    # ------------------------------------------------------------------ #

    def _make_app(self) -> Starlette:
        return Starlette(routes=[
            Route("/{path:path}", self.handle, methods=["GET", "POST", "PUT", "DELETE", "PATCH"]),
            Route("/", self.handle, methods=["GET", "POST", "PUT", "DELETE", "PATCH"]),
        ])

    async def start(self, port: int) -> None:
        config = uvicorn.Config(
            self._make_app(),
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)
        await server.serve()
