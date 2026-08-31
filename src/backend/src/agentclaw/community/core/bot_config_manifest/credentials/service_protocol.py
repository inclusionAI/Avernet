"""Service API Protocol for tenant source credentials (W3, #1471).

Re-exported for adapters by ``api/source_credential_service.py``; defined
here in the owning core module so the concrete service inherits it without
a ``core -> api`` waiver — the repo's established Protocol placement.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.bot_config_manifest.credentials.models import (
        SourceCredentialRecord,
    )
    from agentclaw.community.core.bot_config_manifest.credentials.service import (
        SourceCredentialBinding,
    )


@runtime_checkable
class SourceCredentialServiceProtocol(Protocol):
    """Register, rotate, read (masked), and remove named tenant credentials.

    The profile's fail-closed posture is DI state, not a per-call argument:
    production binds a service that refuses writes without a master key
    (the guard lives in the constructor), while singlebox binds one that
    stores what it can. Callers never declare the profile.
    """

    @abstractmethod
    def put(
        self,
        *,
        name: str,
        header_name: str,
        secret: str,
        allowed_prefixes: list[str],
        credential_type: str = "header",
        modifier: str = "",
    ) -> "SourceCredentialRecord":
        """Validate and store-or-rotate. Raises on the first invalid input.

        Rotation is the same call — same name, new value — and never
        triggers an apply; presentation happens per fetch hop.
        """
        ...

    @abstractmethod
    def get(self, *, name: str) -> "SourceCredentialRecord":
        """Masked metadata for one name; 404-shaped error when absent."""
        ...

    @abstractmethod
    def list_credentials(self) -> "list[SourceCredentialRecord]":
        """Masked metadata for every credential in the request's tenant."""
        ...

    @abstractmethod
    def delete(self, *, name: str) -> bool:
        """Remove by name; idempotent (``False`` when absent).

        Deleting a referenced credential is allowed: the referencing
        entries fail their next fetch with the name, which is the
        apply-layer record, not a storage constraint.
        """
        ...

    @abstractmethod
    def binding(self, *, name: str) -> "SourceCredentialBinding":
        """Injector/policy pair for the W2 fetcher, bound to one name.

        The binding reads the credential per hop — headers under the
        stored name, prefix authorization against the stored prefixes —
        so rotation takes effect on the next fetch without any signal.
        """
        ...
