"""Application service for the narrow active-SkillSet cutover repair."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.repository.protocols.skill_set_control_plane import (
    SkillSetControlPlaneRepositoryProtocol,
)


class ActiveSkillSetInstallationMaterializer:
    """Materialize missing active-only Installations for one exact Bot."""

    @inject
    def __init__(self, repository: SkillSetControlPlaneRepositoryProtocol) -> None:
        self._repository = repository

    def materialize(
        self, *, bot_id: str, owner_id: str, engine_type: str
    ) -> int:
        return self._repository.ensure_active_skillset_installations(
            bot_id=bot_id,
            owner_id=owner_id,
            engine_type=engine_type,
        )

