from dependency_injector import containers, providers

from secbaas.community.core.database import db_manager as _db_manager
from secbaas.community.core.repository.ac_bot import OrmAcBotRepository
from secbaas.community.core.repository.ac_bot_publish import OrmAcBotPublishRepository
from secbaas.community.core.repository.api_gateway import OrmAPIKeyRepository
from secbaas.community.core.repository.arca_ttl import (
    OrmTtlRenewalScheduleRepository,
)
from secbaas.community.core.repository.bot import OrmBotRepository
from secbaas.community.core.repository.bot_device_rel import OrmBotDeviceRelRepository
from secbaas.community.core.repository.bot_qpm import OrmBotQpmRepository
from secbaas.community.core.repository.bot_run import OrmBotRunRepository
from secbaas.community.core.repository.bot_run_interaction import (
    OrmBotRunInteractionRepository,
)
from secbaas.community.core.repository.bot_run_queue import OrmBotRunQueueRepository
from secbaas.community.core.repository.bot_run_queue_chunk import (
    OrmBotRunQueueChunkRepository,
)
from secbaas.community.core.repository.bot_session import OrmBotSessionRepository
from secbaas.community.core.repository.device import OrmDeviceRepository
from secbaas.community.core.repository.device_binding import OrmDeviceBindingRepository
from secbaas.community.core.repository.device_template import (
    OrmDeviceTemplateRepository,
)
from secbaas.community.core.repository.distributed_lock import (
    OrmDistributedLockRepository,
)
from secbaas.community.core.repository.file_transfer_ticket import OrmTicketRepository
from secbaas.community.core.repository.local_user_machine import (
    OrmLocalUserMachineRepository,
)
from secbaas.community.core.repository.publish import OrmPublishRepository
from secbaas.community.core.repository.publish_batch import OrmPublishBatchRepository
from secbaas.community.core.repository.publish_record import OrmPublishRecordRepository
from secbaas.community.core.repository.resource_key import OrmResourceKeyRepository
from secbaas.community.core.repository.session_file_ticket import (
    OrmSessionTicketRepository,
)
from secbaas.community.core.repository.system_config import OrmSystemConfigRepository
from secbaas.community.core.repository.tenant import OrmTenantRepository
from secbaas.community.core.repository.ws_relay_session import (
    OrmWsRelaySessionRepository,
)
from secbaas.community.logger import get_logger

logger = get_logger("bootstrap")

# Shared db_manager provider — avoids repeating providers.Object() 17 times.
_db_provider = providers.Object(_db_manager)


def _orm_repo(repo_cls, **extra_kwargs):
    """Build a ``Singleton(repo_cls, database=_db_provider, **extra_kwargs)`` provider."""
    return providers.Singleton(repo_cls, database=_db_provider, **extra_kwargs)


class CoreRepositoryContainer(containers.DeclarativeContainer):
    config = providers.Configuration()
    db_manager = _db_provider

    ac_bot_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmAcBotRepository),
        sqlite=_orm_repo(OrmAcBotRepository),
        mariadb=_orm_repo(OrmAcBotRepository),
    )
    ac_bot_publish_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmAcBotPublishRepository),
        sqlite=_orm_repo(OrmAcBotPublishRepository),
        mariadb=_orm_repo(OrmAcBotPublishRepository),
    )
    api_gateway_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmAPIKeyRepository),
        sqlite=_orm_repo(OrmAPIKeyRepository),
        mariadb=_orm_repo(OrmAPIKeyRepository),
    )
    arca_ttl_schedule_repository = providers.Selector(
        config.plugins.database.plugin_database,
        zdas=_orm_repo(OrmTtlRenewalScheduleRepository),
        sqlite=_orm_repo(OrmTtlRenewalScheduleRepository),
        mariadb=_orm_repo(OrmTtlRenewalScheduleRepository),
    )
    bot_device_rel_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmBotDeviceRelRepository),
        sqlite=_orm_repo(OrmBotDeviceRelRepository),
        mariadb=_orm_repo(OrmBotDeviceRelRepository),
    )
    bot_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmBotRepository, rel_repo=bot_device_rel_repository),
        sqlite=_orm_repo(OrmBotRepository, rel_repo=bot_device_rel_repository),
        mariadb=_orm_repo(OrmBotRepository, rel_repo=bot_device_rel_repository),
    )
    bot_run_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmBotRunRepository),
        sqlite=_orm_repo(OrmBotRunRepository),
        mariadb=_orm_repo(OrmBotRunRepository),
    )
    bot_qpm_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmBotQpmRepository),
        sqlite=_orm_repo(OrmBotQpmRepository),
        mariadb=_orm_repo(OrmBotQpmRepository),
    )
    bot_run_queue_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmBotRunQueueRepository),
        sqlite=_orm_repo(OrmBotRunQueueRepository),
        mariadb=_orm_repo(OrmBotRunQueueRepository),
    )
    bot_run_interaction_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmBotRunInteractionRepository),
        sqlite=_orm_repo(OrmBotRunInteractionRepository),
        mariadb=_orm_repo(OrmBotRunInteractionRepository),
    )
    bot_run_queue_chunk_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmBotRunQueueChunkRepository),
        sqlite=_orm_repo(OrmBotRunQueueChunkRepository),
        mariadb=_orm_repo(OrmBotRunQueueChunkRepository),
    )
    bot_session_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmBotSessionRepository),
        sqlite=_orm_repo(OrmBotSessionRepository),
        mariadb=_orm_repo(OrmBotSessionRepository),
    )
    device_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmDeviceRepository),
        sqlite=_orm_repo(OrmDeviceRepository),
        mariadb=_orm_repo(OrmDeviceRepository),
    )
    device_binding_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmDeviceBindingRepository),
        sqlite=_orm_repo(OrmDeviceBindingRepository),
        mariadb=_orm_repo(OrmDeviceBindingRepository),
    )
    device_template_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmDeviceTemplateRepository),
        sqlite=_orm_repo(OrmDeviceTemplateRepository),
        mariadb=_orm_repo(OrmDeviceTemplateRepository),
    )
    distributed_lock_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmDistributedLockRepository),
        sqlite=_orm_repo(OrmDistributedLockRepository),
        mariadb=_orm_repo(OrmDistributedLockRepository),
    )
    local_user_machine_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmLocalUserMachineRepository),
        sqlite=_orm_repo(OrmLocalUserMachineRepository),
        mariadb=_orm_repo(OrmLocalUserMachineRepository),
    )
    publish_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmPublishRepository),
        sqlite=_orm_repo(OrmPublishRepository),
        mariadb=_orm_repo(OrmPublishRepository),
    )
    publish_batch_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmPublishBatchRepository),
        sqlite=_orm_repo(OrmPublishBatchRepository),
        mariadb=_orm_repo(OrmPublishBatchRepository),
    )
    publish_record_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmPublishRecordRepository),
        sqlite=_orm_repo(OrmPublishRecordRepository),
        mariadb=_orm_repo(OrmPublishRecordRepository),
    )
    resource_key_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmResourceKeyRepository),
        sqlite=_orm_repo(OrmResourceKeyRepository),
        mariadb=_orm_repo(OrmResourceKeyRepository),
    )
    system_config_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmSystemConfigRepository),
        sqlite=_orm_repo(OrmSystemConfigRepository),
        mariadb=_orm_repo(OrmSystemConfigRepository),
    )
    tenant_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmTenantRepository),
        sqlite=_orm_repo(OrmTenantRepository),
        mariadb=_orm_repo(OrmTenantRepository),
    )
    ws_relay_session_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmWsRelaySessionRepository),
        sqlite=_orm_repo(OrmWsRelaySessionRepository),
        mariadb=_orm_repo(OrmWsRelaySessionRepository),
    )
    ticket_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmTicketRepository),
        sqlite=_orm_repo(OrmTicketRepository),
        mariadb=_orm_repo(OrmTicketRepository),
    )

    session_ticket_repository = providers.Selector(
        config.plugins.database,
        zdas=_orm_repo(OrmSessionTicketRepository),
        sqlite=_orm_repo(OrmSessionTicketRepository),
        mariadb=_orm_repo(OrmSessionTicketRepository),
    )
