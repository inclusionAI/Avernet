"""Application contract for submitting and refreshing Bot risk applications."""

from typing import Any, Mapping, Protocol, runtime_checkable

from agentclaw.community.core.bot_management.addressing import ResolvedBotContext
from agentclaw.community.core.bot_public_risk.repository import RiskCheck


@runtime_checkable
class BotPublicRiskServiceProtocol(Protocol):
    def submit(
        self,
        *,
        bot: ResolvedBotContext,
        actor_id: str,
        surface: str,
        body: Mapping[str, Any],
    ) -> tuple[RiskCheck, bool]: ...

    def refresh(
        self, *, bot: ResolvedBotContext, actor_id: str, record: RiskCheck
    ) -> tuple[str, RiskCheck]: ...


@runtime_checkable
class BotPublicRiskQueryProtocol(Protocol):
    def get(self, *, env: str, bot_pk: int, risk_check_id: str) -> RiskCheck: ...

    def current(
        self, *, env: str, bot_pk: int, public_scope: str
    ) -> RiskCheck | None: ...
