"""Transport-agnostic Bot inventory aggregation service."""
from __future__ import annotations

import json
from typing import Any, Mapping

from agentclaw.community.core.errors import NotFound
from agentclaw.community.core.bot_inventory.errors import BotInventoryUpstreamError
from agentclaw.community.log import get_logger

from agentclaw.community.core.bot_inventory.protocols import (
    BotInventoryBotPort,
    BusinessSpaceContextProtocol,
    DesktopBotInventoryPort,
)
from agentclaw.community.core.bot_inventory.services.lifecycle_view import BotLifecycleView
from agentclaw.community.core.bot_inventory.types import (
    BotInventoryItem,
    BotInventoryKind,
    BusinessSpaceRef,
    DeployMode,
)


logger = get_logger()

MAX_CLOUD_ROWS = 1_000


class BotInventoryService:
    def __init__(
        self,
        *,
        bot_service: BotInventoryBotPort,
        desktop_service: DesktopBotInventoryPort,
        business_space: BusinessSpaceContextProtocol,
        lifecycle_view: BotLifecycleView,
    ) -> None:
        self._bot = bot_service
        self._desktop = desktop_service
        self._business_space = business_space
        self._lifecycle = lifecycle_view

    def list_items(
        self,
        *,
        owner_id: str,
        space: BusinessSpaceRef | None,
        keyword: str | None,
        engine: str | None,
        deploy_mode: DeployMode | None,
        page: int,
        page_size: int,
    ) -> tuple[list[BotInventoryItem], int]:
        cards: list[BotInventoryItem] = []
        if deploy_mode in (None, DeployMode.CLOUD):
            cards.extend(
                self._to_cloud_item(row, owner_id)
                for row in self._list_personal_cloud_rows(
                    owner_id=owner_id,
                    keyword=keyword,
                    engine=engine,
                )
            )
        if deploy_mode in (None, DeployMode.LOCAL):
            cards.extend(
                self._to_local_item(row, owner_id)
                for row in self._list_local_rows(
                    owner_id=owner_id,
                    keyword=keyword,
                    engine=engine,
                )
            )
        if space is not None:
            cards = [c for c in cards if c.space and c.space.space_id == space.space_id]
        cards.sort(key=lambda c: (c.deploy_mode.value, c.bot_name, c.bot_id))
        total = len(cards)
        start = (page - 1) * page_size
        return cards[start : start + page_size], total

    def _get_row(self, *, bot_id: str, owner_id: str) -> Mapping[str, Any]:
        try:
            return self._bot.get_bot(bot_id, owner_id)
        except Exception as exc:
            if not _is_not_found_error(exc):
                raise
            try:
                local_rows = self._desktop.list_user_bots(owner_id)
            except Exception as desktop_exc:
                _raise_if_desktop_service_error(desktop_exc)
                raise
            for row in local_rows:
                if str(row.get("bot_id") or "") == bot_id:
                    return row
            raise

    def _list_personal_cloud_rows(
        self, *, owner_id: str, keyword: str | None, engine: str | None
    ) -> list[Mapping[str, Any]]:
        rows: list[Mapping[str, Any]] = []
        fetch_size = 200
        page = 1
        total: int | None = None
        while True:
            result = self._bot.list_bots_by_conditions(
                owner_id=owner_id,
                bot_name=keyword,
                engine=engine,
                status=None,
                page=page,
                page_size=fetch_size,
            )
            page_items = list(result.get("items", []))
            remaining = MAX_CLOUD_ROWS - len(rows)
            rows.extend(page_items[:remaining])
            if total is None:
                raw_total = result.get("total")
                total = raw_total if isinstance(raw_total, int) else None
            if not page_items:
                break
            if total is not None and len(rows) >= total:
                break
            if len(page_items) < fetch_size:
                break
            if len(rows) >= MAX_CLOUD_ROWS:
                logger.warning(
                    "[BotInventoryService._list_personal_cloud_rows] truncated "
                    "cloud rows owner_id=%s keyword=%s engine=%s max_rows=%d total=%s",
                    owner_id,
                    keyword,
                    engine,
                    MAX_CLOUD_ROWS,
                    total,
                )
                break
            page += 1
        return [row for row in rows if row.get("bot_type") in (None, "", "personal")]

    def _list_local_rows(
        self, *, owner_id: str, keyword: str | None, engine: str | None
    ) -> list[Mapping[str, Any]]:
        try:
            rows = list(self._desktop.list_user_bots(owner_id))
        except Exception as exc:
            _raise_if_desktop_service_error(exc)
            raise
        if keyword:
            rows = [r for r in rows if keyword in str(r.get("bot_name") or "")]
        if engine:
            rows = [
                r for r in rows
                if (r.get("active_engine") or r.get("engine_type") or r.get("engine")) == engine
            ]
        return rows

    def _to_item(self, row: Mapping[str, Any], owner_id: str) -> BotInventoryItem:
        bot_type = str(row.get("bot_type") or "personal")
        if bot_type == "desktop":
            return self._to_local_item(row, owner_id)
        if bot_type == "service":
            return self._to_service_item(row, owner_id)
        return self._to_cloud_item(row, owner_id)

    def _to_cloud_item(self, row: Mapping[str, Any], owner_id: str) -> BotInventoryItem:
        return self._build_item(
            row=row,
            owner_id=owner_id,
            kind=BotInventoryKind.PERSONAL_CLOUD,
            deploy_mode=DeployMode.CLOUD,
        )

    def _to_local_item(self, row: Mapping[str, Any], owner_id: str) -> BotInventoryItem:
        return self._build_item(
            row=row,
            owner_id=owner_id,
            kind=BotInventoryKind.LOCAL,
            deploy_mode=DeployMode.LOCAL,
        )

    def _to_service_item(self, row: Mapping[str, Any], owner_id: str) -> BotInventoryItem:
        return self._build_item(
            row=row,
            owner_id=owner_id,
            kind=BotInventoryKind.SERVICE,
            deploy_mode=DeployMode.CLOUD,
        )

    def _build_item(
        self,
        *,
        row: Mapping[str, Any],
        owner_id: str,
        kind: BotInventoryKind,
        deploy_mode: DeployMode,
    ) -> BotInventoryItem:
        ext = _as_mapping(row.get("ext"))
        display_state = self._lifecycle.display_state(bot={**dict(row), "ext": ext}, kind=kind)
        actions, disabled = self._lifecycle.allowed_actions(bot={**dict(row), "ext": ext}, kind=kind)
        return BotInventoryItem(
            bot_id=str(row.get("bot_id") or ""),
            bot_name=str(row.get("bot_name") or ""),
            bot_desc=str(row.get("bot_desc") or ""),
            engine=str(row.get("active_engine") or row.get("engine_type") or row.get("engine") or ""),
            bot_type=str(row.get("bot_type") or "personal"),
            kind=kind,
            deploy_mode=deploy_mode,
            display_state=display_state,
            status=str(row.get("status") or ""),
            owner_entity_id=str(row.get("owner_id") or row.get("entity_id") or owner_id),
            space=self._business_space.bot_space(bot={**dict(row), "ext": ext}, owner_id=owner_id),
            avatar_url=_optional_str(ext.get("avatar_url") or row.get("avatar_url")),
            machine_id=_optional_str(ext.get("machine_id") or row.get("machine_id")),
            mount_path=_optional_str(ext.get("mount_path") or row.get("mount_path")),
            passport_id=_passport_id(ext),
            actions=actions,
            disabled_actions=disabled or None,
        )


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, Mapping) else {}
    return {}


def _optional_str(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _passport_id(ext: Mapping[str, Any]) -> str | None:
    passport = ext.get("passport")
    if isinstance(passport, Mapping):
        return _optional_str(passport.get("agent_code") or passport.get("agent_id"))
    return None


def _is_not_found_error(exc: Exception) -> bool:
    return isinstance(exc, NotFound) or exc.__class__.__name__ == "BotNotFoundError"


def _raise_if_desktop_service_error(exc: Exception) -> None:
    if exc.__class__.__name__ in {"DesktopBotServiceError", "DesktopBotOrphanError"}:
        raise BotInventoryUpstreamError("desktop service failed") from exc
