"""Bot Render Screen — business logic layer.

只负责 CRUD 语义，不含 HTTP 相关逻辑。
"""
from dataclasses import replace
from typing import Sequence

from injector import inject

from agentclaw.community.core.bot_management.render_screen.models import RenderScreenRecord
from agentclaw.community.core.bot_management.render_screen.scope import resolve_render_screen_scope
from agentclaw.community.core.repository.protocols.bot import (
    BotRepository,
    CollaboratorRepositoryProtocol,
    RenderScreenRepository,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.bot_management.render_screen_service_protocol import (
    RenderScreenCopyResult,
    RenderScreenCopyRow,
    RenderScreenRow,
    RenderScreenServiceProtocol,
)


logger = get_logger()


class RenderScreenService(RenderScreenServiceProtocol):
    """第四屏 CDN 配置业务逻辑。"""

    @inject
    def __init__(
        self,
        repository: RenderScreenRepository,
        bot_repository: BotRepository,
        collaborator_repository: CollaboratorRepositoryProtocol,
    ) -> None:
        self._repo = repository
        self._bot_repo = bot_repository
        self._collaborator_repo = collaborator_repository

    def _require_bot(self, bot_id: str) -> dict:
        try:
            bot = self._bot_repo.get_by_id(bot_id)
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning("[RenderScreen] failed to load bot bot_id=%s err=%s", bot_id, exc)
            raise PermissionError(f"无权操作此 Bot 的 CDN 配置: {bot_id}") from exc
        if not bot:
            raise PermissionError(f"无权操作此 Bot 的 CDN 配置: {bot_id}")
        return bot

    def _bot_has_collaborative_access(self, bot_id: str, user_id: str) -> bool:
        bot = self._require_bot(bot_id)
        if str(bot.get("owner_id") or "") == user_id:
            return True
        bot_pk = bot.get("id")
        if bot_pk is None:
            return False
        try:
            collaborator = self._collaborator_repo.get_by_bot_and_user(
                int(bot_pk),
                user_id,
                get_current_env(),
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning(
                "[RenderScreen] collaborator lookup failed bot_id=%s user_id=%s err=%s",
                bot_id,
                user_id,
                exc,
            )
            return False
        return collaborator is not None

    def _resolve_scope(self, bot_id: str) -> str:
        # Bots externally onboarded through BCN may not have an ac_bot record.
        # Keep their historical behavior by falling back to owner scope.
        return resolve_render_screen_scope(self._bot_repo.get_by_id(bot_id))

    def authorize_render_screen_bot(self, *, bot_id: str, user_id: str) -> str:
        """Resolve the scope and enforce collaborative access for coding bots."""
        scope = self._resolve_scope(bot_id)
        bot = self._bot_repo.get_by_id(bot_id)
        if bot and self._is_service_member_instance(bot, user_id):
            raise PermissionError("无权操作此 Bot 的 CDN 配置")
        if scope == "bot" and not self._bot_has_collaborative_access(bot_id, user_id):
            raise PermissionError(f"无权操作此 Bot 的 CDN 配置: {bot_id}")
        return scope

    def authorize_render_screen_record(
        self,
        *,
        record_id: int,
        user_id: str,
    ) -> RenderScreenRecord:
        """Resolve one record and enforce the correct scope-level permission."""
        record = self._repo.get_by_id(record_id)
        if record is None:
            raise ValueError(f"RenderScreen not found: {record_id}")

        scope = self._resolve_scope(record.bot_id)
        if scope == "bot":
            if not self._bot_has_collaborative_access(record.bot_id, user_id):
                raise PermissionError(f"无权操作此 Bot 的 CDN 配置: {record.bot_id}")
        else:
            bot = self._bot_repo.get_by_id(record.bot_id)
            # 创建侧(legacy 宽口 + openapi v1 COSEC MEMBER)允许协作者写入,且
            # 记录 scope 统一落 Bot 归属者,编辑/删除若只认「记录 owner == 登录人」,
            # 协作者刚创建的记录(落归属者 scope)立刻不可改删 — 回到「只能增加,
            # 不能删除和编辑」的不对称。故放行归属者本人与在册协作者。
            # 但「service Bot 个人定制」例外:记录挂在被服务成员个人名下、既非
            # 归属者也非 entity 行,存量语义为仅本人可改删,不随协作关系放行 —
            # 否则归属交接后新归属者也能改删他人定制,破坏 B6 预期。
            personal_customization = bool(bot) and (
                bot.get("bot_type") == "service"
                and record.owner_id not in (
                    str(bot.get("owner_id") or ""),
                    str(bot.get("entity_id") or ""),
                )
            )
            if bot and not personal_customization and not self._is_service_member_instance(bot, user_id):
                if self._bot_has_collaborative_access(record.bot_id, user_id):
                    return record
            # 兜底保持存量语义:BCN 无 bot 行按「记录 owner == 登录人」判;
            # 历史漂移行的创建人(记录挂在其名下但非归属者/协作者)仍可改删。
            expected_owner = record.owner_id
            if (bot and bot.get("bot_type") == "service"
                    and record.owner_id == str(bot.get("entity_id") or "")):
                expected_owner = str(bot.get("owner_id") or "")
            if expected_owner != user_id:
                raise PermissionError("无权操作此 Bot 的 CDN 配置")
        return record

    @staticmethod
    def _is_service_member_instance(bot: dict, user_id: str) -> bool:
        """服务 Bot 的「被服务成员实例」:行 entity 归本人、归属者另有其人。

        与 authorize_render_screen_bot 创建侧的拦截对称 — 这类成员实例
        既不是归属者也不应经协作关系放行写操作。
        """
        return (
            bot.get("bot_type") == "service"
            and str(bot.get("entity_id") or "") == user_id
            and str(bot.get("owner_id") or "") != user_id
        )

    def list_render_screens(
        self,
        *,
        bot_id: str,
        owner_id: str | None = None,
        current_user_id: str | None = None,
    ) -> list[RenderScreenRecord]:
        """查询某 Bot 下所有 CDN 配置（未删除）。"""
        bot = self._bot_repo.get_by_id(bot_id)
        scope = resolve_render_screen_scope(bot)
        if scope == "bot":
            # 读放开：副屏 CDN 配置（库名 → CDN URL 映射）是非敏感渲染资源。
            # 群聊/分享页/协作场景的查看者并非 Bot 归属者或协作者，但都需要
            # 这份映射来渲染副屏面板，否则前端会报「组件库不存在」。
            # 写操作（create/update/delete）仍走 authorize_* 严格校验，
            # 读放开不影响写权限。
            return self._repo.list_by_bot_id(bot_id=bot_id, owner_id=None)

        effective_owner_id = owner_id or current_user_id or ""
        records = self._repo.list_by_bot_id(bot_id=bot_id, owner_id=effective_owner_id)
        if bot and bot.get("bot_type") == "service":
            return [replace(row, owner_id=str(bot["owner_id"]))
                    if row.owner_id == str(bot["entity_id"]) else row for row in records]
        return records

    def create_render_screen(
        self,
        *,
        bot_id: str,
        owner_id: str,
        name: str,
        cdn_url: str,
        creator_id: str,
        current_user_id: str | None = None,
    ) -> int:
        """创建 CDN 配置，返回新记录 id。"""
        scope = self._resolve_scope(bot_id)
        # 记录 scope 统一按 Bot 归属者落库:读侧 resource_locator 会把查询 owner
        # 归一化成 Bot 的 entity_id,若按调用方(legacy 入口传的是登录人)落库,
        # 协作者在他人 Bot 下写入的记录对归属者/协作者都稳定查空(副屏刷新后
        # 报「组件库不存在」)。BCN 等无 bot 行的场景保持调用方兜底。
        bot = self._bot_repo.get_by_id(bot_id)
        record_owner_id = (
            str(bot.get("owner_id") or "") if bot else ""
        ) or owner_id or creator_id
        if scope == "bot":
            user_id = current_user_id or creator_id
            if not user_id or not self._bot_has_collaborative_access(bot_id, user_id):
                raise PermissionError(f"无权操作此 Bot 的 CDN 配置: {bot_id}")
            existing = self._repo.list_by_bot_id(bot_id=bot_id, owner_id=None)
        else:
            # 收口 legacy 宽口:历史代码对非共享 Bot 的创建不做归属校验,任何
            # 登录人都能写入,是「记录 scope 漂移」这一整类 bug 的根源。BCN 等
            # 无 bot 行的场景保持开放(无从归属判定)。服务 Bot 成员实例与创建侧
            # authorize_render_screen_bot 的拦截对称,不经协作关系放行。
            if bot and not self._is_service_member_instance(bot, current_user_id or creator_id or ""):
                acting_user = current_user_id or creator_id or ""
                if not acting_user or not self._bot_has_collaborative_access(bot_id, acting_user):
                    raise PermissionError(f"无权操作此 Bot 的 CDN 配置: {bot_id}")
            existing = self._repo.list_by_bot_id(
                bot_id=bot_id, owner_id=record_owner_id or None
            )

        if any(r.name == name for r in existing):
            raise ValueError(f"Duplicate name '{name}' for bot_id={bot_id}")
        if any(r.cdn_url == cdn_url for r in existing):
            raise ValueError(f"Duplicate cdn_url '{cdn_url}' for bot_id={bot_id}")

        record_id = self._repo.insert(
            bot_id=bot_id,
            owner_id=record_owner_id,
            name=name,
            cdn_url=cdn_url,
            creator_id=creator_id,
        )
        logger.info("[RenderScreen] created id=%s bot_id=%s name=%s", record_id, bot_id, name)
        return record_id

    def update_render_screen(
        self,
        *,
        record_id: int,
        name: str,
        cdn_url: str,
    ) -> None:
        """更新 CDN 配置。"""
        existing = self._repo.get_by_id(record_id)
        if existing is None:
            raise ValueError(f"RenderScreen not found: {record_id}")
        self._repo.update_by_id(record_id=record_id, name=name, cdn_url=cdn_url)
        logger.info("[RenderScreen] updated id=%s name=%s", record_id, name)

    def delete_render_screen(self, *, record_id: int) -> None:
        """软删除 CDN 配置。"""
        existing = self._repo.get_by_id(record_id)
        if existing is None:
            raise ValueError(f"RenderScreen not found: {record_id}")
        self._repo.delete_by_id(record_id=record_id)
        logger.info("[RenderScreen] deleted id=%s", record_id)

    def get_render_screen(self, record_id: int) -> RenderScreenRecord | None:
        """查询单条记录。"""
        return self._repo.get_by_id(record_id)

    def copy_render_screens(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        rows: Sequence[RenderScreenRow],
    ) -> RenderScreenCopyResult:
        """Converge a copy-serialised render-screen set onto one bot.

        Overwrite converge: the ``rows`` replace the destination's set. A row
        whose ``name`` already exists → ``update`` in place (only when
        ``cdn_url`` differs); a new name → ``create``; a name present on the
        destination but not in ``rows`` → soft ``delete``. Re-applying an
        unchanged set writes nothing — the idempotent retry the apply-engine
        convergence criterion rests on.

        Ownership is taken from the bot row (``owner_id`` from the apply
        context), not the per-row field, and the destination's owner is
        re-read here so a copy onto a bot whose owner was just written lands
        the rows under that owner. The duplicate-name/collaborator checks of
        the user-facing create path are skipped: the rows are the source bot's
        already-valid set, and there is no second author to authorize.
        """
        bot = self._bot_repo.get_by_id(bot_id)
        record_owner_id = (
            str(bot.get("owner_id") or "") if bot else ""
        ) or owner_id
        existing = {r.name: r for r in self._repo.list_by_bot_id(bot_id=bot_id, owner_id=None)}

        results: list[RenderScreenCopyRow] = []
        for row in rows:
            at = f"render_screen[{row.name}]"
            current = existing.pop(row.name, None)
            if current is None:
                try:
                    self._repo.insert(
                        bot_id=bot_id,
                        owner_id=record_owner_id,
                        name=row.name,
                        cdn_url=row.cdn_url,
                        creator_id=actor_id,
                    )
                    results.append(RenderScreenCopyRow(name=row.name, outcome="created"))
                except Exception as exc:  # noqa: BLE001 — composed, never raw in the report
                    logger.warning("[RenderScreen] copy create failed bot_id=%s name=%s err=%s", bot_id, row.name, exc)
                    results.append(RenderScreenCopyRow(
                        name=row.name, outcome="failed",
                        reason=f"render_screen '{row.name}' could not be created",
                    ))
            elif current.cdn_url == row.cdn_url:
                results.append(RenderScreenCopyRow(name=row.name, outcome="unchanged"))
            else:
                try:
                    self._repo.update_by_id(record_id=current.id, name=row.name, cdn_url=row.cdn_url)
                    results.append(RenderScreenCopyRow(name=row.name, outcome="updated"))
                except Exception as exc:  # noqa: BLE001 — composed, never raw in the report
                    logger.warning("[RenderScreen] copy update failed id=%s err=%s", current.id, exc)
                    results.append(RenderScreenCopyRow(
                        name=row.name, outcome="failed",
                        reason=f"render_screen '{row.name}' could not be updated",
                    ))

        # Names present on the destination but no longer declared are removed.
        # ``existing`` was drained of every declared name above, so what
        # remains is exactly the removal set.
        for name, record in existing.items():
            try:
                self._repo.delete_by_id(record_id=record.id)
                results.append(RenderScreenCopyRow(name=name, outcome="removed"))
            except Exception as exc:  # noqa: BLE001 — composed, never raw in the report
                logger.warning("[RenderScreen] copy delete failed id=%s err=%s", record.id, exc)
                results.append(RenderScreenCopyRow(
                    name=name, outcome="failed",
                    reason=f"render_screen '{name}' could not be removed",
                ))

        return RenderScreenCopyResult(rows=tuple(results))

