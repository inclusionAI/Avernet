"""The compiler contract: what turns something else into a ``DesiredState``.

An apply run starts from a **state reference**: a producer's *kind* and the
producer's own *reference* within it. The run service looks the kind up in
the compiler map it was built with — each producer's module contributes its
own row — and re-compiles from the reference in the worker; no serialised
state travels, the task payload carries the reference only. The engine names
no kind: what ``"manifest"`` or ``"genome"`` means is the business of the
module that registered it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Collection, Dict, Mapping, Protocol, runtime_checkable

from agentclaw.community.core.apply_engine.constructs import ApplyConstruct

from agentclaw.community.core.apply_engine.desired_state import DesiredState

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agentclaw.community.core.apply_engine.context import ApplyContext
    from agentclaw.community.core.apply_engine.outcomes import SourceResolution

__all__ = [
    "Compiled",
    "CompileRefused",
    "DesiredStateCompiler",
    "LEGACY_STATE",
    "StateCompilers",
    "StateRef",
    "state_ref_from_payload",
]


@dataclass(frozen=True)
class StateRef:
    """What an apply run converges a bot to: a producer's kind, and the producer's reference within it.

    ``kind`` is the name a compiler registered under (``StateCompilers`` is
    keyed by it). ``reference`` is that producer's own identifier for the
    state — ``None`` for a kind with exactly one state per bot, where there
    is nothing to pick. The two producers today::

        StateRef("manifest")                 # the bot's stored configuration document
        StateRef("genome", promotion_id)     # the revision a promotion delivers

    Each is built by its own module (the Manifest's ``MANIFEST_STATE``, the
    genome's ``genome_state(...)``); the engine only carries them.
    """

    kind: str
    reference: str | None = None

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("a state reference needs a kind")

    def to_payload(self) -> dict[str, Any]:
        """The wire shape, in a task payload and a stored report: ``{"kind": ..., "reference": ...}``."""
        return {"kind": self.kind, "reference": self.reference}


#: What an apply recorded before reports and task payloads carried a reference is read as. There was one
#: producer then — the bot's stored configuration document — so an absent reference means that kind. Read on
#: the legacy decode path only, and the default a report is born with before the run service stamps the
#: reference it applied; every record the service writes says what it applied.
LEGACY_STATE = StateRef("manifest")


def state_ref_from_payload(payload: Mapping[str, Any] | None) -> StateRef:
    """The reference a task payload or a persisted report carries; absent is ``LEGACY_STATE``."""
    if payload is None:
        return LEGACY_STATE
    kind = payload.get("kind")
    if not isinstance(kind, str) or not kind:
        raise ValueError(f"a state reference needs a kind: {payload!r}")
    reference = payload.get("reference")
    return StateRef(kind, str(reference) if reference is not None else None)


@dataclass(frozen=True)
class Compiled:
    """What a compiler answered: the state, and the git sources it resolved on the way (Manifest only)."""

    state: DesiredState
    sources: tuple[SourceResolution, ...] = ()


class CompileRefused(Exception):
    """A compiler refuses the reference as a whole, for a reason the report should say.

    Not a bug: the reference named something that cannot be compiled *now* —
    a promotion whose ref has moved on, a revision the state model no longer
    accepts, a document that stopped validating. In the apply worker the run
    service records the apply as failed with the message as a note on the
    report (an engine exception is recorded without its text, since that is
    where raw internals could reach a response body; this message is composed
    by the compiler for the caller to read). It carries the compiler's own
    ``domain_error`` — the one its HTTP surface maps — so a caller that
    answers a request (a dry run) raises that instead.
    """

    def __init__(self, message: str, domain_error: Exception) -> None:
        # Both in ``args``, so copy and pickle rebuild it through ``cls(*args)``; pickling additionally
        # needs the domain error itself to be picklable, which is its own class's concern.
        super().__init__(message, domain_error)
        self.message = message
        self.domain_error = domain_error

    def __str__(self) -> str:
        return self.message


@runtime_checkable
class DesiredStateCompiler(Protocol):
    """Compile one kind of state reference into a desired state.

    Fetching, grammar and content filing all happen inside ``compile``; what
    comes out names bytes already in the content store. A per-entry problem is
    an ``EntryFailure`` on its declaration; a reference that cannot be
    compiled at all is ``CompileRefused``, recorded with its reason; any other
    exception is a bug and propagates, which the run service records as a
    failed apply.
    """

    def check(self, ctx: ApplyContext, ref: StateRef) -> None:
        """Refuse now what ``compile`` would refuse later, so the caller hears it before an apply id exists.

        Synchronous and cheap: the Manifest compiler re-validates the stored
        document (the engine may have changed since it was accepted), the
        genome compiler checks the promotion is there to deliver and still
        current — the one its ref points at, in a status it knows. Raises
        the compiler's own domain error (the one its HTTP surface maps); the
        same refusal from ``compile``, later, is ``CompileRefused``. Returns
        ``None`` when nothing stands in the way. Fetches nothing. What
        ``check`` accepted can still be refused by ``compile``: the bot and
        its refs move between the two.
        """
        ...

    async def compile(
        self, ctx: ApplyContext, ref: StateRef, *, only: Collection[ApplyConstruct] | None = None
    ) -> Compiled:
        """The desired state the reference names, fetched and filed.

        ``only`` narrows the compile to a subset of constructs. The one caller
        that passes it is bot creation, which applies in two phases: the
        pre-container phase can deliver only the startup script (it is baked
        into the container's start command), and the on-container phase
        delivers everything else once the container runs. Each phase compiles
        with ``only`` = its own constructs, so the pre-container phase spends
        no fetch budget on skills it cannot deliver yet and reports no failure
        for them; the compiler leaves every other construct undeclared in the
        state. ``None`` — every other caller — compiles every construct the
        reference declares.
        """
        ...


#: The run service's lookup: one compiler per reference type. A map
#: multibinding, so each compiler's module contributes its own row.
StateCompilers = Dict[str, DesiredStateCompiler]
