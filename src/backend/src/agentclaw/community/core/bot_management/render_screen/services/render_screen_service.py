"""Bot Render Screen — business logic layer.

只负责 CRUD 语义，不含 HTTP 相关逻辑。
"""
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


logger = get_logger()


class RenderScreenService:
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
        return resolve_render_screen_scope(self._require_bot(bot_id))

    def authorize_render_screen_bot(self, *, bot_id: str, user_id: str) -> str:
        """Resolve the scope and enforce collaborative access for coding bots."""
        scope = self._resolve_scope(bot_id)
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
        elif record.owner_id != user_id:
            raise PermissionError("无权操作此 Bot 的 CDN 配置")
        return record

    def list_render_screens(
        self,
        *,
        bot_id: str,
        owner_id: str | None = None,
        current_user_id: str | None = None,
    ) -> list[RenderScreenRecord]:
        """查询某 Bot 下所有 CDN 配置（未删除）。"""
        try:
            bot = self._require_bot(bot_id)
        except PermissionError as exc:
            raise PermissionError(f"无权查看此 Bot 的 CDN 配置: {bot_id}") from exc

        scope = resolve_render_screen_scope(bot)
        if scope == "bot":
            user_id = current_user_id or owner_id or ""
            if not user_id or not self._bot_has_collaborative_access(bot_id, user_id):
                raise PermissionError(f"无权查看此 Bot 的 CDN 配置: {bot_id}")
            return self._repo.list_by_bot_id(bot_id=bot_id, owner_id=None)

        effective_owner_id = owner_id or current_user_id or ""
        return self._repo.list_by_bot_id(bot_id=bot_id, owner_id=effective_owner_id)

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
        if scope == "bot":
            user_id = current_user_id or creator_id
            if not user_id or not self._bot_has_collaborative_access(bot_id, user_id):
                raise PermissionError(f"无权操作此 Bot 的 CDN 配置: {bot_id}")
            existing = self._repo.list_by_bot_id(bot_id=bot_id, owner_id=None)
        else:
            existing = self._repo.list_by_bot_id(bot_id=bot_id, owner_id=owner_id)

        if any(r.name == name for r in existing):
            raise ValueError(f"Duplicate name '{name}' for bot_id={bot_id}")
        if any(r.cdn_url == cdn_url for r in existing):
            raise ValueError(f"Duplicate cdn_url '{cdn_url}' for bot_id={bot_id}")

        bot = self._require_bot(bot_id)
        record_owner_id = owner_id or creator_id
        if scope == "bot":
            record_owner_id = str(bot.get("owner_id") or record_owner_id)

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
