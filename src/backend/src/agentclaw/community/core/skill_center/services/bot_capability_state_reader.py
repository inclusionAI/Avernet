"""Installation-backed access to a Bot's active capability state."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from injector import inject

from agentclaw.community.core.repository.capability_desired_state_types import (
    InstallationFlushPlan,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.capability_desired_state import (
    CapabilityDesiredStateRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skills_pool import (
    SkillsPoolSkillRepositoryProtocol,
)
from agentclaw.community.core.skill_center.bot_engine_scope import (
    bot_default_engine_types,
    bot_engine_type,
)
from agentclaw.community.core.skill_center.feature_flags import get_skill_center_flags
from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError
from agentclaw.community.core.skill_center.version_resolution_contract import (
    SkillVersionResolverProtocol,
)
from agentclaw.community.core.skills_pool.models import RegisteredSkillAsset
from agentclaw.community.core.skill_center.bot_capability_state_reader_protocol import BotCapabilityStateReaderProtocol


class BotCapabilityStateReader(BotCapabilityStateReaderProtocol):
    """The one read model for a Bot's active capabilities.

    Installation is the active-identity source of truth.  Before the historical
    backfill is accepted, every effective read fully repairs SkillSet state.
    Afterwards a guarded migration mode retains only Default+exclusion
    materialization and still answers from Installation alone. Center identities
    are resolved to an exact PUBLISHED Version before they leave this seam.
    """

    @inject
    def __init__(
        self,
        repository: CapabilityDesiredStateRepositoryProtocol,
        bot_repo: BotRepository,
        pool_skills: SkillsPoolSkillRepositoryProtocol,
        version_resolver: SkillVersionResolverProtocol,
    ) -> None:
        self._repository = repository
        self._bot_repo = bot_repo
        self._pool_skills = pool_skills
        self._version_resolver = version_resolver

    def member_skill_ids(self, *, bot: Mapping[str, Any]) -> frozenset[int]:
        return self._repository.list_member_skill_ids(
            bot_id=str(bot["bot_id"]),
            owner_id=str(bot["owner_id"]),
            engine_type=bot_engine_type(bot),
            default_engine_types=bot_default_engine_types(bot),
        )

    def initialize_installations(
        self,
        *,
        bot_id: str,
        owner_id: str,
        bot: Mapping[str, Any] | None = None,
    ) -> None:
        """Explicitly initialize a newly persisted Bot without Runtime I/O.

        Creation always uses the complete resolver, independent of the reader
        migration gate.  This makes retries converge new rows and ensures a
        newly created Bot does not rely on a later read or projector to gain its
        initial Installation facts.
        """
        resolved_bot = self._bot(bot_id=bot_id, owner_id=owner_id, bot=bot)
        self._repository.initialize_installations(
            bot_id=bot_id,
            owner_id=owner_id,
            env=str(resolved_bot["env"]),
            engine_type=bot_engine_type(resolved_bot),
            default_engine_types=bot_default_engine_types(resolved_bot),
        )

    def synchronize_installations(
        self,
        *,
        bot_id: str,
        owner_id: str,
        bot: Mapping[str, Any] | None = None,
    ) -> None:
        """Apply the configured reader migration scope without Runtime I/O."""
        resolved_bot = self._bot(bot_id=bot_id, owner_id=owner_id, bot=bot)
        self._flush(
            bot=resolved_bot,
            bot_id=bot_id,
            owner_id=owner_id,
        )

    def active_skill_assets(
        self,
        *,
        bot_id: str,
        owner_id: str,
        bot: Mapping[str, Any] | None = None,
    ) -> tuple[RegisteredSkillAsset, ...]:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, bot=bot)
        self.synchronize_installations(
            bot_id=bot_id, owner_id=owner_id, bot=bot
        )
        assets = tuple(
            self._pool_skills.list_bot_installed_assets(
                env=str(bot["env"]),
                bot_id=bot_id,
                owner_id=owner_id,
            )
        )
        return self._version_resolver.resolve_latest_runtime_assets(
            env=str(bot["env"]), assets=assets
        )

    def active_mcp_server_codes(
        self,
        *,
        bot_id: str,
        owner_id: str,
        bot: Mapping[str, Any] | None = None,
    ) -> frozenset[str]:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, bot=bot)
        self.synchronize_installations(
            bot_id=bot_id, owner_id=owner_id, bot=bot
        )
        return frozenset(
            self._repository.list_installed_mcps(bot_id=bot_id, owner_id=owner_id)
        )

    def _bot(
        self, *, bot_id: str, owner_id: str, bot: Mapping[str, Any] | None
    ) -> Mapping[str, Any]:
        """A caller-supplied row is trusted; otherwise the exact Bot must exist."""
        if bot is not None:
            return bot
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise LocalSkillNotFoundError()
        return bot

    def _flush(
        self, *, bot: Mapping[str, Any], bot_id: str, owner_id: str
    ) -> InstallationFlushPlan:
        sync = (
            self._repository.sync_default_installations
            if get_skill_center_flags().installation_default_sync_only
            else self._repository.flush_installations
        )
        return sync(
            bot_id=bot_id,
            owner_id=owner_id,
            env=str(bot["env"]),
            engine_type=bot_engine_type(bot),
            default_engine_types=bot_default_engine_types(bot),
        )
