"""SQLAlchemy repository for environment-scoped frontend user-list entries."""

from __future__ import annotations

from injector import inject
from sqlalchemy import LargeBinary, cast, func

from agentclaw.community.core.user_list.models import EntityUserListModel
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.user_list.rules import MembershipConflictError, validate_membership
from agentclaw.community.core.repository.protocols.identity import UserListRepositoryProtocol


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
                    self._entity_column(session, subject_type) == entity_id,
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
        with self._db.orm_session() as session:
            if subject_type == "department":
                conflicting = session.query(EntityUserListModel.entity_id).filter(
                    EntityUserListModel.entity_id == entity_id,
                    EntityUserListModel.user_list_type == user_list_type,
                    EntityUserListModel.env == target_env,
                    EntityUserListModel.subject_type == subject_type,
                    EntityUserListModel.bot_type == bot_type,
                ).first()
                # COSEC: never mutate a distinct path equated by a legacy collation.
                if conflicting is not None and conflicting[0] != entity_id:
                    raise MembershipConflictError("Department path conflicts with existing collation")
            if not in_whitelist:
                (
                    session.query(EntityUserListModel)
                    .filter(
                        self._entity_column(session, subject_type) == entity_id,
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
            if subject_type == "department":
                persisted = session.query(EntityUserListModel.entity_id).filter(
                    self._entity_column(session, subject_type) == entity_id,
                    EntityUserListModel.user_list_type == user_list_type,
                    EntityUserListModel.env == target_env,
                    EntityUserListModel.subject_type == subject_type,
                    EntityUserListModel.bot_type == bot_type,
                ).with_for_update().first()
                if persisted is None:
                    raise MembershipConflictError("Department path conflicts with existing collation")


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
            entity_column = self._entity_column(session, subject_type)
            return session.query(EntityUserListModel.id).filter(
                entity_column.in_(entity_ids),
                EntityUserListModel.subject_type == subject_type,
                EntityUserListModel.user_list_type == user_list_type,
                EntityUserListModel.env == env,
                EntityUserListModel.bot_type.in_(bot_types),
            ).first() is not None


    @staticmethod
    def _entity_column(session, subject_type: str):
        column = EntityUserListModel.entity_id
        if subject_type == "department":
            if session.get_bind().dialect.name == "sqlite":
                return column.collate("BINARY")
            return cast(column, LargeBinary)
        return column


__all__ = ["UserListRepository"]
