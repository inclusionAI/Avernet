"""Plugin contracts crossed by the Engine materialization service."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from engine.community.core.resource_materialization.models import (
    ChatAttachmentMaterializationRequest,
    MaterializationRequest,
    MaterializationResult,
)


@runtime_checkable
class BaasMaterializationClient(Protocol):
    """Pull one BaaS upload transfer into a caller-owned temporary file.

    ``session_v2`` implementations first request the Session File Sharing
    share-link for the request's tenant/session/transfer identity, then stream
    that short-lived URL into ``destination``. The share-link is a transport
    detail: implementations must not persist or expose it to callers.
    """

    async def pull(
        self,
        request: MaterializationRequest,
        destination: Path,
    ) -> None: ...


@runtime_checkable
class BackendMaterializationCallbackClient(Protocol):
    """Report a terminal materialization result to Backend."""

    async def report(self, result: MaterializationResult) -> None: ...


@runtime_checkable
class TemporaryUrlPullClient(Protocol):
    """Download one validated short-lived capability into a caller-owned file.

    HTTP downloads support at most five 301/302/303/307/308 redirects,
    including relative Locations. Every hop must use HTTP(S) without userinfo,
    resolve exclusively to public IPs, and connect to a validated IP with the
    original Host/SNI. HTTPS must not downgrade to HTTP. Cookies and credentials
    must not propagate between hops. DNS, redirects and body streaming share
    one timeout; the final body remains subject to the request's byte limit.
    """

    async def pull(
        self,
        request: ChatAttachmentMaterializationRequest,
        destination: Path,
    ) -> None: ...
