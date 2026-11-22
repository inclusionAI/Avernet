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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.devices.services.device_context import RuntimeBindingTarget


class PackagePublication(StrEnum):
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    NOT_DISPATCHED = "NOT_DISPATCHED"


class PackageMetadata(StrEnum):
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"


@dataclass(frozen=True, slots=True)
class LocalSkillPackageReceipt:
    """Facts about a package publication, independent of activation."""

    name: str
    content_digest: str | None
    publication: PackagePublication
    metadata: PackageMetadata

    def as_dict(self) -> dict[str, str | None]:
        return {
            "name": self.name,
            "content_digest": self.content_digest,
            "publication": self.publication.value,
            "metadata": self.metadata.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LocalSkillPackageReceipt:
        name, digest = data["name"], data.get("content_digest")
        if not isinstance(name, str) or (
            digest is not None and not isinstance(digest, str)
        ):
            raise ValueError("Invalid Local Skill package receipt")
        return cls(
            name, digest, PackagePublication(data["publication"]),
            PackageMetadata(data["metadata"]),
        )


@dataclass(frozen=True, slots=True)
class LocalSkillPackageWriteResult:
    receipt: LocalSkillPackageReceipt
    upload: dict[str, Any] | None = None
    # The actual Runtime that acknowledged this publication and metadata write.
    target: RuntimeBindingTarget | None = None


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
    async def delete_local_skill(
        self, *, skill_id: str, name: str, bot_id: str, owner_id: str,
        actor_id: str,
    ) -> None:
        """Physically delete one unreferenced Bot-owned Local package and row."""
        ...

    @abstractmethod
    async def ensure_dst_local_skill_asset(
        self, *, bot_id: str, owner_id: str, actor_id: str,
        source_bot_id: str, source_owner_id: str, name: str,
    ) -> dict[str, Any]:
        """Register the dst ``ac_skill`` row for a bot-file-synced Local skill.

        A ``from: from_bot`` member reaches this bot through BOT_FILE_SYNC: the
        sync writes the source bot's workspace onto this one, which carries the
        package's bytes to the dst ``skills-local`` directory. This method does
        not fetch, upload or read the package — it reverse-looks-up the source
        bot's ``ac_skill`` row for ``name``/``description``, recomputes the dst
        ``git_path`` through the same factory the upload road takes, and writes
        the dst ``ac_skill`` row (create-or-replace same-name), skipping the
        package write the upload road runs (file-sync already wrote it).
        """
        ...

@runtime_checkable
class BatchSkillPackageUploadPort(SkillPackageUploadPort, Protocol):
    """Batch publication available to an existing device-backed Bot Apply."""

    @abstractmethod
    async def upload_local_skill_batch(
        self, *, bot_id: str, owner_id: str, actor_id: str,
        packages: Sequence[tuple[str, bytes]],
    ) -> tuple[LocalSkillPackageWriteResult, ...]:
        """Collect a receipt for every named package without replaying failures."""
        ...


__all__ = [
    "BatchSkillPackageUploadPort", "LocalSkillPackageReceipt", "LocalSkillPackageWriteResult",
    "PackageMetadata", "PackagePublication", "SkillPackageUploadPort",
]
