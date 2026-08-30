"""引擎感知挂载点 / sessions 路径测试。

覆盖 openspec/changes/archive/2026-05-25-service-bot-claudecode-engine
中的 spec：engine-aware-baas。
"""
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.common_config import CommonWhiteListService
from agentclaw.community.core.service_bot.services.deploy.managed_composer import (
    ManagedDeployConfigComposer,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasService, Storage
from agentclaw.community.core.workspace.engine_sandbox import EngineSandboxRegistry
from agentclaw.community.core.workspace.engines.aicoding import AICodingSandboxProvider
from agentclaw.community.core.workspace.engines.claude_code import ClaudeCodeSandboxProvider
from agentclaw.community.core.workspace.engines.openclaw import OpenClawSandboxProvider
from agentclaw.community.core.workspace.engines.hermes import HermesSandboxProvider
from agentclaw.community.di import config as cfg
from agentclaw.community.plugins.local.http_client import LocalHttpClient
from agentclaw.community.plugins.local.outbound_rules import NoopOutboundRuleProvider


def _make_registry() -> EngineSandboxRegistry:
    # Tests pin the workspace roots to the prod sandbox defaults so the
    # base_path / mount-point assertions don't depend on the dev
    # machine's $HOME.
    workspace = cfg.WorkspaceConfig()
    registry = EngineSandboxRegistry()
    registry.register(OpenClawSandboxProvider(workspace=workspace))
    registry.register(ClaudeCodeSandboxProvider(workspace=workspace))
    registry.register(AICodingSandboxProvider(workspace=workspace))
    registry.register(HermesSandboxProvider(workspace=workspace))
    return registry


def _make_storage_path() -> MagicMock:
    sp = MagicMock()
    sp.get_bolt_data_path.return_value = "bolt-data/staff/u1/b1"
    sp.get_skills_repo_path.return_value = "skills-repo/b1"
    return sp


def _make_composer(storage_path=None, bot_repo=None) -> ManagedDeployConfigComposer:
    """Mounts and storage are the managed image's layout, so they now live on
    ``ManagedDeployConfigComposer``. Tests of that layout target it directly."""
    return ManagedDeployConfigComposer(
        storage_path=storage_path or _make_storage_path(),
        sandbox_registry=_make_registry(),
        bot_repo=bot_repo or MagicMock(),
    )


def _make_service(storage_path=None, bot_repo=None, common_whitelist_service=None) -> BaasService:
    startup_script_reader = MagicMock()
    startup_script_reader.get_body.return_value = ""
    storage_path = storage_path or _make_storage_path()
    bot_repo = bot_repo or MagicMock()
    return BaasService(
        # Same collaborators as the service: the payload tests below assert on
        # what the composer builds from them.
        deploy_composer=_make_composer(
            storage_path=storage_path, bot_repo=bot_repo
        ),
        baas_api_base="http://test",
        tenant="test",
        template_uuid="test",
        bot_repo=bot_repo,
        bot_publish_repo=MagicMock(),
        system_config_service=MagicMock(),
        storage_path=storage_path,
        device_binding_repo=MagicMock(),
        default_ttl_minutes=10080,
        sandbox_registry=_make_registry(),
        http_client=LocalHttpClient(),
        general_http_client=LocalHttpClient(base_url=""),
        common_whitelist_service=common_whitelist_service or MagicMock(spec=CommonWhiteListService),
        outbound_rule_provider=NoopOutboundRuleProvider(),
        secret_resolver=MagicMock(),
        startup_script_reader=startup_script_reader,
    )


def _bolt_data_mount(entries):
    """从 mount_points 中找到 bolt_data 那个条目。"""
    for entry in entries:
        if entry.local_dir == "/home/admin/nfs/bot-data":
            return entry
    raise AssertionError(f"No bolt_data mount in entries: {entries}")


def _sys_mount(entries):
    """从 mount_points 中找到 sys 那个条目。"""
    for entry in entries:
        if entry.local_dir == "/mnt/sys":
            return entry
    raise AssertionError(f"No sys mount in entries: {entries}")


def _skills_repo_mount(entries):
    """从 mount_points 中找到 skills-repo 那个条目。"""
    for entry in entries:
        if entry.remote_dir == "/skills-repo/b1":
            return entry
    raise AssertionError(f"No skills-repo mount in entries: {entries}")


@pytest.mark.unit
class TestSetupDirectoryEngineAware:
    def test_default_mount_points(self):
        """验证默认返回 bolt_data、skills-repo 和 agentclaw-sys 三个挂载点。"""
        composer = _make_composer()

        entries = composer._setup_directory(
            entity_id="u1",
            entity_type="staff",
            bot_id="b1",
            engine_type="openclaw",
        )

        assert len(entries) == 3
        bolt_data = _bolt_data_mount(entries)
        assert bolt_data.permission == "READ_WRITE"
        skills_repo = _skills_repo_mount(entries)
        assert skills_repo.local_dir == "/home/admin/.openclaw/workspace/skills/skills-repo"
        assert skills_repo.permission == "READ_ONLY"
        sys_mount = _sys_mount(entries)
        assert sys_mount.permission == "READ_ONLY"

    def test_claude_code_engine_type_same_mounts(self):
        """claude_code 引擎类型的挂载点与 openclaw 相同。"""
        composer = _make_composer()

        entries = composer._setup_directory(
            entity_id="u1",
            entity_type="staff",
            bot_id="b1",
            engine_type="claude_code",
        )

        assert len(entries) == 3
        bolt_data = _bolt_data_mount(entries)
        assert bolt_data.permission == "READ_WRITE"
        skills_repo = _skills_repo_mount(entries)
        assert skills_repo.local_dir == "/home/admin/.claude_code/workspace/skills/skills-repo"
        assert skills_repo.permission == "READ_ONLY"
        sys_mount = _sys_mount(entries)
        assert sys_mount.permission == "READ_ONLY"

    def test_empty_engine_type_same_mounts(self):
        """空引擎类型的挂载点也与默认相同。"""
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = None
        composer = _make_composer(bot_repo=bot_repo)

        entries = composer._setup_directory(
            entity_id="u1",
            entity_type="staff",
            bot_id="b1",
            engine_type="",
        )

        assert len(entries) == 3
        bolt_data = _bolt_data_mount(entries)
        assert bolt_data.permission == "READ_WRITE"
        skills_repo = _skills_repo_mount(entries)
        assert skills_repo.local_dir == "/home/admin/.openclaw/workspace/skills/skills-repo"
        assert skills_repo.permission == "READ_ONLY"

    def test_custom_mount_path_is_appended(self):
        composer = _make_composer()

        entries = composer._setup_directory(
            entity_id="u1",
            entity_type="staff",
            bot_id="b1",
            engine_type="claude_code",
            mount_path="/data/extra",
        )

        custom = next(
            (e for e in entries if e.remote_dir == "/data/extra"),
            None,
        )
        assert custom is not None
        assert custom.permission == "READ_WRITE"

    def test_hermes_engine_uses_its_own_build_provider(self):
        composer = _make_composer()

        entries = composer._setup_directory(
            entity_id="u1",
            entity_type="staff",
            bot_id="b1",
            engine_type="hermes",
        )

        skills_repo = _skills_repo_mount(entries)
        assert skills_repo.local_dir == "/home/admin/.hermes/skills/skills-repo"

    def test_center_artifact_adds_frozen_read_only_mount(self):
        composer = _make_composer()
        manifest = {
            "schema_version": 1,
            "engine": "openclaw",
            "active_layout": "pool",
            "layout_contract_version": "skills-pool-p3-v1",
            "center_skills": [
                {
                    "runtime_name": "pdf",
                    "skill_uuid": "00000000-0000-4000-8000-000000000001",
                    "sc_version_number": "1.0.0",
                    "mcp_dependencies": [],
                }
            ],
            "shared_corpora": [
                {
                    "corpus": "center",
                    "runtime_path": "/home/admin/.openclaw/workspace/skills-pool/skill-center",
                    "store_prefix": "aidesktop/aidesktop_dev/bolt_shared/skills-center",
                    "layout_contract_version": "skills-pool-p3-v1",
                    "permission": "read_only",
                    "snapshot_policy": "exclude",
                }
            ],
        }

        entries = composer._setup_directory(
            entity_id="u1",
            entity_type="staff",
            bot_id="b1",
            engine_type="openclaw",
            mount_home_dir_storage=True,
            ext_info={"skills_manifest": manifest},
        )

        center = next(entry for entry in entries if "skills-center" in entry.remote_dir)
        assert center.remote_dir == (
            "/aidesktop/aidesktop_dev/bolt_shared/skills-center"
        )
        assert center.local_dir == (
            "/home/admin/.openclaw/workspace/skills-pool/skill-center"
        )
        assert center.permission == "READ_ONLY"

    def test_old_artifact_does_not_add_center_mount(self):
        composer = _make_composer()

        entries = composer._setup_directory(
            entity_id="u1",
            entity_type="staff",
            bot_id="b1",
            engine_type="openclaw",
            ext_info={"skills_manifest": {
                "schema_version": 1,
                "engine": "openclaw",
                "active_layout": "legacy",
                "layout_contract_version": None,
            }},
        )

        assert all("skills-center" not in entry.remote_dir for entry in entries)


@pytest.mark.unit
class TestSetupSessionsDirEngineAware:
    def test_openclaw_sessions_path_unchanged(self):
        composer = _make_composer()

        storage = composer._setup_sessions_dir(
            entity_id="u1",
            entity_type="staff",
            bot_id="b1",
            engine_type="openclaw",
        )

        assert storage.path == "/home/admin/.openclaw/agents"
        assert storage.type == "nas"

    def test_claude_code_sessions_path_uses_session_root_projects(self):
        # claude_code 的 sessions 目录由独立的 claude_code_session_root
        # 决定，并以 /projects 结尾——与 sandbox 内 ~/.claude/projects 对齐，
        # 而不再复用 claude_code_root 下的 /agents 约定。
        composer = _make_composer()

        storage = composer._setup_sessions_dir(
            entity_id="u1",
            entity_type="staff",
            bot_id="b1",
            engine_type="claude_code",
        )

        assert storage.path == "/home/admin/.claude/projects"

    def test_empty_engine_type_falls_back_to_openclaw(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = None
        composer = _make_composer(bot_repo=bot_repo)

        storage = composer._setup_sessions_dir(
            entity_id="u1",
            entity_type="staff",
            bot_id="b1",
            engine_type="",
        )

        assert storage.path == "/home/admin/.openclaw/agents"

    def test_composer_delegates_sessions_dir_to_provider(self):
        """验证 composer 直接透传 provider.get_sessions_dir 的返回值,
        不再持有任何引擎相关子路径约定（如 /agents 拼接）。
        """
        from agentclaw.community.core.workspace.engine_sandbox import EngineSandboxRegistry

        custom_provider = MagicMock()
        custom_provider.engine_type = "custom_engine"
        custom_provider.get_sessions_dir.return_value = "/some/custom/path/conversations"

        registry = EngineSandboxRegistry()
        registry.register(custom_provider)

        composer = ManagedDeployConfigComposer(
            storage_path=_make_storage_path(),
            sandbox_registry=registry,
            bot_repo=MagicMock(),
        )

        storage = composer._setup_sessions_dir(
            entity_id="u1",
            entity_type="staff",
            bot_id="b1",
            engine_type="custom_engine",
        )

        custom_provider.get_sessions_dir.assert_called_once_with()
        assert storage.path == "/some/custom/path/conversations"


@pytest.mark.unit
class TestBuildCreateBotPayloadStorageWhitelist:
    def _build_payload(self, service: BaasService):
        return service._build_create_bot_payload(
            bot={
                "bot_id": "b1",
                "bot_name": "bot-one",
                "entity_id": "u1",
                "entity_type": "staff",
                "active_engine": "openclaw",
            },
            owner_id="owner1",
            request_id="req1",
            device_count=1,
            migration_path="/tmp/migration",
        )

    def test_default_uses_sessions_dir_when_not_in_whitelist(self):
        whitelist = MagicMock(spec=CommonWhiteListService)
        whitelist.is_bot_feature_enabled.return_value = False
        service = _make_service(common_whitelist_service=whitelist)
        service._deploy_composer._setup_sessions_dir = MagicMock(
            return_value=Storage(
                type="nas",
                storage_id="sessions-storage",
                quota="1Gi",
                permission="0777",
                path="/home/admin/.openclaw/agents",
            )
        )
        service._deploy_composer._setup_home_dir_storage = MagicMock(
            return_value=Storage(
                type="nas",
                storage_id="home-storage",
                quota="1Gi",
                permission="0777",
                path="/home/admin",
            )
        )

        payload = self._build_payload(service)

        whitelist.is_bot_feature_enabled.assert_called_once()
        assert whitelist.is_bot_feature_enabled.call_args.kwargs["business_code"] == "nas_mount"
        assert whitelist.is_bot_feature_enabled.call_args.kwargs["param_code"] == "engine_dir_mount_whitelist"
        assert whitelist.is_bot_feature_enabled.call_args.kwargs["owner_id"] == "owner1"
        assert whitelist.is_bot_feature_enabled.call_args.kwargs["bot_id"] == "b1"
        service._deploy_composer._setup_sessions_dir.assert_called_once_with(
            entity_id="u1",
            entity_type="staff",
            bot_id="b1",
            engine_type="openclaw",
        )
        service._deploy_composer._setup_home_dir_storage.assert_not_called()
        storage = payload["config"]["deploy_config"]["storage"]
        assert storage["path"] == "/home/admin/.openclaw/agents"
        assert storage["storage_id"] == "sessions-storage"

    def test_whitelist_uses_home_dir_storage(self):
        whitelist = MagicMock(spec=CommonWhiteListService)
        whitelist.is_bot_feature_enabled.return_value = True
        service = _make_service(common_whitelist_service=whitelist)
        service._deploy_composer._setup_sessions_dir = MagicMock(
            return_value=Storage(
                type="nas",
                storage_id="sessions-storage",
                quota="1Gi",
                permission="0777",
                path="/home/admin/.openclaw/agents",
            )
        )
        service._deploy_composer._setup_home_dir_storage = MagicMock(
            return_value=Storage(
                type="nas",
                storage_id="home-storage",
                quota="1Gi",
                permission="0777",
                path="/home/admin",
            )
        )

        payload = self._build_payload(service)

        service._deploy_composer._setup_home_dir_storage.assert_called_once_with(
            entity_id="u1",
            entity_type="staff",
            bot_id="b1",
            engine_type="openclaw",
            device_scoped_home_storage=False,
        )
        service._deploy_composer._setup_sessions_dir.assert_not_called()
        storage = payload["config"]["deploy_config"]["storage"]
        assert storage["path"] == "/home/admin"
        assert storage["storage_id"] == "home-storage"

    def test_whitelist_error_falls_back_to_sessions_dir(self):
        whitelist = MagicMock(spec=CommonWhiteListService)
        whitelist.is_bot_feature_enabled.side_effect = RuntimeError("config unavailable")
        service = _make_service(common_whitelist_service=whitelist)
        service._deploy_composer._setup_sessions_dir = MagicMock(
            return_value=Storage(
                type="nas",
                storage_id="sessions-storage",
                quota="1Gi",
                permission="0777",
                path="/home/admin/.openclaw/agents",
            )
        )
        service._deploy_composer._setup_home_dir_storage = MagicMock()

        payload = self._build_payload(service)

        service._deploy_composer._setup_sessions_dir.assert_called_once()
        service._deploy_composer._setup_home_dir_storage.assert_not_called()
        assert payload["config"]["deploy_config"]["storage"]["path"] == "/home/admin/.openclaw/agents"

    def test_empty_migration_path_uses_explicit_home_storage_without_whitelist_check(self):
        service = _make_service(common_whitelist_service=MagicMock(spec=CommonWhiteListService))
        service._should_mount_home_dir_storage = MagicMock(return_value=True)
        service._deploy_composer._setup_sessions_dir = MagicMock()
        service._deploy_composer._setup_home_dir_storage = MagicMock(
            return_value=Storage(
                type="nas",
                storage_id="home-storage",
                quota="1Gi",
                permission="0777",
                path="/home/admin",
            )
        )

        payload = service._build_create_bot_payload(
            bot={
                "bot_id": "b1",
                "bot_name": "bot-one",
                "entity_id": "u1",
                "entity_type": "staff",
                "active_engine": "openclaw",
            },
            owner_id="owner1",
            request_id="req1",
            device_count=1,
            migration_path="",
            mount_home_dir_storage=True,
        )

        service._should_mount_home_dir_storage.assert_not_called()
        service._deploy_composer._setup_sessions_dir.assert_not_called()
        service._deploy_composer._setup_home_dir_storage.assert_called_once_with(
            entity_id="u1",
            entity_type="staff",
            bot_id="b1",
            engine_type="openclaw",
            device_scoped_home_storage=False,
        )
        assert payload["config"]["deploy_config"]["storage"]["storage_id"] == "home-storage"
        assert "--source_dir" not in payload["config"]["deploy_config"]["after_create_cmd_hook"]
        assert "--stage " not in payload["config"]["deploy_config"]["after_create_cmd_hook"]
        assert " --useNas true" in payload["config"]["deploy_config"]["after_create_cmd_hook"]


def test_should_mount_home_dir_storage_returns_false_without_whitelist_service():
    service = _make_service(common_whitelist_service=MagicMock(spec=CommonWhiteListService))
    service._common_whitelist_service = None

    assert service._should_mount_home_dir_storage(owner_id="owner1", bot_id="b1") is False


def test_unspecified_mount_flag_is_resolved_once_and_reaches_both_halves():
    """The ``nas_mount`` read moved up to ``_build_create_bot_payload``.

    It used to happen inside the mount builder and then again inside the
    storage builder, each on its own ``None``. Now the composer is handed the
    answer: one payload makes one whitelist read, and the mounts cannot end up
    disagreeing with the storage about where this bot's data lives.
    """
    whitelist = MagicMock(spec=CommonWhiteListService)
    whitelist.is_bot_feature_enabled.return_value = True
    service = _make_service(common_whitelist_service=whitelist)

    payload = service._build_create_bot_payload(
        bot={
            "bot_id": "b1",
            "bot_name": "bot-one",
            "entity_id": "u1",
            "entity_type": "staff",
            "active_engine": "openclaw",
        },
        owner_id="owner1",
        request_id="req1",
        device_count=1,
        migration_path="",
    )

    whitelist.is_bot_feature_enabled.assert_called_once()
    deploy_config = payload["config"]["deploy_config"]
    assert deploy_config["storage"]["path"] == "/home/admin"
    assert [mp["local_dir"] for mp in deploy_config["mount_points"]] == [
        "/mnt/sys",
        "/opt/nfs/bot-data",
    ]


def test_setup_home_dir_storage_uses_home_admin_path_and_engine_aware_storage_id():
    composer = _make_composer()

    storage = composer._setup_home_dir_storage(
        entity_id="u1",
        entity_type="staff",
        bot_id="b1",
        engine_type="openclaw",
    )

    assert storage.type == "nas"
    assert storage.path == "/home/admin"
    assert storage.quota == "1Gi"
    assert storage.permission == "0777"
    assert storage.storage_id == "dev_staff_u1_openclaw_b1"


@pytest.mark.parametrize("stage", ["verify", "online"])
def test_setup_bot_storage_uses_device_scoped_home_storage_for_service_release_stages(stage):
    composer = _make_composer()

    storage = composer._setup_bot_storage(
        entity_id="u1",
        entity_type="staff",
        owner_id="owner1",
        bot_id="b1",
        engine_type="openclaw",
        mount_home_dir_storage=True,
        bot_type="service",
        stage=stage,
    )

    assert storage.path == "/home/admin"
    assert storage.storage_id == "dev_staff_u1_openclaw_b1_{device_uuid}"


@pytest.mark.unit
def test_setup_bot_storage_keeps_static_storage_id_for_service_draft():
    composer = _make_composer()

    storage = composer._setup_bot_storage(
        entity_id="u1",
        entity_type="staff",
        owner_id="owner1",
        bot_id="b1",
        engine_type="openclaw",
        mount_home_dir_storage=True,
        bot_type="service",
        stage="draft",
    )

    assert storage.path == "/home/admin"
    assert storage.storage_id == "dev_staff_u1_openclaw_b1"


@pytest.mark.unit
def test_build_create_bot_payload_rewrites_migration_path_to_opt_when_mount_home_dir_storage():
    whitelist = MagicMock(spec=CommonWhiteListService)
    whitelist.is_bot_feature_enabled.return_value = True
    service = _make_service(common_whitelist_service=whitelist)
    service._deploy_composer._setup_home_dir_storage = MagicMock(
        return_value=Storage(
            type="nas",
            storage_id="home-storage",
            quota="1Gi",
            permission="0777",
            path="/home/admin",
        )
    )
    payload = service._build_create_bot_payload(
        bot={
            "bot_id": "b1",
            "bot_name": "bot-one",
            "entity_id": "u1",
            "entity_type": "staff",
            "active_engine": "openclaw",
        },
        owner_id="owner1",
        request_id="req1",
        device_count=1,
        migration_path="/home/admin/nfs/bot-data/1/openclaw",
    )
    cmd = payload["config"]["deploy_config"]["after_create_cmd_hook"]
    assert "--source_dir /opt/nfs/bot-data/1/openclaw" in cmd
@pytest.mark.unit
def test_build_create_bot_payload_rewrites_opt_migration_path_back_to_home_admin_when_not_mounting_home_dir():
    whitelist = MagicMock(spec=CommonWhiteListService)
    whitelist.is_bot_feature_enabled.return_value = False
    service = _make_service(common_whitelist_service=whitelist)
    service._setup_sessions_dir = MagicMock(
        return_value=Storage(
            type="nas",
            storage_id="sessions-storage",
            quota="1Gi",
            permission="0777",
            path="/home/admin/.openclaw/agents",
        )
    )
    payload = service._build_create_bot_payload(
        bot={
            "bot_id": "b1",
            "bot_name": "bot-one",
            "entity_id": "u1",
            "entity_type": "staff",
            "active_engine": "openclaw",
        },
        owner_id="owner1",
        request_id="req1",
        device_count=1,
        migration_path="/opt/nfs/bot-data/1/openclaw",
    )
    cmd = payload["config"]["deploy_config"]["after_create_cmd_hook"]
    assert "--source_dir /home/admin/nfs/bot-data/1/openclaw" in cmd
