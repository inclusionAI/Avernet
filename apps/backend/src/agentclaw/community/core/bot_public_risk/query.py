"""Read locally persisted risk applications in their authorized Bot scope."""
from __future__ import annotations

from agentclaw.community.core.bot_public_risk.repository import BotPublicRiskRepository, RiskCheck


class RiskRecordNotFound(LookupError):
    """The application is absent from the addressed Bot scope."""


class RiskQuery:
    def __init__(self, repository: BotPublicRiskRepository) -> None:
        self._repository = repository

    def get(self, *, env: str, bot_pk: int, risk_check_id: str) -> RiskCheck:
        row = self._repository.get(env=env, risk_check_id=risk_check_id)
        if row is None or row.bot_pk != bot_pk or row.env != env:
            raise RiskRecordNotFound("Risk application unavailable")
        return row

    def current(self, *, env: str, bot_pk: int, public_scope: str) -> RiskCheck | None:
        row = self._repository.get_current(env=env, bot_pk=bot_pk, public_scope=public_scope)
        if row is not None and (row.bot_pk != bot_pk or row.env != env):
            raise RiskRecordNotFound("Risk application unavailable")
        return row
