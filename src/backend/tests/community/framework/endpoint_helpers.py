"""Helpers for endpoint injection tests that drive the real app asynchronously.

Two reusable pieces extracted from the publish-flow injection test:

* :func:`drain_background_tasks` — await fire-and-forget work an endpoint spawned
  via ``asyncio.create_task``. The sync ``TestClient`` can't; an
  ``@pytest.mark.asyncio`` test using the ``async_client`` fixture can.
* :func:`http_envelope_response` — build a stub response for the BaaS/BCN
  ``HttpClient`` MockSeam (the ``{code, message, data}`` envelope those upstreams
  return), e.g. as the return of a ``set_response`` / ``set_override`` on
  ``world.get(Annotated[HttpClient, QUALIFIER_BAAS])``.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock


async def drain_background_tasks(timeout: float = 30.0) -> None:
    """Await fire-and-forget tasks an endpoint handler spawned via ``create_task``.

    The test and the in-process ASGI app share one event loop, so a handler's
    ``asyncio.create_task(...)`` lands on this loop. After the HTTP response
    returns, that task is still pending — gather every non-current, unfinished
    task so it runs to completion (including any ``asyncio.to_thread`` offloads
    inside it). No business-code patching is needed.

    ``return_exceptions=True`` so a flow that records its own failure (e.g. a
    publish that lands in ``FAILED``) doesn't break the drain. ``timeout`` is a
    hang guard.

    Caveat: this awaits *all* currently-pending tasks, not only the one your
    endpoint spawned — correct for an in-process single-request test where that's
    the only thing left running. If a handler detaches grandchild tasks that
    outlive their parent, call this in a loop until ``asyncio.all_tasks()``
    settles.
    """
    pending = [
        t for t in asyncio.all_tasks()
        if t is not asyncio.current_task() and not t.done()
    ]
    if pending:
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True), timeout=timeout
        )


def http_envelope_response(
    data: Any = None, *, code: int = 0, message: str = "ok"
) -> MagicMock:
    """A stub ``httpx.Response`` for a BaaS/BCN ``HttpClient`` MockSeam.

    Models the upstream ``{code, message, data}`` envelope: ``raise_for_status``
    is a no-op and ``.json()`` returns the envelope. Use as the value handed to a
    ``set_response`` / ``set_override`` on the qualified ``HttpClient`` seam so a
    service's ``self._http.get/post(...)`` sees a realistic success/error reply
    without any network.
    """
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {
        "code": code, "message": message, "data": {} if data is None else data
    }
    return r
