"""Real G3 package conflicts must terminate G4 fast polling."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterVersion,
    CanonicalCenterVersionIdentity,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ResolvedSkillPlan,
    RuntimeProjectionIssue,
    RuntimeProjectionResult,
    RuntimeProjectionStatus,
)
from agentclaw.community.core.skill_center.runtime_resolver import RuntimeSkillProjection
from agentclaw.community.core.skill_center.services.center_content_distribution import (
    CanonicalCenterContentDistribution,
    CenterContentDistributionConfig,
)
from agentclaw.community.core.skill_center.services.desktop_skill_recovery import (
    DesktopSkillRecoveryTaskHandler,
)
from agentclaw.community.core.skills_pool.models import PoolSkillMapping
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
)
from agentclaw.community.core.task_queue.types import Complete
from agentclaw.community.plugin_api.object_storage import (
    ObjectCreateResult,
    ObjectReadResult,
    ObjectReadStatus,
)
from agentclaw.community.plugins.local.oss_storage import MockObjectStoragePlugin


_IDENTITY = CanonicalCenterVersionIdentity(
    "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "17"
)


class _CanonicalStore:
    def __init__(self) -> None:
        self.reads = 0
        self.version = CanonicalCenterVersion.from_files(
            _IDENTITY, {"SKILL.md": b"# exact\n"}
        )

    def read_version(self, ref):
        assert ref.identity == _IDENTITY
        self.reads += 1
        return self.version


class _Projector:
    def __init__(self, distribution) -> None:
        self._distribution = distribution
        mapping = PoolSkillMapping(
            corpus="center",
            relative_path=None,
            link_name="weather",
            skill_uuid=_IDENTITY.skill_uuid,
            sc_version_number=_IDENTITY.sc_version_number,
        )
        self.plan = ResolvedSkillPlan(
            bot_id="bot-a",
            owner_id="owner-a",
            bot={
                "bot_id": "bot-a",
                "owner_id": "owner-a",
                "entity_id": "owner-a",
                "env": "dev",
                "bot_type": "desktop",
                "active_engine": "openclaw",
                "binding_id": 17,
                "status": "ACTIVE",
            },
            engine="openclaw",
            projection=RuntimeSkillProjection(
                skill_mappings=(mapping,), skill_assets=()
            ),
        )

    def resolve_plan(self, **_kwargs):
        return self.plan

    async def apply_plan(self, **_kwargs):
        pending = self._distribution.lookup(_IDENTITY)
        assert pending.state.value == "PENDING"
        return RuntimeProjectionResult(
            status=RuntimeProjectionStatus.PENDING,
            components={"skills": RuntimeProjectionStatus.PENDING},
            issues=(
                RuntimeProjectionIssue(
                    resource_type="SKILL",
                    code="CENTER_CONTENT_PACKAGE_PENDING",
                    reason="descriptor is absent",
                    status=RuntimeProjectionStatus.PENDING,
                    retryable=True,
                    name="weather",
                    corpus="CENTER",
                ),
            ),
        )


def test_real_package_conflict_does_not_repeat_every_five_seconds(caplog) -> None:
    canonical = _CanonicalStore()
    objects = MockObjectStoragePlugin()
    objects.create_object_if_absent.return_value = ObjectCreateResult.ALREADY_EXISTS
    objects.read_object.side_effect = lambda key: (
        ObjectReadResult(ObjectReadStatus.NOT_FOUND)
        if key.endswith("ready.json")
        else ObjectReadResult(ObjectReadStatus.FOUND, b"conflicting object")
    )
    distribution = CanonicalCenterContentDistribution(
        canonical_store=canonical,
        object_storage=objects,
        url_signer=objects,
        config=CenterContentDistributionConfig(env="dev"),
        now=lambda: datetime(2026, 9, 10, tzinfo=UTC),
    )
    bots = MagicMock()
    bots.get_by_id_and_owner.return_value = {
        **_Projector(distribution).plan.bot,
    }
    layouts = MagicMock()
    layouts.get.return_value = BotSkillLayoutState.legacy_default(
        BotSkillLayoutScope(env="dev", entity_id="owner-a", bot_id="bot-a")
    )
    handler = DesktopSkillRecoveryTaskHandler(
        bots=bots,
        projector=_Projector(distribution),
        distribution=distribution,
        layouts=layouts,
        env_provider=lambda: "dev",
    )

    first = handler.handle({"owner_id": "owner-a", "bot_id": "bot-a"})
    second = handler.handle({"owner_id": "owner-a", "bot_id": "bot-a"})

    assert isinstance(first, Complete)
    assert isinstance(second, Complete)
    assert canonical.reads == 2
    assert "CENTER_CONTENT_PACKAGE_CONFLICT" in caplog.text
