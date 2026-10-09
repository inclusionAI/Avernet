"""The device-backed ``ResourceFilePort``: workspace files on the container.

``ResourceFileService`` writes a bot's workspace files into its live
container. This is the apply engine's view of it — the three methods the
``resources`` materialiser calls, and not the other four.

**The narrowing is the job.** The service also exposes ``list_dir``,
``read_file``, ``iter_directory_files`` and ``create_directory``; a
materialiser handed the whole service could reach any of them. It is also a
keyword *superset* on the three it shares — ``preserve_structure`` is the
console router's folder-upload vocabulary, ``publish_id`` / ``device_uuid``
address a bound instance — and none of that is apply's to pass. The port
declares apply's own call surface and this forwards exactly it.

**The wrapped service is imported under ``TYPE_CHECKING`` only**, for the
reason ``identity_files.py`` records at its own use: the module naming it
reaches the device dispatcher graph at import time, and importing it eagerly
from a module the apply service imports turns a lazy provider into an import
cycle.

Its platform-managed counterpart is ``PlatformResource`` in
``managed_files/ports.py``, which writes the same files as store objects
under the ``workspace`` namespace.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentclaw.community.core.ports.resource_file_port import ResourceFilePort

if TYPE_CHECKING:  # pragma: no cover — see the module docstring
    from agentclaw.community.core.services.resource_file_service import (
        ResourceFileService,
    )


class DeviceResource(ResourceFilePort):
    """The ARCA resource road: workspace files onto the bot's live container.

    Three methods, each forwarded verbatim. What a call looks like from the
    ``resources`` materialiser::

        await port.upload_file(
            entity_id="ent_7", bot_id="bot_42",
            engine_type="claude_code",
            target_dir="data",            # the directory part of the path
            filename="faq.csv",           # the file part
            data=b"q,a\n...",
        )                                  # -> the service's own result dict

        await port.exists(entity_id="ent_7", bot_id="bot_42",
                          engine_type="claude_code",
                          path="data/faq.csv")     # -> True | False

        await port.delete(entity_id="ent_7", bot_id="bot_42",
                          engine_type="claude_code",
                          path="data/faq.csv")     # -> True | False

    ``entity_type`` defaults to ``"staff"`` on all three; apply never passes
    anything else.

    Bound as ``MaterialiserPorts.resource_service`` for the ARCA family. Its
    platform-managed counterpart is ``PlatformResource`` in
    ``managed_files/ports.py``.
    """

    def __init__(self, inner: ResourceFileService) -> None:
        self._inner = inner

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
    ) -> dict[str, Any]:
        return await self._inner.upload_file(
            entity_type=entity_type, entity_id=entity_id, bot_id=bot_id,
            engine_type=engine_type, target_dir=target_dir, filename=filename,
            data=data,
        )

    async def delete(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        path: str,
    ) -> bool:
        return await self._inner.delete(
            entity_type=entity_type, entity_id=entity_id, bot_id=bot_id,
            engine_type=engine_type, path=path,
        )

    async def exists(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        path: str,
    ) -> bool:
        return await self._inner.exists(
            entity_type=entity_type, entity_id=entity_id, bot_id=bot_id,
            engine_type=engine_type, path=path,
        )


__all__ = ["DeviceResource"]
