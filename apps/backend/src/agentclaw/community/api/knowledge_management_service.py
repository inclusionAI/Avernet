"""Authenticated, read-only TC knowledge management facade."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class KnowledgeManagementServiceProtocol(Protocol):
    def list_in_space(
        self, *, space_id: int, actor_id: str, cursor: str, limit: int
    ) -> dict:
        """List readable knowledge-base cards in a space."""
        ...

    def detail(self, *, space_id: int, kb_id: str, actor_id: str) -> dict:
        """Read a knowledge-base card under native access checks."""
        ...

    def authorize_space(self, *, space_id: int, actor_id: str) -> None:
        """Gate a request on local space membership; the provider enforces the rest."""
        ...

    def authorized_bots(
        self, *, space_id: int, kb_id: str, actor_id: str, cursor: int, limit: int
    ) -> dict:
        """Aggregate authorized Bot metadata and saved enablement."""
        ...

    def candidates(
        self,
        *,
        space_id: int,
        kb_id: str,
        actor_id: str,
        cursor: int,
        limit: int,
        keyword: str,
    ) -> dict:
        """List same-space Bot candidates available to a manager."""
        ...

    def grant_states(
        self,
        *,
        space_id: int,
        kb_id: str,
        actor_id: str,
        identities: list[tuple[str, str]],
    ) -> dict:
        """Read authoritative CAS states, including revoked grants."""
        ...

    def set_grant(self, **kwargs) -> dict:
        """Set or revoke a Bot grant with canonical version checks."""
        ...

    def available_for_bot(self, **kwargs) -> dict:
        """List authorized knowledge bases and current Bot enablement."""
        ...
