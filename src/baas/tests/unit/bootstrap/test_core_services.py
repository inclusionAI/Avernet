"""Tests for CoreServiceContainer — standalone, no database needed.

Config-based providers (bot_health_checker_config) are testable with
config.from_dict.  Services requiring repo/infra deps are verified
structurally — they exist and accept overrides.
"""

from unittest.mock import MagicMock

import pytest

from secbaas.community.api.health_check.bot import BotHealthCheckerConfig
from secbaas.community.bootstrap._core_services import CoreServiceContainer


class TestCoreServiceContainerStandalone:
    """CoreServiceContainer can be created standalone with overridden deps."""

    def test_container_can_be_instantiated(self):
        """CoreServiceContainer constructs with no arguments."""
        container = CoreServiceContainer()
        assert container is not None

    def test_config_key_paths_are_accessible(self):
        """Config injection via from_dict reads health_check paths."""
        container = CoreServiceContainer()
        container.config.from_dict(
            {
                "bot_health_checker": {
                    "health_check": {
                        "timeout_seconds": 5,
                        "max_concurrent": 20,
                    },
                    "ttl": {
                        "extend_when_remaining_hours": 8,
                        "target_ttl_hours": 48,
                    },
                },
            }
        )
        assert container.config.bot_health_checker.health_check.timeout_seconds() == 5
        assert container.config.bot_health_checker.ttl.target_ttl_hours() == 48

    def test_all_provider_attributes_exist(self):
        """All service provider attributes are present."""
        container = CoreServiceContainer()
        expected = [
            "bot_health_checker_config",
            "paas_health_provider_factory",
            "device_binding_query_service",
            "bot_health_checker_service",
            "device_service",
            "session_service",
            "system_config_service",
            "api_key_service",
            "api_key_validator",
            "tenant_service",
            "bot_crud_service",
            "publish_service",
            "bot_management_service",
            "publish_admin_service",
            "bot_service_selector",
        ]
        for attr in expected:
            assert hasattr(container, attr), f"Missing provider: {attr}"

    def test_bot_health_checker_config_resolves_with_config(self):
        """bot_health_checker_config resolves when config is set."""
        container = CoreServiceContainer()
        container.config.from_dict(
            {
                "bot_health_checker": {
                    "health_check": {
                        "timeout_seconds": 10,
                        "max_concurrent": 10,
                    },
                    "ttl": {
                        "extend_when_remaining_hours": 16,
                        "target_ttl_hours": 24,
                    },
                },
            }
        )
        cfg = container.bot_health_checker_config()
        assert isinstance(cfg, BotHealthCheckerConfig)
        assert cfg.health_check_timeout == 10
        assert cfg.health_check_max_concurrent == 10
        assert cfg.extend_when_remaining_hours == 16
        assert cfg.target_ttl_hours == 24

    def test_resolve_session_service_with_mocked_deps(self):
        """session_service resolves when bot_session_repo is overridden."""
        container = CoreServiceContainer()
        container.bot_session_repo.override(MagicMock())
        from secbaas.community.core.service.bot_session import DefaultSessionService

        service = container.session_service()
        assert isinstance(service, DefaultSessionService)

    def test_resolve_tenant_service_with_mocked_deps(self):
        """tenant_service resolves when tenant_repo is overridden."""
        container = CoreServiceContainer()
        container.tenant_repo.override(MagicMock())
        from secbaas.community.core.service.tenant_manage import (
            DefaultTenantManageService,
        )

        service = container.tenant_service()
        assert isinstance(service, DefaultTenantManageService)

    def test_resolve_system_config_service_with_mocked_deps(self):
        """system_config_service resolves when system_config_repo is overridden."""
        container = CoreServiceContainer()
        container.system_config_repo.override(MagicMock())
        from secbaas.community.core.service.config_manage import (
            DefaultSystemConfigManageService,
        )

        service = container.system_config_service()
        assert isinstance(service, DefaultSystemConfigManageService)

    def test_resolve_api_key_service_with_mocked_deps(self):
        """api_key_service resolves when api_gateway_repo is overridden."""
        container = CoreServiceContainer()
        container.api_gateway_repo.override(MagicMock())
        from secbaas.community.core.service.api_gateway import DefaultAPIKeyService

        service = container.api_key_service()
        assert isinstance(service, DefaultAPIKeyService)


class TestSandboxDeviceRouterDIResolution:
    """Verify sandbox_device_router resolves with actual handler instances.

    Regression test: providers.Dict is required for handler mapping so that
    dependency_injector resolves each value to its instance rather than
    leaving a providers.Singleton object in the dict.
    See: https://github.com/ets-labs/python-dependency-injector/issues/756
    """

    @pytest.fixture()
    def container(self):
        """Create a CoreServiceContainer with all Dependency() providers overridden."""
        c = CoreServiceContainer()
        # Override all Dependency() providers so resolution doesn't fail
        for dep_name in (
            "bot_repo",
            "device_repo",
            "ac_bot_repo",
            "ac_bot_publish_repo",
            "device_binding_repo",
            "api_gateway_repo",
            "bot_device_rel_repo",
            "bot_session_repo",
            "publish_repo",
            "publish_batch_repo",
            "publish_record_repo",
            "tenant_repo",
            "system_config_repo",
            "device_template_repo",
            "local_user_machine_repo",
            "bot_run_repository",
        ):
            getattr(c, dep_name).override(MagicMock())
        # Override paas_facade (deep dep chain: device_repo → template → factory)
        c.paas_facade.override(MagicMock())
        yield c
        for dep_name in (
            "bot_repo",
            "device_repo",
            "ac_bot_repo",
            "ac_bot_publish_repo",
            "device_binding_repo",
            "api_gateway_repo",
            "bot_device_rel_repo",
            "bot_session_repo",
            "publish_repo",
            "publish_batch_repo",
            "publish_record_repo",
            "tenant_repo",
            "system_config_repo",
            "device_template_repo",
            "local_user_machine_repo",
            "bot_run_repository",
        ):
            getattr(c, dep_name).reset_override()
        c.paas_facade.reset_override()

    def test_sandbox_device_router_resolves(self, container):
        """sandbox_device_router resolves to a SandboxDeviceRouter instance."""
        from secbaas.community.core.service.health_check.sandbox import (
            SandboxDeviceRouter,
        )

        router = container.sandbox_device_router()
        assert isinstance(router, SandboxDeviceRouter)

    def test_handlers_are_instances_not_providers(self, container):
        """Handlers dict values are resolved instances, not provider objects.

        This is the core regression check: if handlers is a plain dict
        (not providers.Dict), the values remain Singleton provider objects
        and calling handler.query_active_sandboxes() would fail with:
            'dependency_injector.providers.Singleton' object has no attribute ...
        """
        from dependency_injector.providers import Singleton

        router = container.sandbox_device_router()
        handlers = router._handlers

        for key, handler in handlers.items():
            assert not isinstance(handler, Singleton), (
                f"Handler for {key!r} is an unresolved Singleton provider, "
                f"not an instance. Did you use a plain dict instead of "
                f"providers.Dict() in the container definition?"
            )

    def test_handlers_have_correct_types(self, container):
        """Each handler in the dict is the expected concrete type."""
        from secbaas.community.core.service.health_check.sandbox import (
            AcBindingSandboxHandler,
            BaasSandboxHandler,
            TableType,
        )

        router = container.sandbox_device_router()
        handlers = router._handlers

        assert TableType.AC_BINDING in handlers
        assert TableType.BAAS in handlers
        assert isinstance(handlers[TableType.AC_BINDING], AcBindingSandboxHandler)
        assert isinstance(handlers[TableType.BAAS], BaasSandboxHandler)

    def test_router_has_required_methods(self, container):
        """SandboxDeviceRouter exposes query_active_sandboxes, warn_device, renew_ttl."""
        router = container.sandbox_device_router()

        assert callable(getattr(router, "query_active_sandboxes", None))
        assert callable(getattr(router, "warn_device", None))
        assert callable(getattr(router, "renew_ttl", None))
