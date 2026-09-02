"""What bot creation needs from the manifest layer, stated without importing it.

Its own module rather than a declaration inside ``create_flow``: that file is
already at the size the architecture cap allows, and a contract is a different
kind of thing from the flow that calls it. The reason it is a ``Protocol`` at
all is the import cycle — see the class docstring.
"""

from __future__ import annotations

from typing import Any, Protocol


class ManifestCreationSeam(Protocol):
    """What creation needs from the manifest package, named without importing it.

    A ``Protocol`` rather than the concrete ``BotCreationManifestSeam``:
    ``core/bot_management`` must not import ``core/bot_config_manifest`` — that
    closes a cycle, since the manifest package reaches back into the creation
    graph — and structural typing states the contract with no import at all. The
    real seam satisfies it by shape; a test double satisfies it by shape too,
    which is the second reason.

    Only the four operations submission calls are here. The pre-container apply
    and the job's own steps belong to the creation job, which holds the seam
    directly and needs no stand-in for it.
    """

    def preflight(
        self, *, document: str, engine_type: str | None, bot_type: str | None
    ) -> dict[str, Any]:
        """Refuse an unusable manifest now. Raises ``ManifestValidationError``."""
        ...

    def persist(
        self,
        *,
        spec_entity_id: str,
        user_id: str,
        bot_id: str,
        document: str,
        modifier: str,
        engine_type: str | None,
        bot_type: str | None,
    ) -> str:
        """Store the document and return the ``entity_id`` it was keyed by."""
        ...

    def start_job(
        self,
        *,
        bot_id: str,
        entity_id: str,
        user_id: str,
        document_owner: str,
        spec: dict[str, Any],
        iframe_url: str | None,
        redirect_url: str | None,
    ) -> None:
        """Hand the creation to its durable job."""
        ...

    def discard(self, *, entity_id: str, bot_id: str) -> bool:
        """Undo what submission wrote. Never raises; reports whether it landed."""
        ...


__all__ = ["ManifestCreationSeam"]
