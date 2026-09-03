"""The skill upload road's narrow naming, for the materialisers' ports.

The ``skills`` materialiser installs a declared package through two methods
of the upload service; this names exactly those. The bound object is the
real ``LocalSkillUploadService`` on ARCA and ``StoreSkillPackagePort`` on the
platform-managed teclaw path — structural typing, no adapter.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Mapping, Optional, Protocol, runtime_checkable


@runtime_checkable
class SkillPackageUploadPort(Protocol):
    """What the ``skills`` materialiser asks of the upload road."""

    @abstractmethod
    async def upload_local_skill(
        self, *, bot_id: str, owner_id: str, actor_id: str, package: bytes
    ) -> dict[str, Any]:
        """Install the validated package and return the skill row it wrote."""
        ...

    @abstractmethod
    async def installed_package_digest(
        self, *, bot: Mapping[str, Any], bot_id: str, owner_id: str, name: str
    ) -> Optional[str]:
        """The digest of the package installed under ``name``, or ``None``."""
        ...


__all__ = ["SkillPackageUploadPort"]
