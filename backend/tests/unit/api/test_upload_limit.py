"""UploadSizeLimitMiddleware edge cases, on a bare ASGI app."""

from typing import Any

from starlette.types import Message, Receive, Scope, Send

from app.api.upload_limit import UploadSizeLimitMiddleware, _content_length

CAP = 10


def _scope(headers: list[tuple[bytes, bytes]] | None = None) -> Scope:
    return {
        "type": "http",
        "method": "POST",
        "path": "/documents",
        "headers": headers or [],
    }


async def _run(app: Any, chunks: list[bytes], scope: Scope) -> list[Message]:
    pending = [
        {"type": "http.request", "body": c, "more_body": i < len(chunks) - 1}
        for i, c in enumerate(chunks)
    ]
    sent: list[Message] = []

    async def receive() -> Message:
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = UploadSizeLimitMiddleware(app, limits=lambda: (CAP, 1.0, 2))
    await middleware(scope, receive, send)
    return sent


async def test_refuses_once_even_if_the_app_keeps_reading_and_never_answers() -> None:
    """An app that ignores the disconnect and sends nothing still yields
    exactly one 413."""
    seen: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        for _ in range(3):
            seen.append((await receive())["type"])

    sent = await _run(app, [b"x" * 6, b"x" * 6, b"x"], _scope())

    assert seen == ["http.request", "http.disconnect", "http.disconnect"]
    assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [413]


async def test_an_unparseable_content_length_falls_back_to_counting() -> None:
    assert _content_length(_scope([(b"content-length", b"lots")])) is None

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await receive()
        await send({"type": "http.response.start", "status": 201, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    sent = await _run(app, [b"small"], _scope([(b"content-length", b"lots")]))

    assert sent[0]["status"] == 201
