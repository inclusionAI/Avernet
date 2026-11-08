"""``ResourceRowsPort``: the one write the ``resources`` materialiser makes to the platform's resource rows.

A port because the console's resource listing (``ac_resource``) is built
from these rows and the materialiser is the second writer of them: after it
has rewritten the resources root it replaces the bot's rows under that root
with one per written file, so the listing and the publish pipeline see what
the apply delivered. Narrower than ``ResourceServiceProtocol`` on purpose:
the materialiser needs this one operation and nothing of the CRUD surface.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class WrittenFile:
    """One file the apply wrote in the workspace, as its row will describe it."""

    #: Workspace-relative, root included: ``"data/kb/faq.md"``.
    path: str
    #: The bytes written, which the console listing shows as the file's size.
    size: int


@runtime_checkable
class ResourceRowsPort(Protocol):
    """The resource rows of one bot, replaced in the workspace."""

    @abstractmethod
    async def replace_rows(
        self,
        *,
        files: Sequence[WrittenFile],
        owner_id: str,
        created_by: Optional[str],
    ) -> None:
        """Replace the bot's file rows of the workspace with one row per written file.

        Deletes every file row under the root first, then inserts one row per
        ``files`` member. ``owner_id`` is the bot's owner: the rows are that
        user's console listing, so they are owned by the owner whoever
        applied. ``created_by`` is the actor who ran the apply, stamped on each
        row for the audit trail (``None`` when the apply had no acting user,
        e.g. a job). A failure raises: a row write that did not land is never
        reported as success.
        """
        ...


__all__ = ["ResourceRowsPort", "WrittenFile"]
