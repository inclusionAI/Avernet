"""The device-backed ``SkillPackageUploadPort``: the manual-upload road (W5).

``LocalSkillUploadService`` and ``LocalSkillDeleteService`` are domain services
with their own Service APIs and HTTP routes. This adapter exposes only the
complete-package replace and physical-delete operations Manifest Apply needs.

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

from typing import Any

from agentclaw.community.core.ports.skill_package_upload_port import (
    SkillPackageUploadPort,
)
from agentclaw.community.core.skill_center.local_skill_upload_service_protocol import (
    LocalSkillUploadServiceProtocol,
)
from agentclaw.community.core.skill_center.local_skill_delete_service_protocol import (
    LocalSkillDeleteServiceProtocol,
)


class DeviceSkillPackageUpload(SkillPackageUploadPort):
    """The ARCA upload road: package files onto the bot's device.

    The complete-package write looks like::

        await port.upload_local_skill(
            bot_id="bot_42", owner_id="usr_owner",
            actor_id="usr_collaborator",
            package=b"PK\x03\x04...",     # the validated canonical zip
        )                                   # -> the service's result dict

    Bound as ``MaterialiserPorts.upload_service`` for the ARCA family. Its
    platform-managed counterpart is ``PlatformSkillPackageUpload`` in
    ``managed_files/ports.py``; unlike the activation delegates, the two share
    no body — only the port and the skill row they record.
    """

    def __init__(
        self,
        inner: LocalSkillUploadServiceProtocol,
        delete_service: LocalSkillDeleteServiceProtocol | None = None,
    ) -> None:
        self._inner = inner
        self._delete = delete_service

    async def upload_local_skill(
        self, *, bot_id: str, owner_id: str, actor_id: str, package: bytes
    ) -> dict[str, Any]:
        return await self._inner.upload_local_skill(
            bot_id=bot_id, owner_id=owner_id, actor_id=actor_id, package=package
        )

    async def delete_local_skill(
        self, *, skill_id: str, name: str, bot_id: str, owner_id: str,
        actor_id: str,
    ) -> None:
        if self._delete is None:
            fallback = getattr(self._inner, "delete_local_skill", None)
            if fallback is None:
                raise RuntimeError("Local Skill deletion service is not configured")
            await fallback(
                skill_id=skill_id,
                name=name,
                bot_id=bot_id,
                owner_id=owner_id,
                actor_id=actor_id,
            )
            return
        await self._delete.delete_local_skill(
            skill_id=skill_id, owner_id=owner_id, user_id=actor_id
        )

__all__ = ["DeviceSkillPackageUpload"]
