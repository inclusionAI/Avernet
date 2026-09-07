"""The device-backed ``SkillPackageUploadPort``: the manual-upload road (W5).

``LocalSkillUploadService`` is a domain service with its own Service API and
HTTP routes; the apply engine is one caller among several. This is the apply
engine's view of it — the two methods the ``skills`` materialiser calls, and
not the third.

**The narrowing is the whole job.** ``upload_local_skill_files`` converts a
browser-selected directory into a package; during an apply the package arrives
as fetched bytes and that method means nothing. Handed the Service API a
materialiser could reach for it; handed the port, it cannot. That is the same
service ``ActivationPort`` performs on ``project``, one level up: a method
hidden rather than a parameter.

Its platform-managed counterpart is ``PlatformSkillPackageUpload`` in
``managed_files/ports.py``, which writes the same package into the store
instead. Unlike the activation delegates, the two share no body — only the
port and the skill row they record.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from agentclaw.community.core.ports.skill_package_upload_port import (
    SkillPackageUploadPort,
)
from agentclaw.community.core.skill_center.local_skill_upload_service_protocol import (
    LocalSkillUploadServiceProtocol,
)


class DeviceSkillPackageUpload(SkillPackageUploadPort):
    """The ARCA upload road: package files onto the bot's device."""

    def __init__(self, inner: LocalSkillUploadServiceProtocol) -> None:
        self._inner = inner

    async def upload_local_skill(
        self, *, bot_id: str, owner_id: str, actor_id: str, package: bytes
    ) -> dict[str, Any]:
        return await self._inner.upload_local_skill(
            bot_id=bot_id, owner_id=owner_id, actor_id=actor_id, package=package
        )

    async def installed_package_digest(
        self, *, bot: Mapping[str, Any], bot_id: str, owner_id: str, name: str
    ) -> Optional[str]:
        return await self._inner.installed_package_digest(
            bot=bot, bot_id=bot_id, owner_id=owner_id, name=name
        )


__all__ = ["DeviceSkillPackageUpload"]
