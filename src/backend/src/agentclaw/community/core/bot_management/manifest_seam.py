"""What bot creation needs from the manifest layer, stated without importing it.

Its own module rather than a declaration inside ``create_flow``: that file is
already at the size the architecture cap allows, and a contract is a different
kind of thing from the flow that calls it. It is also what the implementation
imports to declare itself — see the class docstring — so keeping it free of
everything but ``typing`` is what lets that import stay one-directional.

The reason it is a ``Protocol`` at all is the import cycle — again, see the
class docstring.
"""

from __future__ import annotations

from typing import Any, Protocol


class ManifestCreationSeam(Protocol):
    """What creation needs from the manifest package, named without importing it.

    A ``Protocol`` rather than the concrete ``BotCreationManifestSeam``:
    ``core/bot_management`` must not import ``core/bot_config_manifest`` — that
    closes a cycle, since the manifest package reaches back into the creation
    graph — and a Protocol states the contract with no import at all. A test
    double satisfies it by shape, with no base class and no import, which is the
    second reason.

    **The one real implementation says so out loud.**
    ``BotCreationManifestSeam`` inherits this explicitly. That import runs
    manifest → management, the direction that is allowed and that the manifest
    package already takes; only the reverse closes the cycle. It buys what
    conformance-by-shape alone never did: the contract is checked against *this*
    file rather than against whichever call site happens to pass the seam here,
    and a reader or an IDE can walk between the contract and its implementation
    instead of guessing which class fits. The checking is a type checker's in an
    editor and ``test_creation_seam``'s in CI — the suite pins both the
    inheritance and the four signatures, because no type checker runs on this
    tree.

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
