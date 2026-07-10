"""App-services concern — test / singlebox / corp_test binding.

YAML-or-dummy Dima config; local-only routers (``local_sql_router``); the real
``BotChatService``. ``BotChatService`` and the ``*Protocol`` aliases are
profile-independent — bound here so the column is self-contained.

B11 (3.2): this module is **corp-free**. The code-platform (AntCode) service is
bound to the community ``NoopCodePlatformService`` rather than the corp
``AntCodeService`` — the AntCode git integration is corp-only, and the endpoints
that exercise it live in ``tests/corp`` (which resolves the real service through
the ``corp`` profile, not through this test column). Used by the corp-free
``test``/``singlebox`` column **and** the ``corp_test`` column.
"""
from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.code_platform_service import CodePlatformServiceProtocol
from agentclaw.community.api.bot_chat_service import BotChatServiceProtocol
from agentclaw.community.core.bot_chat.service import BotChatService
from agentclaw.community.core.bot_management.services.aicoding.workspace_hosting_client import WorkspaceHostingClient
from agentclaw.community.di import config as cfg
from agentclaw.community.di.optional_routers import OptionalRouters
from agentclaw.community.log import get_logger


logger = get_logger()


class TestAppServicesModule(Module):
    """test / singlebox / corp_test: dummy Dima config; local routers; real BotChat."""

    def configure(self, binder: Binder) -> None:
        binder.bind(BotChatService, to=BotChatService, scope=singleton)

    @singleton
    @provider
    def _antcode_service_protocol(self) -> CodePlatformServiceProtocol:
        # Corp-free: no AntCode git integration in the community/test column.
        from agentclaw.community.plugins.community.app_services import (
            NoopCodePlatformService,
        )

        return NoopCodePlatformService()

    @singleton
    @provider
    @inject
    def _bot_chat_service_protocol(
        self, svc: BotChatService
    ) -> BotChatServiceProtocol:
        return svc

    @singleton
    @provider
    def workspace_hosting_config(self) -> cfg.WorkspaceHostingConfig:
        """Local/test WorkspaceHostingConfig: read from YAML if available, else dummy."""
        from agentclaw.community.core.config import sofa

        user_cfg = {}
        try:
            user_cfg = dict(getattr(sofa.sofa_config, "user_config", {}) or {})
        except Exception:
            pass

        block = user_cfg.get("dima", {})
        if block and block.get("base_url") and block.get("access_key"):
            logger.info("WorkspaceHostingConfig: using YAML config (test)")
            return cfg.WorkspaceHostingConfig(
                base_url=block["base_url"],
                access_key=block["access_key"],
                access_secret=block["access_secret"],
                tenant=block.get("tenant", "default"),
                timeout=int(block.get("timeout", 30)),
                # Neutral fallbacks; the aixcore endpoints come from the dima
                # block (corp env overlays set them) — OSS-0 #3.
                aixcore_base_url=block.get("aixcore_base_url", ""),
                aixcore_base_url_pre=block.get("aixcore_base_url_pre", ""),
            )

        logger.warning("WorkspaceHostingConfig: using dummy config (test, no YAML)")
        return cfg.WorkspaceHostingConfig(
            base_url="http://localhost:9999",
            access_key="dummy-access-key",
            access_secret="dummy-secret-16b",  # 16 chars for AES
            tenant="test",
            timeout=30,
            aixcore_base_url="http://localhost:9999",
            aixcore_base_url_pre="http://localhost:9999",
        )

    @singleton
    @provider
    def workspace_hosting_client(self) -> WorkspaceHostingClient:
        config = self.workspace_hosting_config()
        logger.info("WorkspaceHostingClient: local/test client")
        return WorkspaceHostingClient(config=config)

    @singleton
    @provider
    def optional_routers(self) -> OptionalRouters:
        """Routers mounted only in local boots (imported lazily to keep
        ``di/ -> api/`` cycle-free at import time)."""
        from agentclaw.community.adapters.http.local.sql import router as local_sql_router

        return OptionalRouters(routers=[local_sql_router])
