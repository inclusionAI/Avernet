"""Project logical Local Skill package requests onto Engine-owned roots."""

from __future__ import annotations

import asyncio

from engine.community.core.skills.layout_planner import (
    LAYOUT_CONTRACT_VERSION,
    LayoutIdentity,
    RuntimeLayoutContext,
    resolve_filesystem_skill_layout,
)
from engine.community.core.skills.local_package import LocalSkillPackagePublisher
from engine.community.core.skills.models import (
    LocalSkillPackageApplyRequest,
    LocalSkillPackageApplyResult,
    LocalSkillPackageLayout,
)


class LocalSkillPackageApplication:
    """Resolve one Engine layout and delegate publication to the shared module."""

    def __init__(
        self,
        engine_type: str,
        *,
        publisher: LocalSkillPackagePublisher | None = None,
        context: RuntimeLayoutContext | None = None,
    ) -> None:
        self._engine_type = engine_type
        self._publisher = publisher or LocalSkillPackagePublisher()
        self._context = context or RuntimeLayoutContext()

    async def apply(
        self, request: LocalSkillPackageApplyRequest
    ) -> LocalSkillPackageApplyResult:
        plan = resolve_filesystem_skill_layout(
            LayoutIdentity(
                engine_type=self._engine_type,
                layout_contract_version=LAYOUT_CONTRACT_VERSION,
            ),
            self._context,
        )
        local_root = (
            plan.legacy_local
            if request.layout is LocalSkillPackageLayout.LEGACY
            else plan.pool_local
        )
        return await asyncio.to_thread(
            self._publisher.publish,
            skill_name=request.skill_name,
            package=request.package,
            target=local_root / request.skill_name,
        )


__all__ = ["LocalSkillPackageApplication"]
