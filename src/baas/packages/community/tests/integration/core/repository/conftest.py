"""Protocol-typed repository fixtures for repository-level integration tests.

Every fixture returns a Protocol type (e.g. BotRepository), backed by the
DI container with SQLite backend (it-sqlite overlay).  Tests use ONLY the
Protocol interface — no concrete class references allowed in test code.
"""

from __future__ import annotations

import pytest

from secbaas.core.repository.ac_bot import AcBotRepository
from secbaas.core.repository.ac_bot_publish import AcBotPublishRepository
from secbaas.core.repository.api_gateway import APIKeyRepository
from secbaas.core.repository.bot import BotRepository
from secbaas.core.repository.bot_device_rel import BotDeviceRelRepository
from secbaas.core.repository.bot_run import BotRunRepository
from secbaas.core.repository.bot_session import BotSessionRepository
from secbaas.core.repository.device import DeviceRepository
from secbaas.core.repository.device_binding import DeviceBindingRepository
from secbaas.core.repository.device_template import DeviceTemplateRepository
from secbaas.core.repository.distributed_lock import DistributedLockRepository
from secbaas.core.repository.local_user_machine import LocalUserMachineRepository
from secbaas.core.repository.publish import PublishRepository
from secbaas.core.repository.publish_batch import PublishBatchRepository
from secbaas.core.repository.publish_record import PublishRecordRepository
from secbaas.core.repository.system_config import SystemConfigRepository
from secbaas.core.repository.tenant import TenantRepository
from tests.integration.fixtures.bootstrap import bootstrap_init

# === Autouse bootstrap: init DI container + SQLite before any test ===


@pytest.fixture(scope="session", autouse=True)
def _auto_bootstrap(bootstrap_init):  # noqa: F811 — fixture param shadows import, autouse dependency
    pass


@pytest.fixture
def db_transaction():
    from unittest import mock

    return mock.MagicMock()


# === Protocol-typed fixture map ===
# Domain → (container_attr, Protocol type)
# Creates a fixture per domain that resolves through the DI container.

_DOMAIN_REGISTRY: dict[str, tuple] = {
    "ac_bot": ("ac_bot_repository", AcBotRepository),
    "ac_bot_publish": ("ac_bot_publish_repository", AcBotPublishRepository),
    "api_gateway": ("api_gateway_repository", APIKeyRepository),
    "bot": ("bot_repository", BotRepository),
    "bot_device_rel": ("bot_device_rel_repository", BotDeviceRelRepository),
    "bot_run": ("bot_run_repository", BotRunRepository),
    "bot_session": ("bot_session_repository", BotSessionRepository),
    "device": ("device_repository", DeviceRepository),
    "device_binding": ("device_binding_repository", DeviceBindingRepository),
    "device_template": ("device_template_repository", DeviceTemplateRepository),
    "distributed_lock": ("distributed_lock_repository", DistributedLockRepository),
    "local_user_machine": (
        "local_user_machine_repository",
        LocalUserMachineRepository,
    ),
    "publish": ("publish_repository", PublishRepository),
    "publish_batch": ("publish_batch_repository", PublishBatchRepository),
    "publish_record": ("publish_record_repository", PublishRecordRepository),
    "system_config": ("system_config_repository", SystemConfigRepository),
    "tenant": ("tenant_repository", TenantRepository),
}


def _make_repo_fixture(container_attr: str, scope: str = "function") -> callable:
    """Create a pytest fixture that returns a Protocol-typed repository.

    Resolves through the DI container (bootstrap_init must have run).
    """

    @pytest.fixture(scope=scope)
    def _fixture():
        from secbaas.bootstrap import get_container

        return getattr(get_container().repository, container_attr)()

    return _fixture


# === Generate all 17 Protocol-typed fixtures dynamically ===

for _domain, (_container_attr, _proto) in _DOMAIN_REGISTRY.items():
    _fixture_fn = _make_repo_fixture(_container_attr)
    _fixture_fn.__name__ = f"{_domain}_repository"
    globals()[_fixture_fn.__name__] = _fixture_fn

# For convenience: shorter aliases matching existing fixture names
# (so tests can use `bot_repository` or the domain-qualified `bot_repository`)


@pytest.fixture
def bot_repository_fixture(bot_repository: BotRepository) -> BotRepository:
    """Alias for bot_repository (Protocol-typed)."""
    return bot_repository


@pytest.fixture
def device_repository_fixture(device_repository: DeviceRepository) -> DeviceRepository:
    """Alias for device_repository (Protocol-typed)."""
    return device_repository


@pytest.fixture
def rel_repository(
    bot_device_rel_repository: BotDeviceRelRepository,
) -> BotDeviceRelRepository:
    """Shorthand for bot_device_rel_repository (Protocol-typed)."""
    return bot_device_rel_repository
