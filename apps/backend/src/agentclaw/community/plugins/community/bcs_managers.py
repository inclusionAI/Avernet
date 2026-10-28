"""HttpBcsManagersPlugin — trusted-App HTTP delivery to BCS managers.

The ``BcsManagersPlugin`` PROD implementation: exactly one ``PUT`` per
call to the BCS v1 bot-authority managers endpoint through the Gateway,
with every failure classified into the kernel's ``DeliveryFailure``
vocabulary. Nothing else — no retries, no HMAC, no
``X-Avernet-Principal`` signing, no user-token forwarding; the
Authorization header carries only the per-tenant trusted-App credential
(the same bearer the Gateway app-token strategy resolves against its
app registry). Everything deeper about identity is BCS's own
application rule, applied to the verified App.

BCS contract (v1 bot authority, frozen upstream):

- ``PUT /api/v1/collaboration/bots/{bot_id}/managers`` with body
  ``{"manager_user_ids": [...]}``. The array is REQUIRED — never
  null — so an empty roster is a PUT of ``[]`` that clears the set
  rather than a patch or a mistake. The bot id travels percent-encoded
  (``quote(..., safe="")``) so spaces, slashes, and colons cannot
  split or reshape the route; BCS decodes the segment back.
- The only confirmed outcome is HTTP 200 with the Envelope
  ``{"code": 20000, "message", "request_id", "data": {"added",
  "removed", "total"}}``. The counts are returned verbatim —
  ``total == 0`` on a valid envelope is BCS's real answer, and drift
  stays the consumer's decision, never the adapter's disguise.

Classification (the safety core — a wrong label either re-sends a
mutation or silently strands a bot):

- ``NOT_SENT`` — proven pre-send: a tenant the composition root never
  gave a credential (the adapter refuses to send unauthenticated), and
  connect / pool-acquisition failures. Safe to retry.
- ``REJECTED`` — 400/401/403/404/409: BCS considered and refused this
  snapshot. Permanent for this attempt.
- ``UNKNOWN`` — everything else: read/write/overall timeout (pre- and
  post-send are indistinguishable at the wire), disconnects, 5xx, 429
  (a rate limit proves nothing about pre- vs post-execution), and any
  response that fails Envelope verification. ``UNKNOWN`` blocks the
  bot's next sync; this adapter never auto-retries any outcome.

Failures carry only the stable ``kind`` plus a short classification
code — never response bodies or exception texts, which may hold
internal diagnostics.

Lifecycle: with ``client=None`` the plugin lazily creates and owns one
``httpx.AsyncClient`` on the first async call (born inside the worker
loop); ``aclose()`` closes only a self-owned client and is a no-op for
one the caller owns. The composition root (Task 6) resolves the
``base_url`` (Gateway external origin; the path is fixed here) and the
per-tenant credentials; the adapter takes both as constructor
parameters.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from urllib.parse import quote

import httpx

from agentclaw.community.kernel.bot_manager_sync import (
    DeliveryFailure,
    ManagerSetChange,
    ManagerSnapshot,
)
from agentclaw.community.plugin_api.bcs_managers import (
    BcsManagersError,
    BcsManagersPlugin,
)
from agentclaw.community.plugin_api.impl_registry import Mode, plugin_impl

#: BCS Envelope success code (``20_000`` = OK in the v1 API).
_SUCCESS_CODE = 20000

#: Statuses BCS applies to a *considered and refused* managers PUT —
#: permanent rejections. 429 is deliberately absent: a rate limit
#: cannot prove it refused the request before execution.
_REJECTED_STATUSES = frozenset({400, 401, 403, 404, 409})


@plugin_impl(
    mode=Mode.PROD,
    rationale="trusted-App bearer PUT to the BCS managers endpoint via the Gateway",
)
class HttpBcsManagersPlugin(BcsManagersPlugin):
    """One PUT, one classified verdict — no retries, no leaky errors."""

    def __init__(
        self,
        *,
        base_url: str,
        app_credentials: Mapping[str, str],
        client: httpx.AsyncClient | None = None,
    ) -> None:
        #: Gateway external origin; the adapter owns the fixed path.
        self._base_url = base_url
        #: Backend tenant → trusted-App credential, resolved by the
        #: composition root. Read-only at call time, never recomposed.
        self._app_credentials = app_credentials
        self._client = client
        #: ``client=None`` is the self-managed pool mode: the client is
        #: created lazily on the first async call and owned until
        #: ``aclose()``. An explicit client belongs to its caller.
        self._owns_client = client is None

    async def replace_managers(
        self,
        snapshot: ManagerSnapshot,
        *,
        timeout_seconds: float,
    ) -> ManagerSetChange:
        """Deliver the full replace and return BCS's applied diff."""
        credential = self._app_credentials.get(snapshot.key.avernet_tenant)
        if not credential:
            # No credential, no send: an unauthenticated PUT would be
            # worse than a deferred sync.
            raise BcsManagersError(
                DeliveryFailure.NOT_SENT, "missing_tenant_credential"
            )
        path = (
            "/api/v1/collaboration/bots/"
            + quote(snapshot.bcs_bot_id, safe="")
            + "/managers"
        )
        try:
            # The whole adapter op — client use, response read, and
            # Envelope verification — runs inside the one budget.
            async with asyncio.timeout(timeout_seconds):
                client = self._borrow_client()
                response = await client.put(
                    path,
                    headers={"Authorization": f"Bearer {credential}"},
                    json={"manager_user_ids": list(snapshot.manager_user_ids)},
                    # Mirror the budget onto the wire so httpx's ~5s
                    # phase defaults cannot cap a longer attempt before
                    # the outer backstop (the wall-clock authority) fires.
                    timeout=timeout_seconds,
                )
                return _verify_envelope(response)
        except TimeoutError:
            # Pre- vs post-send is indistinguishable at the budget edge.
            raise BcsManagersError(DeliveryFailure.UNKNOWN, "timeout") from None
        except httpx.ConnectError:
            # The socket was never established: provably not sent.
            raise BcsManagersError(
                DeliveryFailure.NOT_SENT, "connect_error"
            ) from None
        except httpx.ConnectTimeout:
            # Same proof, connect-stage budget exhaustion.
            raise BcsManagersError(
                DeliveryFailure.NOT_SENT, "connect_timeout"
            ) from None
        except httpx.PoolTimeout:
            # No connection was ever acquired from the pool.
            raise BcsManagersError(
                DeliveryFailure.NOT_SENT, "pool_timeout"
            ) from None
        except httpx.ReadTimeout:
            # The request was already on the wire — the outcome is BCS's.
            raise BcsManagersError(
                DeliveryFailure.UNKNOWN, "read_timeout"
            ) from None
        except httpx.WriteTimeout:
            # Possibly delivered even though the write never finished.
            raise BcsManagersError(
                DeliveryFailure.UNKNOWN, "write_timeout"
            ) from None
        except httpx.HTTPError:
            # Disconnects, protocol errors, anything unverifiable.
            raise BcsManagersError(
                DeliveryFailure.UNKNOWN, "transport_error"
            ) from None

    def _borrow_client(self) -> httpx.AsyncClient:
        """The caller-provided client, or the lazily self-owned one."""
        if self._client is None:
            # First async call: the pool is born inside the worker loop.
            self._client = httpx.AsyncClient(base_url=self._base_url)
        return self._client

    async def aclose(self) -> None:
        """Release the delivery pool; a caller-owned client stays open."""
        if self._owns_client and self._client is not None:
            client, self._client = self._client, None
            await client.aclose()


def _verify_envelope(response: httpx.Response) -> ManagerSetChange:
    """Parse BCS's reply into a ``ManagerSetChange`` or raise classified.

    Only the four contract fields are read; nothing from the response
    body travels into the error path.
    """
    status = response.status_code
    if status in _REJECTED_STATUSES:
        raise BcsManagersError(DeliveryFailure.REJECTED, f"http_{status}")
    if status != 200:
        # 429 included: no proof the limit applied before execution.
        raise BcsManagersError(DeliveryFailure.UNKNOWN, f"http_{status}")
    malformed = BcsManagersError(DeliveryFailure.UNKNOWN, "malformed_envelope")
    try:
        payload = response.json()
    except ValueError:
        raise malformed from None
    if not isinstance(payload, dict) or payload.get("code") != _SUCCESS_CODE:
        raise malformed from None
    data = payload.get("data")
    if not isinstance(data, dict):
        raise malformed from None
    if not all(_is_count(data.get(field)) for field in ("added", "removed", "total")):
        raise malformed from None
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise malformed from None
    return ManagerSetChange(
        added=data["added"],
        removed=data["removed"],
        total=data["total"],
        request_id=request_id,
    )


def _is_count(value: object) -> bool:
    """A count is a non-negative real int — ``True`` is not 1."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0