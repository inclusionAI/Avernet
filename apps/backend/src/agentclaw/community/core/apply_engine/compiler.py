"""The compiler contract: what turns something else into a ``DesiredState``.

An apply run starts from a **state reference**, typed: ``ManifestStateRef``
(the bot's stored Manifest document; a bot has one, so it carries nothing) or
``GenomeStateRef`` (the revision the named promotion delivers). The run
service picks the compiler by the reference's type and re-compiles from it in
the worker; no serialised state travels, the task payload carries the
reference's identifiers only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Collection, Dict, Mapping, Protocol, TypeVar, runtime_checkable

from agentclaw.community.core.apply_engine.constructs import ApplyConstruct

from agentclaw.community.core.apply_engine.desired_state import DesiredState

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agentclaw.community.core.apply_engine.context import ApplyContext
    from agentclaw.community.core.apply_engine.outcomes import SourceResolution

__all__ = [
    "Compiled",
    "CompileRefused",
    "DesiredStateCompiler",
    "GenomeStateRef",
    "ManifestStateRef",
    "StateCompilers",
    "StateRef",
    "state_ref_from_payload",
]


@dataclass(frozen=True)
class ManifestStateRef:
    """The bot's stored Manifest document."""

    kind: ClassVar[str] = "manifest"

    def to_payload(self) -> dict[str, Any]:
        return {"kind": self.kind}


@dataclass(frozen=True)
class GenomeStateRef:
    """The revision the named promotion delivers (the promotion row holds the revision id)."""

    kind: ClassVar[str] = "genome"
    promotion_id: str

    def __post_init__(self) -> None:
        if not self.promotion_id:
            raise ValueError("a genome state reference needs a promotion id")

    def to_payload(self) -> dict[str, Any]:
        return {"kind": self.kind, "promotion_id": self.promotion_id}


StateRef = ManifestStateRef | GenomeStateRef


def state_ref_from_payload(payload: Mapping[str, Any] | None) -> StateRef:
    """The reference a task payload or a persisted report carries; absent means the Manifest."""
    if payload is None:
        return ManifestStateRef()
    kind = payload.get("kind")
    if kind == ManifestStateRef.kind:
        return ManifestStateRef()
    if kind == GenomeStateRef.kind:
        return GenomeStateRef(promotion_id=str(payload.get("promotion_id") or ""))
    raise ValueError(f"unknown state reference kind: {kind!r}")


@dataclass(frozen=True)
class Compiled:
    """What a compiler answered: the state, and the git sources it resolved on the way (Manifest only)."""

    state: DesiredState
    sources: tuple[SourceResolution, ...] = ()


R = TypeVar("R", ManifestStateRef, GenomeStateRef, contravariant=True)


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
        # Both in ``args`` so the exception copies and pickles as any other.
        super().__init__(message, domain_error)
        self.message = message
        self.domain_error = domain_error

    def __str__(self) -> str:
        return self.message


@runtime_checkable
class DesiredStateCompiler(Protocol[R]):
    """Compile one kind of state reference into a desired state.

    Fetching, grammar and content filing all happen inside ``compile``; what
    comes out names bytes already in the content store. A per-entry problem is
    an ``EntryFailure`` on its declaration; a reference that cannot be
    compiled at all is ``CompileRefused``, recorded with its reason; any other
    exception is a bug and propagates, which the run service records as a
    failed apply.
    """

    def check(self, ctx: ApplyContext, ref: R) -> None:
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

    async def compile(self, ctx: ApplyContext, ref: R, *, only: Collection[ApplyConstruct] | None = None) -> Compiled:
        """The desired state the reference names, fetched and filed.

        ``only`` is the set of constructs this run will apply — a creation
        phase's half — and a compiler leaves every other construct out of the
        state, declared or not, so no fetch is spent on a construct the run
        cannot touch and no failure is reported for one it will not reach.
        ``None`` compiles every declared construct.
        """
        ...


#: The run service's lookup: one compiler per reference type. A map
#: multibinding, so each compiler's module contributes its own row.
StateCompilers = Dict[type, DesiredStateCompiler]
