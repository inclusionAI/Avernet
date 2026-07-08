"""``modules_for(profile)`` — the concern × profile matrix selector.

The single place that maps a ``DeployProfile`` to its infrastructure
module column. ``build_injector`` installs exactly one column on top of
the profile-independent business modules; there is no "prod base +
overrides". Keeping the mapping here (one function) makes the composition
root and the test fixtures impossible to drift apart.

Imports are function-local per branch so a profile only imports its own
column — the ``corp`` and ``community`` columns are import-disjoint.
"""
from __future__ import annotations

from injector import Module

from agentclaw.community.di.profile import DeployProfile


def _common_test_doubles() -> list[Module]:
    """The corp-free doubles shared by the ``test``/``singlebox`` and ``corp_test``
    columns — every LOCAL-stub concern that does **not** touch ``agentclaw.corp``.

    The corp-touching concerns (devices, outbound-rules, device-sync, app-services,
    and the four reuse modules) are added per-branch by :func:`modules_for`: the
    community equivalents for ``test``/``singlebox``, the corp-flavored ones for
    ``corp_test``. Imports are function-local so this helper stays corp-free.
    """
    from agentclaw.community.di.modules.infrastructure.test.cache import TestCacheModule
    from agentclaw.community.di.modules.infrastructure.test.health import TestHealthModule
    from agentclaw.community.di.modules.infrastructure.test.http_client import (
        TestHttpClientModule,
    )
    from agentclaw.community.di.modules.infrastructure.test.identity import TestIdentityModule
    from agentclaw.community.di.modules.infrastructure.test.secret import TestSecretModule
    from agentclaw.community.di.modules.infrastructure.test.skill_center import (
        TestSkillCenterClientModule,
    )
    from agentclaw.community.di.modules.infrastructure.test.tracer import TestTracerModule
    from agentclaw.community.di.modules.infrastructure.test.drm import TestDRMModule
    from agentclaw.community.di.modules.infrastructure.test.sandbox_runtime import (
        TestSandboxRuntimeModule,
    )
    from agentclaw.community.di.modules.infrastructure.test.approval_workflow import (
        TestApprovalWorkflowModule,
    )
    from agentclaw.community.di.modules.infrastructure.test.bot_publish_approval import (
        TestBotPublishApprovalModule,
    )
    from agentclaw.community.di.modules.testing_access_module import TestingAccessModule
    from agentclaw.community.di.modules.testing_aicoding_module import TestingAicodingModule
    from agentclaw.community.di.modules.testing_database_module import TestingDatabaseModule
    from agentclaw.community.di.modules.testing_mcp_module import TestingMcpModule
    from agentclaw.community.di.modules.testing_skill_center_module import (
        TestingSkillCenterModule,
    )

    return [  # noqa: FLA002 — a fixed module list, not many distinct return values
        # Per-concern overrides for non-infrastructure concerns.
        # TestingAccessModule overrides PolicyServiceProtocol: singlebox →
        # LocalPolicyService (all-open), pytest → real PolicyService.
        TestingAccessModule(),
        TestApprovalWorkflowModule(),
        TestBotPublishApprovalModule(),
        # WorkspaceHostingService stub (offline DIMA) — corp-free.
        TestingAicodingModule(),
        # SQLite + per-module repos (TestingDatabaseModule owns the SQLite engine;
        # TestingSkillCenterModule binds the MockOss double).
        TestingDatabaseModule(),
        TestingSkillCenterModule(),
        TestingMcpModule(),
        # Decomposed per-concern test modules.
        TestCacheModule(),
        TestSecretModule(),
        TestIdentityModule(),
        TestHealthModule(),
        TestTracerModule(),
        TestDRMModule(),
        TestSandboxRuntimeModule(),
        # HTTP clients — corp-free (LocalHttpClient under pytest; community HttpxClient
        # under singlebox).
        TestHttpClientModule(),
        TestSkillCenterClientModule(),
    ]


def modules_for(profile: DeployProfile) -> list[Module]:
    """Return the infrastructure module column for ``profile``.

    This is the concern × profile matrix selector: each profile installs
    exactly one variant per concern — no "prod base + overrides". Imports
    are function-local per branch so a profile only imports its own column
    (the ``corp`` and ``community`` columns are import-disjoint).

    The former ``InfrastructureModule`` / ``TestingInfrastructureModule``
    monoliths are fully decomposed (B1 Group C) into the per-concern modules
    under ``di/modules/infrastructure/``. A few concerns still ride a shared
    ``Testing*`` module (devices / mcp / skill_center / antprocess / …) until
    their owning B-SDD decomposes them.
    """
    if profile is DeployProfile.CORP:
        # The corp column lives in corp-only code (``infrastructure.corp.column``)
        # and is supplied through the ``modules_bootstrap`` registry, which the
        # composition root populates via ``register_corp_modules(CORP)`` before
        # ``build_injector``. This file names no ``infrastructure.corp`` module so
        # the community distribution ships a corp-free profile selector (B8).
        from agentclaw.community.di.modules_bootstrap import get_corp_modules

        return get_corp_modules()

    if profile in (DeployProfile.TEST, DeployProfile.SINGLEBOX):
        # B11 (3.2): the test/singlebox column is **corp-free** — every concern that
        # the corp-flavored column borrowed from corp is bound to a community/neutral
        # equivalent here, so these profiles import no ``agentclaw.corp`` (verified by
        # the corp-absent staged run). ``corp_test`` (below) keeps the corp-flavored
        # doubles for tests/corp. Imports are function-local so the community
        # distribution never imports the corp-flavored branch.
        from agentclaw.community.di.modules.infrastructure.test.devices import (
            TestDevicesModule,
        )
        from agentclaw.community.di.modules.infrastructure.test.token_vault import (
            TestTokenVaultModule,
        )
        from agentclaw.community.di.modules.infrastructure.test.app_services import (
            TestAppServicesModule,
        )
        from agentclaw.community.di.modules.infrastructure.community.aicoding import (
            CommunityAICodingModule,
        )
        from agentclaw.community.di.modules.infrastructure.community.device_sync import (
            CommunityDeviceSyncModule,
        )
        from agentclaw.community.di.modules.infrastructure.community.governance import (
            CommunityGovernanceModule,
        )
        from agentclaw.community.di.modules.infrastructure.community.outbound_rules import (
            CommunityOutboundRulesModule,
        )

        return _common_test_doubles() + [
            # Token vault — empty-key (encrypt = passthrough); no SecretResolver dep.
            TestTokenVaultModule(),
            # The reuse column's concerns, now community:
            CommunityAICodingModule(),      # empty workflow catalog (no AntCode)
            CommunityGovernanceModule(),    # no-op notify sender (no DingTalk)
            # Outbound rules + device-sync — community (empty rules / no-op dispatch).
            CommunityOutboundRulesModule(),
            CommunityDeviceSyncModule(),
            # App services — corp-free test module: real BotChatService, local_sql
            # router, dummy Dima config, community no-op code-platform (AntCode).
            TestAppServicesModule(),
            # Devices — corp-free local/SQLite doubles (no ARCA factory / corp config).
            # Installed LAST on purpose: ``CommunityDeviceSyncModule`` also binds
            # ``DeviceAdapterTransport`` to the community no-op, but the test column
            # needs the stateful ``InMemoryDeviceAdapterTransport`` (cron gateway
            # contract tests). Injector is last-wins, so ``TestDevicesModule`` must
            # come after ``CommunityDeviceSyncModule`` to reinstate the in-memory
            # transport.
            TestDevicesModule(),
        ]

    if profile is DeployProfile.CORP_TEST:
        # The monorepo-only corp test column: the same corp-free common doubles plus
        # the corp-flavored modules (corp reuse column + the corp-flavored ``Test*``
        # doubles, e.g. the MagicMock ARCA sandbox factory) so tests/corp keeps its
        # corp bindings. Supplied via the modules_bootstrap registry (populated by
        # register_corp_modules(CORP_TEST) at the composition root), so this file
        # names no infrastructure.corp module (B8). Function-local imports keep the
        # corp-flavored ``Test*`` modules out of the community distribution's import
        # graph (this branch is never taken there).
        from agentclaw.community.di.modules_bootstrap import get_test_corp_modules
        from agentclaw.community.di.modules.infrastructure.test.app_services import (
            TestAppServicesModule,
        )
        from agentclaw.community.di.modules.infrastructure.test.outbound_rules import (
            TestOutboundRulesModule,
        )
        from agentclaw.community.di.modules.infrastructure.test.device_sync import (
            TestDeviceSyncModule,
        )
        from agentclaw.community.di.modules.testing_devices_module import (
            TestingDevicesModule,
        )

        column: list[Module] = _common_test_doubles() + [
            # Corp-flavored device doubles (MagicMock ARCA factory + config_corp).
            TestingDevicesModule(),
            # Prod outbound-rule builder / device-sync dispatcher (corp), reused
            # by the corp suite under local doubles.
            TestOutboundRulesModule(),
            TestDeviceSyncModule(),
            # App services — the SAME corp-free module the test/singlebox column uses.
            # NOTE(totalfrank): it binds ``CodePlatformServiceProtocol`` to the
            # community ``NoopCodePlatformService``, NOT the corp ``AntCodeService``,
            # even under corp_test. No tests/corp case resolves corp AntCode through
            # the DI-built corp_test injector today (the corp-binding-parity test
            # builds a full ``CORP`` injector directly). If corp AntCode DI coverage
            # is ever needed under corp_test, add a corp app-services overlay that
            # rebinds ``CodePlatformServiceProtocol`` to ``AntCodeService`` here.
            TestAppServicesModule(),
        ]
        # Corp modules the test column reuses (config providers + profile-blind
        # codefuse vault + corp AICoding services + DingTalk governance) — supplied
        # via the registry so this file names no infrastructure.corp module.
        column.extend(get_test_corp_modules())
        return column

    if profile is DeployProfile.COMMUNITY:
        from agentclaw.community.di.modules.infrastructure.community.aicoding import (
            CommunityAICodingModule,
        )
        from agentclaw.community.di.modules.infrastructure.community.app_services import (
            CommunityAppServicesModule,
        )
        from agentclaw.community.di.modules.infrastructure.community.cache import (
            CommunityCacheModule,
        )
        from agentclaw.community.di.modules.infrastructure.community.database import (
            CommunityDatabaseModule,
        )
        from agentclaw.community.di.modules.infrastructure.community.devices import (
            CommunityDevicesModule,
        )
        from agentclaw.community.di.modules.infrastructure.community.health import (
            CommunityHealthModule,
        )
        from agentclaw.community.di.modules.infrastructure.community.identity import (
            CommunityIdentityModule,
        )
        from agentclaw.community.di.modules.infrastructure.community.mcp_center import (
            CommunityMcpCenterModule,
        )
        from agentclaw.community.di.modules.infrastructure.community.object_storage import (
            CommunityObjectStorageModule,
        )
        from agentclaw.community.di.modules.infrastructure.community.secret import (
            CommunitySecretModule,
        )
        from agentclaw.community.di.modules.infrastructure.community.skill_center import (
            CommunitySkillCenterClientModule,
        )
        from agentclaw.community.di.modules.infrastructure.community.tracer import (
            CommunityTracerModule,
        )
        from agentclaw.community.di.modules.infrastructure.community.outbound_rules import (
            CommunityOutboundRulesModule,
        )
        from agentclaw.community.di.modules.infrastructure.community.drm import (
            CommunityDRMModule,
        )
        from agentclaw.community.di.modules.infrastructure.community.sandbox_runtime import (
            CommunitySandboxRuntimeModule,
        )
        from agentclaw.community.di.modules.infrastructure.community.device_sync import (
            CommunityDeviceSyncModule,
        )
        from agentclaw.community.di.modules.infrastructure.community.approval_workflow import (
            CommunityApprovalWorkflowModule,
        )
        from agentclaw.community.di.modules.infrastructure.community.bot_publish_approval import (
            CommunityBotPublishApprovalModule,
        )
        from agentclaw.community.di.modules.infrastructure.community.governance import (
            CommunityGovernanceModule,
        )

        column: list[Module] = [
            # Decomposed per-concern community infrastructure modules.
            CommunityCacheModule(),
            CommunitySecretModule(),
            CommunityDatabaseModule(),
            CommunityObjectStorageModule(),
            CommunityIdentityModule(),
            CommunityHealthModule(),
            CommunityTracerModule(),
            CommunityOutboundRulesModule(),
            CommunityDRMModule(),
            CommunitySandboxRuntimeModule(),
            CommunityDeviceSyncModule(),
            # BaaS-only device service router + DeviceAccessor (B9).
            CommunityDevicesModule(),
            # HTTP transport is the neutral base ``HttpClientModule`` (community
            # had no profile-specific HTTP dependency) — installed in the base
            # module list for every profile.
            CommunityMcpCenterModule(),
            CommunitySkillCenterClientModule(),
            # AICoding (empty workflow catalog) + app services (no-op AntCode/BotChat).
            CommunityAICodingModule(),
            CommunityAppServicesModule(),
            # Approval workflow + publish-approval strategy (B7).
            CommunityApprovalWorkflowModule(),
            CommunityBotPublishApprovalModule(),
            # Governance notify sender — no-op (no DingTalk in community; B11 Phase A).
            CommunityGovernanceModule(),
        ]
        return column

    raise ValueError(f"Unhandled deploy profile: {profile!r}")
