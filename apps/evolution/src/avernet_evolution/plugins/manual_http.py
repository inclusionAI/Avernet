"""Trusted control-plane delivery over purpose-bound Backend callbacks.

No generic URL/action input, no Worker registration, no implicit delegation.
Only a verified built-in human request with a current DataScope may call it.
"""

from dataclasses import replace
from typing import Any
from urllib.parse import quote

from avernet_genome.models import BotRef

from ..core.evolution_promote.manual_contract import ManualDelivery
from ..core.evolution_promote.service import PromotionNotReady
from ..core.evolution_run.data_scope import DataScope, current_data_scope, data_scope
from .platform_http import BotHttp, PlatformHttpError


class HttpManualDelivery(ManualDelivery):
    def __init__(self, http: BotHttp):
        self.http = http

    def _scope(self, bot: BotRef, authorization_id: str) -> DataScope:
        scope = current_data_scope.get()
        if scope is None or scope.bot != bot:
            raise PermissionError("human control scope required for promotion")
        return replace(scope, promotion_authorization_id=authorization_id)

    async def get(self, bot: BotRef, authorization_id: str) -> dict[str, Any] | None:
        with data_scope(self._scope(bot, authorization_id)):
            try:
                return await self.http.request(
                    bot, "GET", "/genome/manual-promotions/" + quote(authorization_id, safe="")
                )
            except PlatformHttpError as exc:
                if exc.status == 404:
                    return None
                raise

    async def apply(self, bot: BotRef, authorization: dict[str, Any]) -> dict[str, Any]:
        scope = self._scope(bot, authorization["id"])
        if scope.user_id != authorization["user_id"]:
            raise PermissionError("authorization belongs to another operator")
        fields = (
            "id",
            "basis",
            "candidate_id",
            "run_id",
            "revision_id",
            "parent_revision_id",
            "strategy",
            "user_id",
            "reason",
            "authorized_at",
        )
        with data_scope(
            replace(
                scope,
                run_id=authorization["run_id"],
                parent=authorization["parent_revision_id"],
                strategy=authorization["strategy"],
            )
        ):
            try:
                return await self.http.request(
                    bot, "POST", "/genome/manual-promotions", body={key: authorization[key] for key in fields}
                )
            except PlatformHttpError as exc:
                if exc.status in {400, 409, 422}:
                    raise PromotionNotReady(
                        "Backend refused promotion; refresh provenance, baseline and drift"
                    ) from None
                if exc.status == 403:
                    raise PermissionError("current operator cannot apply this candidate") from None
                raise
