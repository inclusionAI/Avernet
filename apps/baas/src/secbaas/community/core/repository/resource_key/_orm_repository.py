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
    def exists_bot_mapping(self, resource_key_id: int, bot_id: str) -> bool:
        """Whether ``resource_key_id`` may act on ``bot_id``.

        A mapping row whose ``bot_id`` is the literal wildcard grants the key
        every bot in its tenant. The wildcard is an explicit, deliberate row an
        administrator inserts — never inferred from an empty mapping list, which
        stays deny-by-default.
        """
        row = (
            self._session.query(ResourceKeyBotMappingModel)
            .filter(
                ResourceKeyBotMappingModel.resource_key_id == resource_key_id,
                or_(
                    ResourceKeyBotMappingModel.bot_id == bot_id,
                    ResourceKeyBotMappingModel.bot_id == WILDCARD_BOT_ID,
                ),
            )
            .first()
        )
        return row is not None
