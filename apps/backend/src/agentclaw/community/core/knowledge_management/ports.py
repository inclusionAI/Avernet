"""Outbound catalog and display ports; no concrete transport or HTTP status."""

from typing import Protocol


class KnowledgeCatalog(Protocol):
    def list_in_space(
        self, *, space_id: int, actor_id: str, cursor: str, limit: int
    ) -> dict:
        """List readable knowledge bases in the requested space."""
        ...

    def detail(self, *, space_id: int, kb_id: str, actor_id: str) -> dict:
        """Read canonical knowledge-base metadata and access permissions."""
        ...

    def page_authorized(self, *, scope, space_id: int, cursor: int, limit: int) -> dict:
        """Read at most limit authorized IDs and an opaque integer next_cursor.

        Cursor zero starts a page sequence; consumers forward it unchanged,
        never add a page size, and never prefetch the legacy complete list.
        space_id must be a positive int; invalid ids fail locally as
        INVALID_KNOWLEDGE_INPUT without provider I/O.
        """
        ...

    def list_authorized(self, *, scope, space_id: int) -> tuple[str, ...]:
        """List knowledge bases authorized for one composite Bot identity."""
        ...

    def details(self, *, space_id: int, kb_ids: list[str], actor_id: str) -> list[dict]:
        """Read one ordered, complete batch of up to 20 unique IDs.

        Empty input returns no details. Any denied, missing or mismatched item
        fails the complete batch; callers never receive partial metadata.
        """
        ...

    def authorized_bots(
        self, *, space_id: int, kb_id: str, actor_id: str, cursor: int, limit: int
    ) -> dict:
        """List current Bot grants with pagination."""
        ...

    def grant_states(
        self, *, kb_id: str, actor_id: str, identities: list[tuple[str, str]], env: str
    ) -> dict:
        """Read authoritative CAS states, including revoked grants."""
        ...

    def set_grant(self, **kwargs) -> dict:
        """Write a Bot grant through the canonical knowledge service."""
        ...


class KnowledgeDisplaySource(Protocol):
    platform: str

    def scope(self, space_id: int, actor_id: str) -> dict:
        """Resolve the current user and space display scope."""
        ...

    def display(self, space_id: int, identities: list[tuple[str, str]]) -> dict:
        """Read local metadata for the supplied composite Bot identities."""
        ...

    def candidates(
        self, space_id: int, *, cursor: int, limit: int, keyword: str
    ) -> dict:
        """Page through eligible Bots in one space."""
        ...

    def bot_context(self, scope, actor_id) -> dict:
        """Resolve native Bot edit context for the current actor."""
        ...

    def conversation_bot(self, scope) -> dict:
        """Resolve a live composite Bot and its space, without an Editor grant.

        Consumers must establish native conversation access before reading assets.
        """
        ...

    def has_enabled_selection(self, *, bot) -> bool:
        """Read whether this exact tenant/env/Bot/space has any enabled selection.

        Consumers must establish conversation access before querying this state.
        This is a bounded local existence check, not provider authorization.
        """
        ...

    def selections(self, *, bot, kb_ids) -> dict:
        """Read persisted selections for the specified Bot and bases."""
        ...


class KnowledgeEnablementReader(Protocol):
    def read(self, *, space_id: int, kb_id: str, bots: dict) -> dict:
        """Read enablement values for the supplied authorized Bots."""
        ...


class KnowledgeIdentityResolver(Protocol):
    def staff_no(self, user_id: str) -> str:
        """Resolve the authenticated platform identity for the configured service."""
        ...
