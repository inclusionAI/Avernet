"""HarnessModule — production wiring for the bot health-check harness.

All four harness repositories (HarnessPatch, HarnessTemplate,
HarnessPatchRecord, HarnessScanRecord) are unified ORM bodies bound
here and run on both OceanBase prod and SQLite tests via the injected
DatabasePlugin. The 6 services are mode-agnostic and pick up whichever
repo/plugin is bound.
"""
from __future__ import annotations

from typing import Annotated

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.content_scanner_service import ContentScannerProtocol
from agentclaw.community.api.patch_engine_service import PatchEngineProtocol
from agentclaw.community.api.patch_library_service import PatchLibraryProtocol
from agentclaw.community.api.patch_planner_service import PatchPlannerProtocol
from agentclaw.community.core.repository.protocols.harness import HarnessTemplateRepository
from agentclaw.community.core.repository.protocols.harness import HarnessScanRecordRepository
from agentclaw.community.core.repository.protocols.harness import HarnessPatchRepository
from agentclaw.community.core.repository.protocols.harness import HarnessPatchRecordRepository
from agentclaw.community.core.harness.services.bot_profile import BotProfile
from agentclaw.community.core.harness.services.content_scanner import ContentScanner
from agentclaw.community.core.harness.services.llm import LLM
from agentclaw.community.di.config import BcsFuseConfig, KbConfig, LLMHarnessConfig
from agentclaw.community.plugin_api.http_client import HttpClient, QUALIFIER_GENERAL
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.core.harness.services.patch_engine import PatchEngine
from agentclaw.community.core.harness.services.patch_library import PatchLibrary
from agentclaw.community.core.harness.services.patch_planner import PatchPlanner
from agentclaw.community.core.services.identity import IdentityService
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin
from agentclaw.community.plugins.harness_patch_record_repository import (
    HarnessPatchRecordRepository as UnifiedHarnessPatchRecordRepository,
)
from agentclaw.community.plugins.harness_patch_repository import (
    HarnessPatchRepository as UnifiedHarnessPatchRepository,
)
from agentclaw.community.plugins.harness_repository import (
    HarnessTemplateRepository as UnifiedHarnessTemplateRepository,
)
from agentclaw.community.plugins.harness_scan_repository import (
    HarnessScanRecordRepository as UnifiedHarnessScanRecordRepository,
)


logger = get_logger()


class HarnessModule(Module):
    """Production bindings for ``core/harness``."""

    def configure(self, binder: Binder) -> None:
        # LLM is constructed by the ``_llm`` provider below (injects
        # LLMHarnessConfig for its neutral base_url / secret name).
        # Unified ORM repos with @inject __init__ — the injector can
        # construct them from the bound DatabasePlugin; no @provider needed.
        binder.bind(
            HarnessPatchRepository,
            to=UnifiedHarnessPatchRepository,
            scope=singleton,
        )
        binder.bind(
            HarnessPatchRecordRepository,
            to=UnifiedHarnessPatchRecordRepository,
            scope=singleton,
        )
        binder.bind(
            HarnessTemplateRepository,
            to=UnifiedHarnessTemplateRepository,
            scope=singleton,
        )
        binder.bind(
            HarnessScanRecordRepository,
            to=UnifiedHarnessScanRecordRepository,
            scope=singleton,
        )

    # ── Mode-agnostic services ──────────────────────────────────────

    @singleton
    @provider
    @inject
    def _llm(
        self,
        llm_config: LLMHarnessConfig,
        secret_resolver: SecretResolver,
        general_http: Annotated[HttpClient, QUALIFIER_GENERAL],
    ) -> LLM:
        """Construct the harness LLM from the injected ``LLMHarnessConfig`` (the
        ``llm`` yaml block). The API token is resolved through the injected
        ``SecretResolver`` seam (corp → Mist, community → env) by the config's
        ``secret_name``, so the harness names no corp secret client. HTTP goes
        through the shared ``general`` ``HttpClient`` (sync, sofa_tracer-safe;
        callers pass absolute URLs)."""
        return LLM(
            base_url=llm_config.base_url,
            secret_name=llm_config.secret_name,
            secret_resolver=secret_resolver,
            http_client=general_http,
        )

    @singleton
    @provider
    def bot_profile(
        self,
        identity_service: IdentityService,
        skill_set_service_factory: SkillSetServiceFactory,
        path_factory: WorkspacePathFactory,
    ) -> BotProfile:
        return BotProfile(
            identity_service=identity_service,
            path_factory=path_factory,
            skill_set_service_factory=skill_set_service_factory,
        )

    @singleton
    @provider
    def patch_library(self, repo: HarnessTemplateRepository) -> PatchLibrary:
        lib = PatchLibrary(repo=repo)
        lib.load_all()
        return lib

    @singleton
    @provider
    def content_scanner(
        self,
        bot_profile: BotProfile,
        llm: LLM,
        patch_library: PatchLibrary,
        mcp_center: MCPCenterPlugin,
        bcsfuse: BcsFuseConfig,
        kb: KbConfig,
    ) -> ContentScanner:
        from agentclaw.community.utils.env_utils import get_current_env

        bcsfuse_base_url = (
            bcsfuse.base_url_pre
            if get_current_env() == "pre"
            else bcsfuse.base_url
        )
        return ContentScanner(
            bot_profile=bot_profile,
            llm=llm,
            patch_library=patch_library,
            mcp_center=mcp_center,
            bcsfuse_base_url=bcsfuse_base_url,
            kb_config=kb,
        )

    @singleton
    @provider
    def patch_engine(
        self,
        bot_profile: BotProfile,
        scanner: ContentScanner,
        db: DatabasePlugin,
        patch_record_repo: HarnessPatchRecordRepository,
        identity_service: IdentityService,
    ) -> PatchEngine:
        return PatchEngine(
            bot_profile=bot_profile,
            scanner=scanner,
            db=db,
            patch_record_repo=patch_record_repo,
            identity_service=identity_service,
        )

    @singleton
    @provider
    def patch_planner(
        self,
        patch_library: PatchLibrary,
        llm: LLM,
        bot_profile: BotProfile,
        patch_record_repo: HarnessPatchRecordRepository,
        patch_repo: HarnessPatchRepository,
        scan_record_repo: HarnessScanRecordRepository,
    ) -> PatchPlanner:
        return PatchPlanner(
            patch_library=patch_library,
            llm=llm,
            bot_profile=bot_profile,
            patch_record_repo=patch_record_repo,
            patch_repo=patch_repo,
            scan_record_repo=scan_record_repo,
        )

    # ── Service API Protocol aliases ────────────────────────────────────
    # Each @provider below resolves the concrete singleton and returns it
    # under the Protocol type, so ``Injected(<X>Protocol)`` in adapters/
    # gets the same instance as ``Injected(<X>)`` elsewhere.

    @singleton
    @provider
    @inject
    def _content_scanner_protocol(self, svc: ContentScanner) -> ContentScannerProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _patch_library_protocol(self, svc: PatchLibrary) -> PatchLibraryProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _patch_engine_protocol(self, svc: PatchEngine) -> PatchEngineProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _patch_planner_protocol(self, svc: PatchPlanner) -> PatchPlannerProtocol:
        return svc
