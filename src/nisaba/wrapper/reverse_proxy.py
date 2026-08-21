"""
Local HTTP reverse-proxy for Anthropic API traffic.

Terminates plain HTTP on localhost (no TLS interception, no CA cert),
mutates request bodies via AugmentInjector, and streams responses back
from an upstream base URL.

Upstream is resolved from NISABA_ANTHROPIC_BASE_URL (an explicit knob so
we never juggle the user's shell ANTHROPIC_BASE_URL). Defaults to
https://api.anthropic.com when unset.

For corporate relays that use a non-standard CA, set SSL_CERT_FILE or
REQUESTS_CA_BUNDLE — httpx will use that bundle for upstream TLS.
"""

import asyncio
import logging
import os
from typing import Optional

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from nisaba.wrapper.injector import AugmentInjector

logger = logging.getLogger(__name__)

# RFC 7230 §6.1 — hop-by-hop headers must not be forwarded end-to-end.
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

REQ_STRIP = HOP_BY_HOP | {"host", "content-length"}
RESP_STRIP = HOP_BY_HOP | {"content-length"}

DEFAULT_UPSTREAM = "https://api.anthropic.com"
UPSTREAM_ENV_VAR = "NISABA_ANTHROPIC_BASE_URL"


def resolve_upstream_base() -> str:
    return os.environ.get(UPSTREAM_ENV_VAR, DEFAULT_UPSTREAM).rstrip("/")


def _resolve_verify():
    # httpx honors this bundle for upstream TLS; matches how requests/urllib3
    # resolve corporate CAs. If neither is set, httpx uses certifi.
    bundle = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    return bundle if bundle else True


class ReverseProxyServer:
    """Localhost HTTP reverse-proxy that injects augments and forwards to upstream."""

    def __init__(
        self,
        listen_port: int,
        injector: AugmentInjector,
        upstream_base: Optional[str] = None,
    ):
        self.listen_port = listen_port
        self.injector = injector
        self.upstream_base = (upstream_base or resolve_upstream_base()).rstrip("/")

        self._client: Optional[httpx.AsyncClient] = None
        self._server: Optional[uvicorn.Server] = None

    def _build_app(self) -> Starlette:
        return Starlette(routes=[
            Route(
                "/{path:path}",
                self._handle,
                methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
            ),
        ])

    async def start(self) -> asyncio.Task:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(None),  # SSE streams may last minutes
            follow_redirects=False,
            verify=_resolve_verify(),
            http2=False,
        )
        config = uvicorn.Config(
            self._build_app(),
            host="localhost",
            port=self.listen_port,
            log_config=None,
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        logger.info(
            f"Reverse proxy listening on http://localhost:{self.listen_port} "
            f"→ {self.upstream_base}"
        )
        return asyncio.create_task(self._server.serve())

    async def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _handle(self, request: Request) -> Response:
        body = await request.body()
        mutated = self.injector.process_request(
            method=request.method,
            path=request.url.path,
            body_bytes=body,
        )

        fwd_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in REQ_STRIP
        }

        upstream_url = f"{self.upstream_base}{request.url.path}"
        if request.url.query:
            upstream_url = f"{upstream_url}?{request.url.query}"

        assert self._client is not None
        upstream_req = self._client.build_request(
            method=request.method,
            url=upstream_url,
            headers=fwd_headers,
            content=mutated,
        )

        try:
            upstream_resp = await self._client.send(upstream_req, stream=True)
        except httpx.RequestError as e:
            logger.error(f"Upstream request failed: {e}")
            return Response(f"Upstream error: {e}", status_code=502)

        resp_headers = {
            k: v for k, v in upstream_resp.headers.items()
            if k.lower() not in RESP_STRIP
        }

        # For errors, buffer to log a preview; otherwise stream through.
        if upstream_resp.status_code >= 400:
            body_bytes = await upstream_resp.aread()
            self.injector.note_error_response(
                upstream_resp.status_code,
                request.method,
                request.url.path,
                body_bytes[:500],
            )
            await upstream_resp.aclose()
            return Response(
                content=body_bytes,
                status_code=upstream_resp.status_code,
                headers=resp_headers,
            )

        async def stream_body():
            try:
                # aiter_raw yields bytes as received (post-dechunking, pre-decompress).
                # content-encoding is preserved in resp_headers so the client decodes.
                async for chunk in upstream_resp.aiter_raw():
                    yield chunk
            finally:
                await upstream_resp.aclose()

        return StreamingResponse(
            stream_body(),
            status_code=upstream_resp.status_code,
            headers=resp_headers,
        )
