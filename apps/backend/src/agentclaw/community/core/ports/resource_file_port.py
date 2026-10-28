"""A bot's workspace resource files, named by what the caller needs of them.

An outbound port (see this package's README): the ``resources`` materialiser
uploads, deletes, probes, lists and clears a bot's workspace files, and this
names exactly those operations.

**It is a narrowing.** ``ResourceFileService`` has more public methods —
``list_dir`` (the console's projected listing), ``read_file``,
``iter_directory_files``, ``create_directory`` alongside these. The
materialiser needs five; handed the service it could reach the rest.

**Two implementations, split on where the write lands** — the same axis the
delivery families split on everywhere else:

* ``DeviceResource`` (ARCA) forwards to ``ResourceFileService``, which writes
  the file into the bot's live container.
* ``PlatformResource`` (platform-managed teclaw) writes objects under the
  store's ``workspace`` namespace and never touches a container; the composed
  artifact is the delivery.

**The port knows no policy.** ``list_entries`` lists what is there and
``delete_dir`` deletes what it is told; which workspace entries a resources
apply leaves alone (``core/workspace/protected_entries``) is the apply
engine's rule, applied by the materialiser over this port's answers.

Members are ``@abstractmethod`` on purpose, and every implementation —
``ResourceFileService`` included — **inherits** this port rather than merely
satisfying it structurally. The backend runs no static type checker, so a
structurally-satisfied Protocol is verified by nothing at all; abstract members
make a dropped or renamed method fail at construction instead of resolving to
an inherited ``...`` stub that silently returns ``None``. The DI provider used
to carry a hand-rolled ``isinstance`` check for exactly that drift; the base
class is the check now.
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class WorkspaceEntry:
    """One entry of a workspace listing: its workspace-relative path and whether it is a directory."""

    path: str
    is_dir: bool


@runtime_checkable
class ResourceFilePort(Protocol):
    """The five methods the ``resources`` materialiser reaches, as a type key.

    The declared keyword-only parameters are *apply's own call surface* —
    exactly and only what the materialiser passes, every one present on the
    real ``ResourceFileService`` method with the same declared default. The
    real service is a deliberate keyword **superset** (``preserve_structure``
    is the console router's folder-upload vocabulary; ``runtime_context`` a
    resolved device): a superset satisfies a structurally-checked protocol,
    so the port stays narrow on purpose. ``publish_id`` / ``device_uuid``
    address a service bot's verify or online stage instance, the way the
    console's listing does; a draft bot passes neither. A
    ``runtime_checkable`` isinstance only checks method *presence* — the
    reflection test in the materialiser's suite is what pins the mirror
    against drift (alongside the fake, which copies these shapes).
    """

    @abstractmethod
    async def upload_file(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        target_dir: str,
        filename: str,
        data: bytes,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def delete(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        path: str,
    ) -> bool: ...

    @abstractmethod
    async def exists(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        path: str,
    ) -> bool: ...

    @abstractmethod
    async def list_entries(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        prefix: str = "",
        recursive: bool = False,
        publish_id: str | None = None,
        device_uuid: str | None = None,
    ) -> tuple[WorkspaceEntry, ...]:
        """The entries under ``prefix`` (``""`` is the workspace root), workspace-relative, sorted by path.

        ``recursive`` asks for every descendant in one query (the engines'
        own ``recursive`` listing), else the one level. ``()`` is a confirmed
        empty directory, never a listing that could not be made: an
        implementation that cannot tell the two apart raises
        :class:`ListingFailed` instead of answering empty, because the apply
        engine clears and rewrites a workspace on the strength of this
        answer.
        """
        ...

    @abstractmethod
    async def delete_dir(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        path: str,
        publish_id: str | None = None,
        device_uuid: str | None = None,
    ) -> bool:
        """Delete the directory at ``path`` and everything under it; ``False`` when the transport did not.

        No probe: the caller says it is a directory (a listing just did).
        ``False`` is the device filesystems' one failure signal (they catch
        their own errors), so on a directory a listing just showed it is a
        refusal, which the caller treats as one.
        """
        ...


class ListingFailed(RuntimeError):
    """``list_entries`` could not list the directory and could not confirm it absent.

    The apply engine clears and rewrites the workspace on the strength of a
    listing (what it removes, and what there is to clear), so a listing that
    failed must never come back as "nothing there". The device transports do
    not all distinguish the two — teclaw's ``list_dir`` answers ``None`` for
    both — which is why this is raised rather than an empty answer.
    """


__all__ = ["ListingFailed", "ResourceFilePort", "WorkspaceEntry"]
