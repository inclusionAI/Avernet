"""ORM-based resource key repository."""

from sqlalchemy import or_

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.logger import get_logger

from ._orm_model import (
    WILDCARD_BOT_ID,
    ResourceKeyBotMappingModel,
    ResourceKeyModel,
)
from ._protocol import ResourceKeyRepository
from ._record import ResourceKeyRecord

log = get_logger("orm")


class OrmResourceKeyRepository(OrmConnectionMixin, ResourceKeyRepository):
    def __init__(self, database) -> None:
        self._database = database

    @with_orm_session
    def get_by_resource_key_and_tenant(
        self, resource_key: str, tenant: str
    ) -> ResourceKeyRecord | None:
        row = (
            self._session.query(ResourceKeyModel)
            .filter(
                ResourceKeyModel.resource_key == resource_key,
                ResourceKeyModel.tenant == tenant,
            )
            .first()
        )
        return row.to_record() if row else None

    @with_orm_session
    def exists_bot_mapping(
        self,
        resource_key_id: int,
        bot_id: str,
        *,
        claimed_bot_id: str | None = None,
    ) -> bool:
        """Whether ``resource_key_id`` may act on ``bot_id``.

        A mapping row whose ``bot_id`` is the literal wildcard grants the key
        every bot in its tenant. The wildcard is an explicit, deliberate row an
        administrator inserts — never inferred from an empty mapping list, which
        stays deny-by-default.

        ``claimed_bot_id`` is the bot a presented service principal names in its
        ``app.bot_id`` claim. It participates as a third match **inside** the
        resource-key scope, so it is never a bypass: a claimed bot still needs a
        grant naming it (an exact row, or a wildcard). ``None`` reduces the
        predicate to exactly the pre-claim behaviour.
        """
        grants = [bot_id, WILDCARD_BOT_ID]
        claimed = (claimed_bot_id or "").strip()
        if claimed:
            grants.append(claimed)
        row = (
            self._session.query(ResourceKeyBotMappingModel)
            .filter(
                ResourceKeyBotMappingModel.resource_key_id == resource_key_id,
                or_(*(ResourceKeyBotMappingModel.bot_id == grant for grant in grants)),
            )
            .first()
        )
        return row is not None
