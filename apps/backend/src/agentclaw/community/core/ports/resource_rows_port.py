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
from typing import Optional, Protocol, Sequence, runtime_checkable


@runtime_checkable
class ResourceRowsPort(Protocol):
    """The resource rows of one bot, replaced under the resources root."""

    @abstractmethod
    async def replace_manifest_rows(
        self,
        *,
        files: Sequence[tuple[str, int]],
        owner_id: str,
        created_by: Optional[str],
    ) -> None:
        """Replace the bot's file rows under ``data/`` with one row per ``(path, size)``, source ``manifest``.

        Deletes every file row under the root first, then inserts the given
        rows, owned by ``owner_id`` (the bot's owner, whose console listing
        these rows are) and stamped ``created_by`` (the actor who applied).
        A failure raises: a row write that did not land is never reported as
        success.
        """
        ...


__all__ = ["ResourceRowsPort"]
