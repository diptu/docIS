"""
Reject oversized upload requests before their body is parsed.

FastAPI parses a multipart body completely (spooling files to disk) before
the route runs, so the per-file check in ``DocumentService`` alone would
accept, say, a 5 GB request and only then reject it. This middleware caps
the whole request body of ``POST /documents``: a declared
``Content-Length`` over the cap is refused immediately, and a body that
grows past it while streaming (chunked encoding, or a false header) is cut
off. Per-file limits are still enforced by the service.
"""

from collections.abc import Callable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

UPLOAD_PATHS = frozenset({"/documents", "/documents/"})


# (request body cap in bytes, per-file limit in MiB, max files per upload)
Limits = tuple[int, float, int]


class UploadSizeLimitMiddleware:
    def __init__(self, app: ASGIApp, limits: Callable[[], Limits]) -> None:
        """``limits()`` returns (request byte cap, per-file MiB, max files);
        it is called per request, so the current settings apply."""
        self.app = app
        self.limits = limits

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"] not in UPLOAD_PATHS
        ):
            await self.app(scope, receive, send)
            return

        cap, file_mb, max_files = self.limits()
        refusal = JSONResponse(
            status_code=413,
            content={
                "detail": (
                    f"Upload too large: the limit is {file_mb:g} MB per file "
                    f"and {max_files} files per upload."
                )
            },
            headers={"Connection": "close"},
        )

        declared = _content_length(scope)
        if declared is not None and declared > cap:
            await refusal(scope, receive, send)
            return

        received = 0
        exceeded = False
        refused = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            if exceeded:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > cap:
                    # Stop feeding the app; it sees a disconnected client.
                    exceeded = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal refused
            if not exceeded:
                await send(message)
            elif not refused:
                # Replace whatever the app answers with the 413.
                refused = True
                await refusal(scope, receive, send)

        await self.app(scope, limited_receive, guarded_send)
        if exceeded and not refused:
            await refusal(scope, receive, send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope["headers"]:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None
