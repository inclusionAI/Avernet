"""Stable Version-Published consumer seam for Track Latest."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.skill_center.materialization_contract import (
    PublishedMaterializedSkillVersion,
)


@runtime_checkable
class TrackLatestServiceProtocol(Protocol):
    def version_published(
        self, version: PublishedMaterializedSkillVersion
    ) -> None: ...


__all__ = ["TrackLatestServiceProtocol"]
