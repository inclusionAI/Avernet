"""ORM-backed AC Bot Publish repository implementation.

Read-only access to ac_bot_publish table.
Uses @with_orm_session decorator for SQLAlchemy ORM Session lifecycle.
"""

from secbaas.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.logger import get_logger

from ._orm_model import AcBotPublishModel
from ._protocol import AcBotPublishRepository

log = get_logger("orm-repository")


class OrmAcBotPublishRepository(OrmConnectionMixin, AcBotPublishRepository):
    """ORM-based read-only access to ac_bot_publish table."""

    def __init__(self, database) -> None:
        self._database = database

    @with_orm_session
    def get_binding_id(
        self,
        *,
        source_bot_id: str,
        status: str = "success",
        owner_id: str | None = None,
        env: str | None = None,
    ) -> int | None:
        log.info(
            "get_binding_id: source_bot_id=%s, status=%s, env=%s",
            source_bot_id,
            status,
            env,
        )
        """Query binding_id from ac_bot_publish ext JSON for service bots.

        Reads ext JSON column and extracts binding.{online, verify}.id
        depending on the status value.
        """
        import json

        filters = [
            AcBotPublishModel.source_bot_id == source_bot_id,
            AcBotPublishModel.status == status,
        ]
        if owner_id is not None:
            filters.append(AcBotPublishModel.owner_id == owner_id)
        if env is not None:
            filters.append(AcBotPublishModel.env == env)

        row = (
            self._session.query(AcBotPublishModel)
            .filter(*filters)
            .order_by(AcBotPublishModel.id.desc())
            .first()
        )

        if row is None:
            log.info("[ac-bot-publish:get_binding_id] result: None")
            return None

        try:
            ext_text = row.ext
            ext = json.loads(ext_text) if isinstance(ext_text, str) else ext_text
            binding_info = ext.get("binding", {})

            binding_key = "verify" if status == "validating" else "online"
            raw = binding_info.get(binding_key)
            result = int(raw) if raw is not None else None
            log.info("[ac-bot-publish:get_binding_id] result: %s", result)
            return result
        except (json.JSONDecodeError, TypeError, AttributeError):
            log.info("[ac-bot-publish:get_binding_id] result: None (parse error)")
            return None

    @with_orm_session
    def get_binding_ids(
        self,
        *,
        source_bot_id: str,
        status: str = "success",
        owner_id: str | None = None,
        env: str | None = None,
    ) -> list[int]:
        """获取所有匹配的 binding_id 列表（对齐 SQL INNER JOIN 行为）。

        与 get_binding_id 的区别：
        1. 返回所有匹配记录的 binding_id（去重），而非只取最新一条
        2. env 参数可选，传入时在 ac_bot_publish 层过滤

        Args:
            source_bot_id: 原始 bot_id
            status: 发布状态（validating/success）
            owner_id: 所有者 ID
            env: 环境参数，可选

        Returns:
            binding_id 列表（按 id DESC 排序，去重）
        """
        log.info(
            "get_binding_ids: source_bot_id=%s, status=%s, owner_id=%s, env=%s",
            source_bot_id,
            status,
            owner_id,
            env,
        )
        import json

        filters = [
            AcBotPublishModel.source_bot_id == source_bot_id,
            AcBotPublishModel.status == status,
        ]
        if owner_id is not None:
            filters.append(AcBotPublishModel.owner_id == owner_id)
        if env is not None:
            filters.append(AcBotPublishModel.env == env)

        rows = (
            self._session.query(AcBotPublishModel)
            .filter(*filters)
            .order_by(AcBotPublishModel.id.desc())
            .all()
        )

        result_ids: list[int] = []
        seen: set[int] = set()
        for row in rows:
            try:
                ext_text = row.ext
                ext = json.loads(ext_text) if isinstance(ext_text, str) else ext_text
                binding_info = ext.get("binding", {})

                binding_key = "verify" if status == "validating" else "online"
                raw = binding_info.get(binding_key)
                if raw is not None:
                    bid = int(raw)
                    if bid not in seen:
                        seen.add(bid)
                        result_ids.append(bid)
            except (json.JSONDecodeError, TypeError, AttributeError, ValueError):
                continue

        log.info(
            "[ac-bot-publish:get_binding_ids] result: %s for source_bot_id=%s, status=%s",
            result_ids,
            source_bot_id,
            status,
        )
        return result_ids
