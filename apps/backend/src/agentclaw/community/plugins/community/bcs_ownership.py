"""One bounded, trusted-App ownership PUT to BCS; no retries or domain policy.

Only transport/envelope validity is checked here. Core decides whether the
returned Bot and Owner confirm the target snapshot. Errors carry fixed codes,
never response bodies or credentials. Injected clients belong to their caller;
otherwise the adapter lazily owns a pooled client on the delivery loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from urllib.parse import quote

import httpx

from agentclaw.community.kernel.bot_manager_sync import DeliveryFailure
from agentclaw.community.kernel.bot_ownership_sync import (
    OwnershipChange,
    OwnershipSnapshot,
)
from agentclaw.community.plugin_api.bcs_ownership import (
    BcsOwnershipError,
    BcsOwnershipPlugin,
)
from agentclaw.community.plugin_api.impl_registry import Mode, plugin_impl

_SUCCESS_CODE = 20000
_AMBIGUOUS_4XX_STATUSES = frozenset({408, 425, 429})


@plugin_impl(mode=Mode.PROD, rationale="direct trusted ownership sync")
class HttpBcsOwnershipPlugin(BcsOwnershipPlugin):
    """Deliver once using a newly minted, tenant-scoped Backend principal."""

    def __init__(
        self,
        *,
        base_url: str,
        mint_principal: Callable[[str], str],
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url
        self._mint_principal = mint_principal
        self._client = client
        self._owns_client = client is None

    async def replace_ownership(
        self,
        snapshot: OwnershipSnapshot,
        *,
        timeout_seconds: float,
    ) -> OwnershipChange:
        """Bound signing, pool setup, HTTP, and parsing by one attempt budget."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        try:
            async with asyncio.timeout(timeout_seconds):
                try:
                    principal = self._mint_principal(snapshot.key.avernet_tenant)
                    if not isinstance(principal, str) or not principal:
                        raise ValueError("missing_principal")
                except Exception:
                    # Every mint failure precedes sending, including signer
                    # timeouts. Cancellation (BaseException) is never absorbed.
                    raise BcsOwnershipError(
                        DeliveryFailure.NOT_SENT, "principal_mint_failed"
                    ) from None
                path = (
                    "/api/v1/collaboration/bots/"
                    + quote(snapshot.bcs_bot_id, safe="")
                    + "/ownership"
                )
                client = self._borrow_client()
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError
                response = await client.put(
                    path,
                    headers={"X-Avernet-Principal": principal},
                    json={"owner_user_id": snapshot.owner_user_id},
                    timeout=remaining,
                )
                return _verify_envelope(response)
        except TimeoutError:
            raise BcsOwnershipError(DeliveryFailure.UNKNOWN, "timeout") from None
        except httpx.ConnectError:
            raise BcsOwnershipError(DeliveryFailure.NOT_SENT, "connect_error") from None
        except httpx.ConnectTimeout:
            raise BcsOwnershipError(
                DeliveryFailure.NOT_SENT, "connect_timeout"
            ) from None
        except httpx.PoolTimeout:
            raise BcsOwnershipError(DeliveryFailure.NOT_SENT, "pool_timeout") from None
        except httpx.ReadTimeout:
            raise BcsOwnershipError(DeliveryFailure.UNKNOWN, "read_timeout") from None
        except httpx.WriteTimeout:
            raise BcsOwnershipError(DeliveryFailure.UNKNOWN, "write_timeout") from None
        except httpx.HTTPError:
            raise BcsOwnershipError(
                DeliveryFailure.UNKNOWN, "transport_error"
            ) from None

    def _borrow_client(self) -> httpx.AsyncClient:
        """Create an owned pool only on first delivery, on the delivery loop."""
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._base_url)
        return self._client

    async def aclose(self) -> None:
        """Release an owned pool once; never close the caller's client."""
        if self._owns_client and self._client is not None:
            client, self._client = self._client, None
            await client.aclose()


def _verify_envelope(response: httpx.Response) -> OwnershipChange:
    """Validate the v1 response shape without asserting target alignment."""
    status = response.status_code
    if 400 <= status < 500 and status not in _AMBIGUOUS_4XX_STATUSES:
        raise BcsOwnershipError(DeliveryFailure.REJECTED, f"http_{status}")
    if status != 200:
        raise BcsOwnershipError(DeliveryFailure.UNKNOWN, f"http_{status}")
    malformed = BcsOwnershipError(DeliveryFailure.UNKNOWN, "malformed_envelope")
    try:
        body = response.json()
    except ValueError:
        raise malformed from None
    if (
        not isinstance(body, dict)
        or type(body.get("code")) is not int
        or body["code"] != _SUCCESS_CODE
        or not isinstance(body.get("message"), str)
    ):
        raise malformed
    data = body.get("data")
    required = {"bot_id", "owner_user_id", "previous_owner_user_id", "changed"}
    if not isinstance(data, dict) or not required <= data.keys():
        raise malformed
    if not _valid_id(body.get("request_id")) or not _valid_id(data["bot_id"]):
        raise malformed
    owner = data["owner_user_id"]
    previous = data["previous_owner_user_id"]
    changed = data["changed"]
    if type(changed) is not bool:
        raise malformed
    if owner is not None and not _valid_id(owner):
        raise malformed
    if previous is not None and not _valid_id(previous):
        raise malformed
    if changed and (owner is None or previous is None or owner == previous):
        raise malformed
    if not changed and previous is not None:
        raise malformed
    return OwnershipChange(data["bot_id"], owner, previous, changed, body["request_id"])


def _valid_id(value: object) -> bool:
    """IDs must be nonempty and unpadded; do not silently normalize them."""
    return isinstance(value, str) and bool(value) and value == value.strip()
