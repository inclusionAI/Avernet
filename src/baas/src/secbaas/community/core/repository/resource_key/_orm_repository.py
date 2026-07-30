"""ORM-based resource key repository."""

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.logger import get_logger

from ._orm_model import ResourceKeyModel
from ._orm_model import ResourceKeyBotMappingModel
from ._protocol import ResourceKeyRepository
from ._record import ResourceKeyRecord

log = get_logger("orm-repository")


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
        row = (
            self._session.query(ResourceKeyBotMappingModel)
            .filter(
                ResourceKeyBotMappingModel.resource_key_id == resource_key_id,
                ResourceKeyBotMappingModel.bot_id == bot_id,
            )
            .first()
        )
        return row is not None