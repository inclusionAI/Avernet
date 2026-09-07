"""The skill-package upload road, named by what the caller needs of it.

An outbound port (see this package's README): the ``skills`` materialiser
installs a declared package through two methods, and this names exactly those.

**What it deliberately omits is ``upload_local_skill_files``.** That method
belongs to ``LocalSkillUploadServiceProtocol`` — the Service API — and converts
one browser-selected directory's ``(relative_path, content)`` tuples into the
same validated package. It has exactly one caller, the directory-upload route,
and no meaning during a manifest apply, where the package arrives as fetched
bytes. A materialiser holding the whole Service API could reach for it; holding
the port, it cannot. Same narrowing ``ActivationPort`` performs on ``project``,
applied to a method rather than a parameter.

**Two implementations, split on where the write lands** — the same axis the
delivery families split on everywhere else:

* ``DeviceSkillPackageUpload`` (ARCA) writes the package into the bot's
  ``skills-local`` directory *on the device*, through the same
  ``LocalSkillUploadService`` the manual-upload route takes — which is what
  makes an installed skill indistinguishable from an uploaded one: it is one.
* ``PlatformSkillPackageUpload`` (platform-managed teclaw) writes the same
  package as objects in the managed-files store and never touches a container;
  the composed artifact is the delivery.

Unlike the activation pair, these two share no body: one writes device files,
the other store objects. Only the skill row they record is common.

Members are ``@abstractmethod`` on purpose. The backend runs no static type
checker, so a structurally-satisfied Protocol is verified by nothing at all;
abstract members make a dropped or renamed method fail at construction instead
of resolving to an inherited ``...`` stub that silently returns ``None``.
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
