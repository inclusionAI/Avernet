"""Application contract for submitting and refreshing Bot risk applications."""

from typing import Any, Mapping, Protocol, runtime_checkable

from agentclaw.community.core.bot_management.addressing import ResolvedBotContext
from agentclaw.community.core.bot_public_risk.repository_protocol import RiskCheck


@runtime_checkable
class BotPublicRiskServiceProtocol(Protocol):
    """Commands for an already authorized Bot and authenticated operator.

    ``submit`` returns (record, created); reuse with a conflicting normalized
    intent raises RequestConflict. Dependencies and storage errors propagate.
    ``refresh`` queries only the record's original check ID and returns
    UPDATED or NOT_QUERIED plus the locally persisted record. Failed external
    queries propagate and must not overwrite the last known conclusion.
    """
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
    """Read local records within a previously authorized Bot and tenant scope.

    A missing or cross-Bot record raises RiskRecordNotFound; ``current`` may
    return None. These reads never contact the external security service.
    """
    def get(self, *, env: str, bot_pk: int, risk_check_id: str) -> RiskCheck: ...

    def current(
        self, *, env: str, bot_pk: int, public_scope: str
    ) -> RiskCheck | None: ...
