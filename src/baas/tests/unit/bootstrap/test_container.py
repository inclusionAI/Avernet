"""Tests for the bootstrap DI container assembly.

Verifies that ``get_container().repository.ac_bot_repository()``
and all other repository factories return correctly-wired instances
through the ``ApplicationContainer`` → ``RepositoryContainer`` chain.
"""

import pytest
from dependency_injector.containers import Container

from secbaas.community.bootstrap import ApplicationContainer, get_container
from secbaas.community.bootstrap._configs import DatabaseConfig
from secbaas.community.core.repository.ac_bot import (
    AcBotRepository,
)
from secbaas.community.core.repository.ac_bot import (
    OrmAcBotRepository as OrmAcBotRepositoryType,
)
from secbaas.community.core.repository.ac_bot_publish import (
    OrmAcBotPublishRepository as OrmAcBotPublishRepositoryType,
)
from secbaas.community.core.repository.api_gateway import (
    OrmAPIKeyRepository as OrmAPIKeyRepositoryType,
)
from secbaas.community.core.repository.bot import (
    OrmBotRepository as OrmBotRepositoryType,
)
from secbaas.community.core.repository.bot_device_rel import (
    OrmBotDeviceRelRepository as OrmBotDeviceRelRepositoryType,
)
from secbaas.community.core.repository.bot_run import (
    OrmBotRunRepository as OrmBotRunRepositoryType,
)
from secbaas.community.core.repository.bot_session import (
    OrmBotSessionRepository as OrmBotSessionRepositoryType,
)
from secbaas.community.core.repository.device import (
    OrmDeviceRepository as OrmDeviceRepositoryType,
)
from secbaas.community.core.repository.device_binding import (
    OrmDeviceBindingRepository as OrmDeviceBindingRepositoryType,
)
from secbaas.community.core.repository.device_template import (
    OrmDeviceTemplateRepository as OrmDeviceTemplateRepositoryType,
)
from secbaas.community.core.repository.distributed_lock import (
    OrmDistributedLockRepository as OrmDistributedLockRepositoryType,
)
from secbaas.community.core.repository.local_user_machine import (
    OrmLocalUserMachineRepository as OrmLocalUserMachineRepositoryType,
)
from secbaas.community.core.repository.publish import (
    OrmPublishRepository as OrmPublishRepositoryType,
)
from secbaas.community.core.repository.publish_batch import (
    OrmPublishBatchRepository as OrmPublishBatchRepositoryType,
)
from secbaas.community.core.repository.publish_record import (
    OrmPublishRecordRepository as OrmPublishRecordRepositoryType,
)
from secbaas.community.core.repository.system_config import (
    OrmSystemConfigRepository as OrmSystemConfigRepositoryType,
)
from secbaas.community.core.repository.tenant import (
    OrmTenantRepository as OrmTenantRepositoryType,
)
from secbaas.community.spi.database import PluginDatabaseType
from tests.utils import load_web_port

# ── 17 repositories with their expected ORM types ─────────────────────────

_ALL_REPOS = [
    ("ac_bot_repository", OrmAcBotRepositoryType),
    ("ac_bot_publish_repository", OrmAcBotPublishRepositoryType),
    ("api_gateway_repository", OrmAPIKeyRepositoryType),
    ("bot_repository", OrmBotRepositoryType),
    ("bot_device_rel_repository", OrmBotDeviceRelRepositoryType),
    ("bot_run_repository", OrmBotRunRepositoryType),
    ("bot_session_repository", OrmBotSessionRepositoryType),
    ("device_repository", OrmDeviceRepositoryType),
    ("device_binding_repository", OrmDeviceBindingRepositoryType),
    ("device_template_repository", OrmDeviceTemplateRepositoryType),
    ("distributed_lock_repository", OrmDistributedLockRepositoryType),
    ("local_user_machine_repository", OrmLocalUserMachineRepositoryType),
    ("publish_repository", OrmPublishRepositoryType),
    ("publish_batch_repository", OrmPublishBatchRepositoryType),
    ("publish_record_repository", OrmPublishRecordRepositoryType),
    ("system_config_repository", OrmSystemConfigRepositoryType),
    ("tenant_repository", OrmTenantRepositoryType),
]


# ═══════════════════════════════════════════════════════════════════════════
# Container singleton
# ═══════════════════════════════════════════════════════════════════════════


class TestContainerSingleton:
    """get_container() lazy-singleton behaviour."""

    def test_get_container_returns_singleton(self):
        """Repeated calls return the same ApplicationContainer instance."""
        c1 = get_container()
        c2 = get_container()
        assert c1 is c2
        assert isinstance(c1, Container)

    def test_singleton_persists_after_init(self):
        """Calling plugin.init_database does not invalidate the singleton."""
        c1 = get_container()
        c1.plugins.plugin_database().init_database(
            DatabaseConfig(
                plugin_type=PluginDatabaseType.SQLITE_ORM, db_url="sqlite:///:memory:"
            )
        )
        c2 = get_container()
        assert c1 is c2


# ═══════════════════════════════════════════════════════════════════════════
# Repository chain
# ═══════════════════════════════════════════════════════════════════════════


class TestRepositoryContainer:
    """RepositoryContainer resolves all 17 repos through the DI chain."""

    def test_ac_bot_orm_type(self):
        """get_container().repository.ac_bot_repository() -> OrmAcBotRepository."""
        repo = get_container().repository.ac_bot_repository()
        assert isinstance(repo, OrmAcBotRepositoryType)
        assert repo._database is not None

    @pytest.mark.parametrize("repo_attr,expected_type", _ALL_REPOS)
    def test_all_repositories_accessible(self, repo_attr, expected_type):
        """Each of the 17 repos resolves to the correct ORM variant."""
        repo = getattr(get_container().repository, repo_attr)()
        assert isinstance(repo, expected_type), (
            f"{repo_attr} should be {expected_type.__name__}, got {type(repo).__name__}"
        )

    def test_ac_bot_satisfies_protocol(self):
        """OrmAcBotRepository satisfies the runtime-checkable AcBotRepository protocol."""
        repo = get_container().repository.ac_bot_repository()
        assert isinstance(repo, AcBotRepository)

    def test_ac_bot_list_active_bots_returns_empty(self):
        """Smoke-test: list_active_bots on a fresh DB returns (0, []).

        Exercises the full @with_orm_session → db_manager → plugin chain.
        """
        repo = get_container().repository.ac_bot_repository()
        total, items = repo.list_active_bots(page=1, page_size=10, env="prod")
        assert total == 0
        assert items == []


# ═══════════════════════════════════════════════════════════════════════════
# Service container
# ═══════════════════════════════════════════════════════════════════════════


class TestServiceContainer:
    """ServiceContainer resolves with repository dependencies injected."""

    def test_session_service_constructs(self):
        """DefaultSessionService can be built — simple service with 1 repo dep."""
        service = get_container().services.session_service()
        from secbaas.community.core.service.bot_session import DefaultSessionService

        assert isinstance(service, DefaultSessionService)
        assert service._bot_session_repository is not None


# ═══════════════════════════════════════════════════════════════════════════
# Full-container config injection
# ═══════════════════════════════════════════════════════════════════════════


class TestApplicationContainerConfig:
    """ApplicationContainer accepts injected config that flows to sub-containers."""

    # ── Bot service config ───────────────────────────────────────────────

    def test_bot_service_config_flows_to_infra(self):
        """bot_service config injected at top level reaches InfraContainer."""
        container = get_container()
        container.config.from_dict(
            {
                "bot_service": {
                    "proxy_base_url": "http://cfg-test:9090",
                    "proxy_ws_base_url": "ws://cfg-test:9090",
                    "adapter_port": 20003,
                    "connect_timeout": 5,
                    "request_timeout": 25,
                },
            }
        )
        cfg = container.services.bot_service_config()
        assert cfg.proxy_base_url == "http://cfg-test:9090"
        assert cfg.connect_timeout == 5
        assert cfg.request_timeout == 25

    def test_baas_bot_service_config_flows(self):
        """baas_bot_service uses the same bot_service.* config keys."""
        container = get_container()
        container.config.from_dict(
            {
                "bot_service": {
                    "adapter_port": 20003,
                    "ws_path": "/custom/ws",
                    "connect_timeout": 3,
                    "request_timeout": 15,
                },
            }
        )
        cfg = container.services.baas_bot_service_config()
        assert cfg.adapter_port == 20003
        assert cfg.ws_path == "/custom/ws"

    # ── Health checker config ────────────────────────────────────────────

    def test_health_checker_config_flows(self):
        """Health checker config reaches CoreServiceContainer."""
        container = get_container()
        container.config.from_dict(
            {
                "bot_health_checker": {
                    "health_check": {
                        "timeout_seconds": 15,
                        "max_concurrent": 5,
                    },
                    "ttl": {
                        "extend_when_remaining_hours": 4,
                        "target_ttl_hours": 12,
                    },
                },
            }
        )
        cfg = container.services.bot_health_checker_config()
        assert cfg.health_check_timeout == 15
        assert cfg.health_check_max_concurrent == 5
        assert cfg.extend_when_remaining_hours == 4
        assert cfg.target_ttl_hours == 12

    # ── Sub-container accessibility ──────────────────────────────────────

    def test_all_sub_containers_accessible(self):
        """All 3 sub-containers are accessible from ApplicationContainer."""
        container = get_container()
        # plugins
        assert container.plugins is not None
        assert hasattr(container.plugins, "config")
        # repository
        assert container.repository is not None
        assert hasattr(container.repository, "ac_bot_repository")
        # services
        assert container.services is not None
        assert hasattr(container.services, "session_service")


class TestSandboxDeviceRouterFullChain:
    """Verify sandbox_device_router resolves through the full DI chain.

    Regression test: providers.Dict is required so that handler values
    are resolved instances, not provider objects.
    """

    def test_sandbox_device_router_resolves_via_app_container(self):
        """sandbox_device_router resolves through ApplicationContainer → services."""
        from dependency_injector.providers import Singleton

        from secbaas.community.core.service.health_check.sandbox import (
            SandboxDeviceRouter,
        )

        container = get_container()
        container.config.from_dict(
            {
                "web_port": load_web_port(),
                "plugins": {
                    "secret": "stub",
                    "sandbox": {
                        "arca": "stub",
                        "desktop": "stub",
                        "docker": "stub",
                        "k8s": "stub",
                        "teclaw": "stub",
                        "poolab": "stub",
                    },
                },
            }
        )
        router = container.services.sandbox_device_router()
        assert isinstance(router, SandboxDeviceRouter)

    def test_handlers_are_instances_not_providers_via_app_container(self):
        """Handlers are real instances, not unresolved Singleton providers.

        This is the critical regression check: if the DI definition uses
        a plain dict instead of providers.Dict(), the handler values remain
        as Singleton provider objects, causing:
            'dependency_injector.providers.Singleton' object has no attribute
            'query_active_sandboxes'
        """
        from dependency_injector.providers import Singleton

        from secbaas.community.core.service.health_check.sandbox import TableType

        container = get_container()
        container.config.from_dict(
            {
                "web_port": load_web_port(),
                "plugins": {
                    "secret": "stub",
                    "sandbox": {
                        "arca": "stub",
                        "desktop": "stub",
                        "docker": "stub",
                        "k8s": "stub",
                        "teclaw": "stub",
                        "poolab": "stub",
                    },
                },
            }
        )
        router = container.services.sandbox_device_router()
        handlers = router._handlers

        for key, handler in handlers.items():
            assert not isinstance(handler, Singleton), (
                f"Handler for {key!r} is an unresolved Singleton provider, "
                f"not an instance. Use providers.Dict() in the container "
                f"definition to ensure values are resolved."
            )

    def test_router_methods_callable_via_app_container(self):
        """Resolved router exposes query_active_sandboxes, warn_device, renew_ttl."""
        container = get_container()
        container.config.from_dict(
            {
                "web_port": load_web_port(),
                "plugins": {
                    "secret": "stub",
                    "sandbox": {
                        "arca": "stub",
                        "desktop": "stub",
                        "docker": "stub",
                        "k8s": "stub",
                        "teclaw": "stub",
                        "poolab": "stub",
                    },
                },
            }
        )
        router = container.services.sandbox_device_router()

        assert callable(getattr(router, "query_active_sandboxes", None))
        assert callable(getattr(router, "warn_device", None))
        assert callable(getattr(router, "renew_ttl", None))


class TestInjectEnterprisePluginsImportError:
    def test_import_error_is_silently_caught(self) -> None:
        import sys
        from unittest.mock import patch

        from secbaas.community.bootstrap._container import (
            _inject_enterprise_plugins,
        )

        container = object()
        with patch.dict(sys.modules, {"secbaas.community.plugin_registry": None}):
            _inject_enterprise_plugins(container)
