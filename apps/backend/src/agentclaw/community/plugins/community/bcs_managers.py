"""One direct BCS full-replace PUT authenticated as the Backend service.

The composition root supplies a tenant-scoped service-principal minter. This
adapter owns HTTP/envelope parsing, never retries, and never logs credentials or
response bodies. The consumer bounds retries, including uncertain transport
outcomes, under the explicitly best-effort contract.

NOT_SENT means connect/pool/mint failure; REJECTED means a permanent 4xx refusal;
408/425/429 and all other transport/status/envelope failures are UNKNOWN. UNKNOWN
does not imply that BCS failed to apply the write and does not permanently block
future synchronization. A supplied client belongs to its caller; otherwise this
plugin lazily owns one pooled AsyncClient on the worker loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from urllib.parse import quote

import httpx

from agentclaw.community.core.gateway_principal.errors import (
    PrincipalVerificationError,
)
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

#: Transient/ambiguous client statuses. Every other 4xx is a considered,
#: permanent refusal for this fixed PUT. 408/425/429 do not prove whether a
#: request was applied, so the consumer may retry them under its shared budget.
_AMBIGUOUS_4XX_STATUSES = frozenset({408, 425, 429})


@plugin_impl(
    mode=Mode.PROD,
    rationale="self-signed BCS principal PUT direct to the managers endpoint",
)
class HttpBcsManagersPlugin(BcsManagersPlugin):
    """One PUT, one classified verdict — no retries, no leaky errors."""

    def __init__(
        self,
        *,
        base_url: str,
        mint_principal: Callable[[str], str],
        client: httpx.AsyncClient | None = None,
    ) -> None:
        #: BCS origin; the adapter owns the fixed authority path.
        self._base_url = base_url
        #: Backend tenant → a self-signed ``X-Avernet-Principal`` for
        #: this component's service identity. Handed in by the
        #: composition root; the adapter never sees key material.
        self._mint_principal = mint_principal
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
        try:
            principal = self._mint_principal(snapshot.key.avernet_tenant)
            if not isinstance(principal, str) or not principal:
                raise PrincipalVerificationError("principal minter returned no token")
        except Exception:
            # Any mint failure is necessarily pre-send. Keep provider details out
            # of the contract and preserve the Plugin API's classified-error rule.
            raise BcsManagersError(
                DeliveryFailure.NOT_SENT, "principal_mint_failed"
            ) from None
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
                    headers={"X-Avernet-Principal": principal},
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
            raise BcsManagersError(DeliveryFailure.NOT_SENT, "connect_error") from None
        except httpx.ConnectTimeout:
            # Same proof, connect-stage budget exhaustion.
            raise BcsManagersError(
                DeliveryFailure.NOT_SENT, "connect_timeout"
            ) from None
        except httpx.PoolTimeout:
            # No connection was ever acquired from the pool.
            raise BcsManagersError(DeliveryFailure.NOT_SENT, "pool_timeout") from None
        except httpx.ReadTimeout:
            # The request was already on the wire — the outcome is BCS's.
            raise BcsManagersError(DeliveryFailure.UNKNOWN, "read_timeout") from None
        except httpx.WriteTimeout:
            # Possibly delivered even though the write never finished.
            raise BcsManagersError(DeliveryFailure.UNKNOWN, "write_timeout") from None
        except httpx.HTTPError:
            # Disconnects, protocol errors, anything unverifiable.
            raise BcsManagersError(DeliveryFailure.UNKNOWN, "transport_error") from None

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
    if 400 <= status < 500 and status not in _AMBIGUOUS_4XX_STATUSES:
        raise BcsManagersError(DeliveryFailure.REJECTED, f"http_{status}")
    if status != 200:
        # 408/425/429 included: no proof the response precedes execution.
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
