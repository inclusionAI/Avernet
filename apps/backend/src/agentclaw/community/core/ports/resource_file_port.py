"""A bot's workspace resource files, named by what the caller needs of them.

An outbound port (see this package's README): the ``resources`` materialiser
uploads, deletes and probes a bot's workspace files, and this names exactly
those three operations.

**It is a narrowing.** ``ResourceFileService`` has seven public methods —
``list_dir``, ``read_file``, ``iter_directory_files``, ``create_directory``
alongside these three. The materialiser needs three; handed the service it
could reach the rest.

**Two implementations, split on where the write lands** — the same axis the
delivery families split on everywhere else:

* ``DeviceResource`` (ARCA) forwards to ``ResourceFileService``, which writes
  the file into the bot's live container.
* ``PlatformResource`` (platform-managed teclaw) writes objects under the
  store's ``workspace`` namespace and never touches a container; the composed
  artifact is the delivery.

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
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ResourceFilePort(Protocol):
    """The five methods the ``resources`` materialiser reaches, as a type key.

    The declared keyword-only parameters are *apply's own call surface* —
    exactly and only what the materialiser passes, every one present on the
    real ``ResourceFileService`` method with the same declared default. The
    real service is a deliberate keyword **superset** (``preserve_structure``
    is the console router's folder-upload vocabulary; ``publish_id`` /
    ``device_uuid`` address a bound instance): a superset satisfies a
    structurally-checked protocol, so the port stays narrow on purpose. A
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
    async def list_root(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
    ) -> tuple[str, ...]:
        """Every resource file in the workspace as a workspace-relative path, the protected entries left out.

        The workspace root is the resources root; the entries left out are
        ``core/workspace/protected_entries`` (identity files, the skills
        trees, the engine's state and configuration, dotfiles), the same
        ones ``clear_root`` leaves alone. ``()`` is a confirmed empty
        workspace, never a listing that could not be made: an implementation
        that cannot tell the two apart raises :class:`RootListingFailed`
        instead of answering empty.
        """
        ...

    @abstractmethod
    async def clear_root(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
    ) -> bool:
        """Delete every unprotected entry of the workspace root; ``False`` when there was none.

        A delete that does not land raises :class:`RootClearFailed`; ``False``
        never hides a refusal.
        """
        ...


class RootListingFailed(RuntimeError):
    """``list_root`` could not list the root and could not confirm it absent.

    The apply engine clears and rewrites the root on the strength of this
    listing (what it removes, and whether a refused clear found anything to
    refuse), so a listing that failed must never come back as "nothing
    there". The device transports do not all distinguish the two — teclaw's
    ``list_dir`` answers ``None`` for both — which is why the implementation
    confirms absence from the parent directory and raises this when that
    confirmation is not available.
    """


class RootClearFailed(RuntimeError):
    """``clear_root`` found an entry it could not delete.

    The device filesystems answer ``False`` for their own failures, and a
    ``False`` on an entry the root listing just showed is a refusal, not
    "nothing there". The apply engine reports the category not cleared and
    writes nothing over the root; the next apply converges it.
    """


__all__ = ["ResourceFilePort", "RootClearFailed",
    "RootListingFailed"]
