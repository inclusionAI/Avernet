"""The manifest layer's creation seam, stated without importing it.

Its own module rather than a declaration inside ``create_flow``: that file is
already at the size the architecture cap allows, a contract is a different kind
of thing from the flow that calls it, and ``create_flow`` is only one of the
three callers. It is also what the implementation imports to declare itself and
what the DI container binds, so it stays free of everything but ``typing`` and
one value type — that is what lets the import stay one-directional and the
module stay cheap to import.

The reason it is a ``Protocol`` at all is the import cycle — see the class
docstring.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.task_queue.types import TaskRecord


@runtime_checkable
class ManifestCreationSeam(Protocol):
    """Everything bot creation asks of the manifest package, named without it.

    A ``Protocol`` rather than the concrete ``BotCreationManifestSeam``:
    ``core/bot_management`` must not import ``core/bot_config_manifest`` — that
    closes a cycle, since the manifest package reaches back into the creation
    graph — and a Protocol states the contract with no import at all. A test
    double satisfies it by shape, with no base class and no import, which is the
    second reason.

    **This is the type the container binds and every consumer holds.** The
    concrete class is constructed in one place, the DI provider that wires its
    collaborators; everywhere else — ``submit_bot_creation_with_manifest``, the
    two ``with-manifest`` routes, the creation job's handler — names this. A
    consumer that held the class instead would be depending on how the seam is
    built rather than on what it promises, and would drag the whole manifest
    package into modules that need six method signatures.

    ``@runtime_checkable`` follows from that binding and nothing else:
    python-injector ``isinstance``-checks an instance against the key it is
    bound to, so rebinding this key to a stand-in — which the endpoint suite
    does — raises on a plain ``Protocol``. It buys no guarantee worth having on
    its own, since the check is attribute presence and never a signature; that
    is what the implementation's declaration and ``test_creation_seam`` are for.

    **The one real implementation says so out loud.**
    ``BotCreationManifestSeam`` inherits this explicitly. That import runs
    manifest → management, the direction that is allowed and that the manifest
    package already takes; only the reverse closes the cycle. It buys what
    conformance-by-shape alone never did: the contract is checked against *this*
    file rather than against whichever call site happens to pass the seam, and a
    reader or an IDE can walk between the contract and its implementation
    instead of guessing which class fits. The checking is a type checker's in an
    editor and ``test_creation_seam``'s in CI — the suite pins both the
    inheritance and the signatures, because no type checker runs on this tree.

    **Why all six and not only submission's four.** ``preflight``, ``persist``,
    ``start_job`` and ``discard`` are what submission calls; ``apply_pre_container``
    is the creation job's and ``find_job`` the poll's. They are one contract
    because they are served by one object with one lifetime, handed out under
    one binding: naming only submission's would leave the job and the route
    holding the concrete class, which is the coupling this exists to remove.
    ``create_flow`` calling four of six is the ordinary shape of a caller that
    does not need everything, not a reason to split the seam in two.
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

    def apply_pre_container(
        self,
        *,
        entity_id: str,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        engine_type: str | None,
        bot_type: str | None,
        bot: dict[str, Any] | None = None,
    ) -> str | None:
        """Run the pre-container phase. Never raises; ``None`` if it never started."""
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

    def find_job(self, *, entity_id: str, bot_id: str) -> TaskRecord | None:
        """This creation's task row, or ``None`` if none was submitted."""
        ...

    def discard(
        self, *, entity_id: str, bot_id: str, owner_id: str | None = None
    ) -> bool:
        """Undo what submission wrote. Never raises; reports whether it landed."""
        ...


__all__ = ["ManifestCreationSeam"]
