"""Integration tests for bootstrap container initialization.

Verifies that the ``bootstrap_init`` fixture creates a fully wired
ApplicationContainer with all repositories, services, and plugins
resolved correctly.
"""

from __future__ import annotations

import pytest

from secbaas.community.bootstrap import ApplicationContainer, get_container
from secbaas.community.plugins.auth.stub import StubAuthPlugin
from secbaas.community.plugins.cache.stub import StubCachePlugin
from secbaas.community.plugins.sandbox.arca import StubArcaSandboxPlugin
from secbaas.community.plugins.sandbox.desktop import StubDesktopSandboxPlugin
from secbaas.community.plugins.sandbox.k8s import StubK8sSandboxPlugin
from secbaas.community.plugins.sandbox.teclaw import StubTeClawBotPlugin
from secbaas.community.plugins.secret.stub import StubSecretStorePlugin


class TestBootstrapInitialization:
    """Verify the bootstrap container is created and wired correctly."""

    @pytest.mark.integration
    def test_container_is_created_and_config_loaded(
        self, bootstrap_init: ApplicationContainer
    ) -> None:
        """bootstrap_init yields a non-None container with loaded config."""
        assert bootstrap_init is not None

        # Config should have been loaded via from_dict()
        config: dict[str, object] = bootstrap_init.config()
        assert config is not None
        assert isinstance(config, dict)
        # At minimum the overlay itself must be set
        assert "plugins" in config, "Config should contain plugins section"

        # get_container() should return the same container
        assert get_container() is bootstrap_init

    @pytest.mark.integration
    def test_all_repository_providers_resolve(
        self, bootstrap_init: ApplicationContainer
    ) -> None:
        """All 17 repository providers resolve without errors."""
        repo = bootstrap_init.repository

        # Resolve each repository — a clean resolution proves wiring
        repo.ac_bot_repository()  # ac_bot
        repo.ac_bot_publish_repository()  # ac_bot_publish
        repo.api_gateway_repository()  # api_gateway
        repo.bot_repository()  # bot
        repo.bot_device_rel_repository()  # bot_device_rel
        repo.bot_run_repository()  # bot_run
        repo.bot_session_repository()  # bot_session
        repo.device_repository()  # device
        repo.device_binding_repository()  # device_binding
        repo.device_template_repository()  # device_template
        repo.distributed_lock_repository()  # distributed_lock
        repo.local_user_machine_repository()  # local_user_machine
        repo.publish_repository()  # publish
        repo.publish_batch_repository()  # publish_batch
        repo.publish_record_repository()  # publish_record
        repo.system_config_repository()  # system_config
        repo.tenant_repository()  # tenant

    @pytest.mark.integration
    def test_all_core_service_providers_resolve(
        self, bootstrap_init: ApplicationContainer
    ) -> None:
        """All key service providers (20+) resolve cleanly."""
        svc = bootstrap_init.services

        svc.auth_service()  # AuthService
        svc.tenant_service()  # DefaultTenantManageService
        svc.device_template_service()  # DefaultDeviceTemplateService
        svc.paas_facade()  # PaasServiceFacade
        svc.bot_http_dispatcher()  # DefaultBotHttpDispatcher
        svc.bot_wss_dispatcher()  # DefaultBotWssDispatcher
        svc.bot_cmd_dispatcher()  # DefaultBotCmdDispatcher
        svc.bot_open_folder_dispatcher()  # DefaultBotOpenFolderDispatcher
        svc.device_service()  # DefaultDeviceService
        svc.session_service()  # DefaultSessionService
        svc.system_config_service()  # DefaultSystemConfigManageService
        svc.api_key_service()  # DefaultAPIKeyService
        svc.bot_crud_service()  # DefaultBotCrudService
        svc.publish_service()  # DefaultPublishService
        svc.bot_management_service()  # DefaultBotManagementService
        svc.publish_admin_service()  # DefaultPublishAdminService
        svc.bot_runner()  # BotRunner
        svc.bcn_uplink_client()  # BcnUplinkClient
        svc.bcn_downlink_service()  # DefaultBcnDownlinkService
        svc.sandbox_device_router()  # SandboxDeviceRouter
        svc.bot_health_checker_service()  # BotHealthCheckerService (21st)
        svc.bot_service_selector()  # BotServiceSelector (22nd)

    @pytest.mark.integration
    def test_plugins_loaded_with_stub_types(
        self, bootstrap_init: ApplicationContainer
    ) -> None:
        """In it-zdas overlay, every plugin resolves to its stub type."""
        plugins = bootstrap_init.plugins

        # provider.Singleton() providers → instances
        auth = plugins.auth_plugin()
        assert isinstance(auth, StubAuthPlugin)

        cache = plugins.cache_plugin()
        assert isinstance(cache, StubCachePlugin)

        secret = plugins.secret_plugin()
        assert isinstance(secret, StubSecretStorePlugin)

        desktop = plugins.desktop_sandbox_plugin()
        assert isinstance(desktop, StubDesktopSandboxPlugin)

        # provider.Object() providers → classes (not instances)
        arca = plugins.arca_sandbox_plugin_factory()
        assert arca is StubArcaSandboxPlugin

        teclaw = plugins.teclaw_bot_plugin_factory()
        assert teclaw is StubTeClawBotPlugin

        k8s = plugins.k8s_sandbox_plugin_factory()
        assert k8s is StubK8sSandboxPlugin

    @pytest.mark.integration
    def test_database_plugin_resolved(
        self, bootstrap_init: ApplicationContainer
    ) -> None:
        """Database manager has a plugin set."""
        db_manager = bootstrap_init.repository.db_manager()
        assert db_manager is not None

        plugin = db_manager._plugin
        assert plugin is not None, "DatabaseManager should have a plugin set"
