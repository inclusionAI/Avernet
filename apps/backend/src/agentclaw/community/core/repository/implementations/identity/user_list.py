"""SQLAlchemy repository for environment-scoped frontend user-list entries."""

from __future__ import annotations

from injector import inject
from sqlalchemy import func

from agentclaw.community.core.user_list.models import EntityUserListModel
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.log import get_logger
from agentclaw.community.core.user_list.rules import validate_membership
from agentclaw.community.core.repository.protocols.identity import UserListRepositoryProtocol


logger = get_logger()


class UserListRepository(
    UserListRepositoryProtocol,
):
    """Use an exact, parameterized current-environment membership query."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def exists(
        self,
        *,
        entity_id: str,
        user_list_type: str,
        env: str | None = None,
        subject_type: str = "user",
        bot_type: str = "all",
    ) -> bool:
        entity_id = validate_membership(entity_id, subject_type, bot_type)
        target_env = env or get_current_env()
        with self._db.orm_session() as session:
            return (
                session.query(EntityUserListModel.id)
                .filter(
                    EntityUserListModel.entity_id == entity_id,
                    EntityUserListModel.user_list_type == user_list_type,
                    EntityUserListModel.env == target_env,
                    EntityUserListModel.subject_type == subject_type,
                    EntityUserListModel.bot_type == bot_type,
                )
                .first()
                is not None
            )

    def set_membership(
        self,
        *,
        entity_id: str,
        user_list_type: str,
        in_whitelist: bool,
        env: str | None = None,
        subject_type: str = "user",
        bot_type: str = "all",
    ) -> None:
        entity_id = validate_membership(entity_id, subject_type, bot_type)
        target_env = env or get_current_env()
        logger.info(
            "user_list.membership_write comparison=database_collation "
            "subject_type=%s bot_type=%s user_list_type=%s env=%s in_whitelist=%s",
            subject_type, bot_type, user_list_type, target_env, in_whitelist,
        )
        with self._db.orm_session() as session:
            if not in_whitelist:
                (
                    session.query(EntityUserListModel)
                    .filter(
                        EntityUserListModel.entity_id == entity_id,
                        EntityUserListModel.user_list_type == user_list_type,
                        EntityUserListModel.env == target_env,
                        EntityUserListModel.subject_type == subject_type,
                        EntityUserListModel.bot_type == bot_type,
                    )
                    .delete(synchronize_session=False)
                )
                return

            table = EntityUserListModel.__table__
            values = {
                "entity_id": entity_id,
                "user_list_type": user_list_type,
                "env": target_env,
                "subject_type": subject_type,
                "bot_type": bot_type,
            }
            if session.get_bind().dialect.name == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as _insert

                statement = _insert(table).values(**values)
                statement = statement.on_conflict_do_update(
                    index_elements=["entity_id", "user_list_type", "env", "subject_type", "bot_type"],
                    set_={"gmt_modified": func.now()},
                )
            else:
                from sqlalchemy.dialects.mysql import insert as _insert

                statement = _insert(table).values(**values)
                statement = statement.on_duplicate_key_update(
                    gmt_modified=func.now(),
                )
            session.execute(statement)


    def has_rules(
        self, *, subject_type: str, user_list_type: str, env: str,
        bot_types: tuple[str, ...],
    ) -> bool:
        with self._db.orm_session() as session:
            return session.query(EntityUserListModel.id).filter(
                EntityUserListModel.subject_type == subject_type,
                EntityUserListModel.user_list_type == user_list_type,
                EntityUserListModel.env == env,
                EntityUserListModel.bot_type.in_(bot_types),
            ).first() is not None

    def exists_any(
        self, *, entity_ids: tuple[str, ...], subject_type: str,
        user_list_type: str, env: str, bot_types: tuple[str, ...],
    ) -> bool:
        with self._db.orm_session() as session:
            return session.query(EntityUserListModel.id).filter(
                EntityUserListModel.entity_id.in_(entity_ids),
                EntityUserListModel.subject_type == subject_type,
                EntityUserListModel.user_list_type == user_list_type,
                EntityUserListModel.env == env,
                EntityUserListModel.bot_type.in_(bot_types),
            ).first() is not None

__all__ = ["UserListRepository"]
