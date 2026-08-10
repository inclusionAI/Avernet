"""SQLAlchemy repository for environment-scoped frontend user-list entries."""

from __future__ import annotations

from injector import inject
from sqlalchemy import func

from agentclaw.community.core.user_list.models import EntityUserListModel
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env
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
    ) -> bool:
        target_env = env or get_current_env()
        with self._db.orm_session() as session:
            return (
                session.query(EntityUserListModel.id)
                .filter(
                    EntityUserListModel.entity_id == entity_id,
                    EntityUserListModel.user_list_type == user_list_type,
                    EntityUserListModel.env == target_env,
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
    ) -> None:
        target_env = env or get_current_env()
        with self._db.orm_session() as session:
            if not in_whitelist:
                (
                    session.query(EntityUserListModel)
                    .filter(
                        EntityUserListModel.entity_id == entity_id,
                        EntityUserListModel.user_list_type == user_list_type,
                        EntityUserListModel.env == target_env,
                    )
                    .delete(synchronize_session=False)
                )
                return

            table = EntityUserListModel.__table__
            values = {
                "entity_id": entity_id,
                "user_list_type": user_list_type,
                "env": target_env,
            }
            if session.get_bind().dialect.name == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as _insert

                statement = _insert(table).values(**values)
                statement = statement.on_conflict_do_update(
                    index_elements=["entity_id", "user_list_type", "env"],
                    set_={"gmt_modified": func.now()},
                )
            else:
                from sqlalchemy.dialects.mysql import insert as _insert

                statement = _insert(table).values(**values)
                statement = statement.on_duplicate_key_update(
                    gmt_modified=func.now(),
                )
            session.execute(statement)


__all__ = ["UserListRepository"]
