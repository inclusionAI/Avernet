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
from typing import TYPE_CHECKING, Any, ClassVar, Dict, Mapping, Protocol, TypeVar, runtime_checkable

from agentclaw.community.core.apply_engine.desired_state import DesiredState

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agentclaw.community.core.apply_engine.context import ApplyContext
    from agentclaw.community.core.apply_engine.outcomes import SourceResolution

__all__ = [
    "Compiled",
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


@runtime_checkable
class DesiredStateCompiler(Protocol[R]):
    """Compile one kind of state reference into a desired state.

    Fetching, grammar and content filing all happen inside ``compile``; what
    comes out names bytes already in the content store. A per-entry problem is
    an ``EntryFailure`` on its declaration; an exception is a bug and
    propagates, which the run service records as a failed apply.
    """

    def check(self, ctx: ApplyContext, ref: R) -> None:
        """Refuse now what ``compile`` would refuse later, so the caller hears it before an apply id exists.

        Synchronous and cheap: the Manifest compiler re-validates the stored
        document (the engine may have changed since it was accepted), the
        genome compiler checks the promotion is there to deliver. Raises the
        same error ``compile`` would; returns ``None`` when nothing stands in
        the way. Fetches nothing.
        """
        ...

    async def compile(self, ctx: ApplyContext, ref: R) -> Compiled: ...


#: The run service's lookup: one compiler per reference type. A map
#: multibinding, so each compiler's module contributes its own row.
StateCompilers = Dict[type, DesiredStateCompiler]
