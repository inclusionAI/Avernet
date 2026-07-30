"""Central DI container construction.

``build_injector`` is the single place where the module list lives. Each
per-business-module ``Module`` is added here as it migrates onto DI.

``eager_check_critical_bindings`` is a startup integrity check that
``api/app.py`` runs when ``SERVER_ENV`` resolves to ``pre`` or
``prod``. It crashes loudly on boot if a critical binding is missing
instead of deferring the failure to first request. ``dev`` / local
boots skip it to keep startup snappy and to tolerate the prod-only
deps (ZDAS handle, Arca sandbox) that aren't reachable locally.
"""
from __future__ import annotations

from collections.abc import Iterable

from injector import Injector, Module

from agentclaw.community.di.modules.access_module import AccessModule
from agentclaw.community.di.modules.aicoding_module import AICodingModule
from agentclaw.community.di.modules.bot_collaborator_module import BotCollaboratorModule
from agentclaw.community.di.modules.bot_dormant_module import BotDormantModule
from agentclaw.community.di.modules.bot_management_module import BotManagementModule
from agentclaw.community.di.modules.bot_public_module import BotPublicModule
from agentclaw.community.di.modules.caller_identity_module import CallerIdentityModule
from agentclaw.community.di.modules.channel_module import ChannelModule
from agentclaw.community.di.modules.common_config_module import CommonConfigModule
from agentclaw.community.di.modules.config_module import ConfigModule
from agentclaw.community.di.modules.cron_module import CronModule
from agentclaw.community.di.modules.engine_runtime_module import EngineRuntimeModule
from agentclaw.community.di.modules.desktop_bot_module import DesktopBotModule
from agentclaw.community.di.modules.devices_module import DevicesModule
from agentclaw.community.di.modules.expert_chat_module import ExpertChatModule
from agentclaw.community.di.modules.engine_config_module import EngineConfigModule
from agentclaw.community.di.modules.grt_chat_module import GrtChatModule
from agentclaw.community.di.modules.harness_module import HarnessModule
from agentclaw.community.di.modules.identity_module import IdentityModule
from agentclaw.community.di.modules.http_client_module import HttpClientModule
from agentclaw.community.di.modules.mcp_module import McpModule
from agentclaw.community.di.modules.quality_module import QualityModule
from agentclaw.community.di.modules.resources_module import ResourcesModule
from agentclaw.community.di.modules.service_bot_module import ServiceBotModule
from agentclaw.community.di.modules.session_resources_module import SessionResourcesModule
from agentclaw.community.di.modules.skill_center_module import SkillCenterModule
from agentclaw.community.di.modules.skills_pool_module import SkillsPoolModule
from agentclaw.community.di.modules.system_config_module import SystemConfigModule
from agentclaw.community.di.modules.task_queue_module import TaskQueueModule
from agentclaw.community.di.modules.economy_governance_module import EconomyGovernanceModule
from agentclaw.community.di.modules.user_list_module import UserListModule
from agentclaw.community.di.profile import DeployProfile
from agentclaw.community.di.profile_modules import modules_for
from agentclaw.community.log import get_logger


logger = get_logger()

# Global injector instance, set by build_injector after construction.
# Used by get_app_injector() for service locator pattern in legacy code.
_app_injector: Injector | None = None


def get_app_injector() -> Injector:
    """Return the global application injector.

    Raises:
        RuntimeError: If the injector has not been initialized yet.
    """
    if _app_injector is None:
        raise RuntimeError(
            "DI injector not initialized. "
            "Ensure build_injector() has been called during app startup."
        )
    return _app_injector


def build_injector(
    *,
    profile: DeployProfile,
    extra_modules: Iterable[Module] | None = None,
) -> Injector:
    """Construct the application's ``Injector``.

    The **base list** holds profile-independent business modules plus shared
    default bindings such as ``AccessModule`` and ``HttpClientModule``. Exactly
    one profile-specific column from ``modules_for(profile)`` is installed
    afterward. That column supplies profile-owned infrastructure and may
    intentionally override selected base keys; Injector's last binding wins.

    ``profile`` (a ``DeployProfile``) is resolved once at the composition
    root from the mandatory ``DEPLOY_PROFILE`` switch. ``extra_modules``
    lets a caller (or test) install additional bindings on top — e.g. an
    intra-profile override or a per-test fake.
    """
    modules: list[Module] = [
        ConfigModule(),
        SkillCenterModule(),
        ServiceBotModule(),
        DesktopBotModule(),
        SystemConfigModule(),
        CommonConfigModule(),
        BotManagementModule(),
        SkillsPoolModule(),
        BotPublicModule(),
        DevicesModule(),
        McpModule(),
        AICodingModule(),
        CronModule(),
        EngineRuntimeModule(),
        ExpertChatModule(),
        GrtChatModule(),
        IdentityModule(),
        EngineConfigModule(),
        ChannelModule(),
        AccessModule(),
        ResourcesModule(),
        SessionResourcesModule(),
        HarnessModule(),
        BotCollaboratorModule(),
        CallerIdentityModule(),
        UserListModule(),
        BotDormantModule(),
        TaskQueueModule(),
        QualityModule(),
        # HTTP transport (real httpx) — shared by every base profile. Only the
        # test/corp_test columns override these keys with LocalHttpClient;
        # singlebox intentionally uses the real clients for local services.
        HttpClientModule(),
        EconomyGovernanceModule(),
    ]

    modules.extend(modules_for(profile))

    if extra_modules:
        modules.extend(extra_modules)
    global _app_injector
    _app_injector = Injector(modules)
    return _app_injector


# ── Eager bootstrap check ───────────────────────────────────────────────

def eager_check_critical_bindings(injector: Injector) -> None:
    """Resolve a small allowlist of critical bindings to surface
    misconfiguration at startup rather than first request.

    The list is intentionally short — only bindings whose absence
    breaks the app's core operations (auth, database, the typed
    configs that downstream services derive from). Resolving them
    here forces their providers to run; any unbound dep raises
    ``UnsatisfiedRequirement`` immediately.

    Call site: ``api/app.py`` runs this when ``SERVER_ENV`` resolves
    to ``pre`` or ``prod``. ``dev`` / local boots skip it because
    some prod-only deps (e.g. the ZDAS handle + the corp-registered critical
    config bindings) aren't expected to resolve cleanly under SQLite.
    """
    from agentclaw.community.di.modules_bootstrap import get_eager_check_keys
    from agentclaw.community.plugin_api.auth import AuthPlugin
    from agentclaw.community.plugin_api.database import DatabasePlugin

    # Neutral critical bindings + any corp-only ones the corp composition root
    # registered (BuserviceSsoConfig / ArcaSandboxConfig). container.py names no
    # corp config type — the corp keys come through the registry (B8, pt 2).
    critical: list[type] = [
        DatabasePlugin,
        AuthPlugin,
        *get_eager_check_keys(),
    ]

    missing: list[str] = []
    for key in critical:
        try:
            injector.get(key)
        except Exception as exc:  # noqa: BLE001 — bubble up via aggregate
            missing.append(f"{key.__module__}.{key.__name__}: {exc}")

    if missing:
        raise RuntimeError(
            "DI eager-check failed: the following critical bindings did "
            "not resolve. Fix the bindings before deploying.\n  "
            + "\n  ".join(missing)
        )
    logger.info(
        "[DI] eager-check OK: resolved %s critical bindings", len(critical)
    )
