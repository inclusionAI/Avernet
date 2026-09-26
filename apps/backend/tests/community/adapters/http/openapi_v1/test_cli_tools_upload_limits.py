"""The CLI-tool upload's receive-time bound (W9, #1477).

The endpoint suite pins what a caller *sees* when an upload is too large: the
413 and its ``413110``. This pins the half a request/response assertion cannot
see — that the platform stops **reading** at the cap, rather than buffering a
body it has already decided to refuse.

The cap itself is monkeypatched down. Moving 200 MiB through a test to prove
the platform will not move 200 MiB is the one shape this test must not take;
what is under test is the mechanism, and the number it uses in production is
asserted against the table that states it, in its own case below.
"""
from __future__ import annotations

import pytest
from starlette.requests import Request

from agentclaw.community.adapters.http.openapi_v1.bots import cli_tools
from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    FETCH_ENTRY_LIMITS,
)

_PATH = "/openapi/v1/bots/{bot_id}/cli-tools"
_BOUNDARY = "cli-tools-test-boundary"
_CHUNK = b"\x00" * 1024


def test_the_cap_is_the_categorys_own_width() -> None:
    """Not a number this module chose. A binary the platform would refuse to
    fetch for a manifest is not one it accepts by upload either, and the only
    way to keep that true is for both to read the same table."""
    assert cli_tools._UPLOAD_LIMIT == FETCH_ENTRY_LIMITS["cli_tools"]


def _prologue() -> bytes:
    return (
        f"--{_BOUNDARY}\r\n"
        'Content-Disposition: form-data; name="file"; filename="mycli"\r\n'
        "Content-Type: application/octet-stream\r\n"
        "\r\n"
    ).encode()


def _request(chunks_sent: list[int]) -> Request:
    """A POST whose body never ends and never says how long it is.

    No ``Content-Length``, so the cheap header refusal cannot fire and the
    stream is the only thing that can stop this. ``chunks_sent`` counts what
    the platform actually pulled.
    """
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/openapi/v1/bots/bot7/cli-tools",
        "raw_path": b"/openapi/v1/bots/bot7/cli-tools",
        "root_path": "",
        "query_string": b"",
        "headers": [
            (
                b"content-type",
                f"multipart/form-data; boundary={_BOUNDARY}".encode(),
            ),
            (b"transfer-encoding", b"chunked"),
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }

    async def receive() -> dict:
        chunks_sent.append(1)
        body = _prologue() if len(chunks_sent) == 1 else _CHUNK
        return {"type": "http.request", "body": body, "more_body": True}

    return Request(scope, receive)


@pytest.mark.asyncio
async def test_an_over_cap_body_is_refused_without_being_read_whole(
    monkeypatch,
) -> None:
    reached: list[str] = []

    async def endpoint(bot_id: str) -> None:  # pragma: no cover - must not run
        reached.append(bot_id)

    route = cli_tools.CliToolUploadRoute(_PATH, endpoint, methods=["POST"])
    monkeypatch.setattr(cli_tools, "_UPLOAD_LIMIT", 8 * 1024)

    chunks_sent: list[int] = []
    response = await route.get_route_handler()(_request(chunks_sent))

    assert response.status_code == 413
    assert b"413110" in response.body
    # The body is endless, so finishing at all is the assertion: the platform
    # stopped pulling. The bound is the cap plus the framing allowance, and one
    # KiB per chunk, so this is a small multiple of that — never the whole of a
    # stream that has no end.
    assert len(chunks_sent) < 100
    # And the handler below it never ran: the refusal is the route's, decided
    # before a dependency, a form model or a service call could see the bytes.
    assert reached == []
