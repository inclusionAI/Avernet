"""SSE pass-through regression test for the aicoding data-proxy.

Guards the one layer the declarative endpoint suite can't express:
the **router** rendering a streaming result. The JSON cases in
``test_aicoding_data_proxy.py`` only exercise the buffered
``ForwardResult`` → ``Response`` tail. They would stay green even if
the router dropped its ``StreamingResponse`` branch — which is exactly
the regression that shipped (``31fbcb04d`` removed the branch while
the service kept returning ``StreamingForwardResult``, so every
``text/event-stream`` upstream 500'd on ``result.content``).

This drives the real path end-to-end — real router → real
``DataProxyService.forward`` → real ``httpx`` → an in-process fake
engine that replies ``text/event-stream`` — and asserts the response
streams back as SSE rather than buffering or erroring.
"""
from __future__ import annotations

from tests.community.factories.aicoding import route_to_fake_engine_sse


def test_event_stream_upstream_passes_through_as_sse(client) -> None:
    """A ``text/event-stream`` upstream is rendered as a streaming SSE
    response (not buffered, not a 500 on the missing ``.content``)."""
    route_to_fake_engine_sse(world=None)

    with client.stream(
        "POST",
        "/api/aicoding/data-proxy/api/eval/stream",
        json={"prompt": "hi", "mode": "freeform"},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith(
            "text/event-stream"
        )
        body = b"".join(resp.iter_raw())

    # All frames the fake engine emitted made it through intact.
    assert b'"type":"turn_start"' in body
    assert b'"type":"agent_end"' in body
    assert b"event: done" in body
