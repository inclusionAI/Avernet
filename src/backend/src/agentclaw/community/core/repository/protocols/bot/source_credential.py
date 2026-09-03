"""Tenant source credential repository contract (W3, #1471).

Every member is ``@abstractmethod``; domain imports are TYPE_CHECKING-only
(see ``core/repository/README.md`` for the load-bearing direction).
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Optional, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.bot_config_manifest.credentials.models import (
        CredentialType,
        SourceCredentialRow,
    )


@runtime_checkable
class SourceCredentialRepositoryProtocol(Protocol):
    """Keyed on ``name`` within the request's tenant (the guard carries it).

    No ``env`` axis: the credential is a tenant-level object — see the DDL
    for the reasoning. Upsert is the only write: a re-PUT under the same
    name rotates the value in place.
    """

    @abstractmethod
    def get(self, *, name: str) -> Optional[SourceCredentialRow]:
        """The row for this tenant's ``name``, or ``None``."""
        ...

    @abstractmethod
    def list(self) -> list[SourceCredentialRow]:
        """Every row in the tenant, ordered by name."""
        ...

    @abstractmethod
    def upsert(
        self,
        *,
        name: str,
        credential_type: CredentialType,
        header_name: str,
        allowed_prefixes: list[str],
        secret_ciphertext: str,
        owner_app_id: int,
        modifier: str,
    ) -> SourceCredentialRow:
        """Insert, or whole-replace the row with the same name (rotation).

        Params (beyond the keyed ones the row itself documents):

        - ``credential_type`` — the validated presentation mechanism; the
          service has already refused reserved/unknown values.
        - ``allowed_prefixes`` — the validated list; storage encodes it as
          the row's JSON array form.
        - ``secret_ciphertext`` — TokenVault output, twice over: already
          *encrypted* (or singlebox plaintext), never the raw secret —
          no plaintext secret reaches this layer under any profile.
        - ``owner_app_id`` — the creating application's registry id.
          Stamped on insert and never re-stamped on replace; the *owner*
          gate itself (refuse a non-owner's rotation before storage) is
          the service's, which reads the row first.
        - ``modifier`` — the audit actor the router composed off the
          verified principal (``app:<id>`` / ``app:<id>:on-behalf-of:<user>``).
        """
        ...

    @abstractmethod
    def delete(self, *, name: str) -> bool:
        """Hard-delete by name. Idempotent — ``False`` when absent."""
        ...
