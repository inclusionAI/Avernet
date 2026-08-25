"""Golden ``deploy_config`` — the create-bot payload, pinned byte for byte.

Every bot this platform has ever started was started by the string in
``after_create_cmd_hook`` and the mounts beside it. Composing that payload is
being moved off ``BaasService`` and behind ``DeployConfigComposer`` so a second
deployment (ACK/ECI, open-source image) can compose its own. A *move* that
changes the payload is not a move — it is a silent production change to the
boot of every bot, on a path no unit test asserts end to end.

So the expected values below were captured from the pre-extraction code and are
literal on purpose: they are a fixed point, not a derivation. If a diff makes
this file need editing, that diff changed what a bot runs.

The matrix covers every branch the moved code has: the ``nas_mount`` whitelist
(sessions dir vs home dir), a service bot vs an unpublished personal bot (which
is what drives the read-only rules and the device-scoped storage id), and a bot
with a per-bot startup script (issue #926) so the user stage keeps wrapping the
platform chain.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.service_bot.services.deploy.managed_composer import (
    ManagedDeployConfigComposer,
)
from agentclaw.community.core.workspace.engine_sandbox import EngineSandboxRegistry
from agentclaw.community.core.workspace.engines.openclaw import OpenClawSandboxProvider
from agentclaw.community.di import config as cfg
from agentclaw.community.plugins.local.http_client import LocalHttpClient
from agentclaw.community.plugins.local.outbound_rules import NoopOutboundRuleProvider


def _make_service(
    *, home_dir_whitelist: bool, startup_script: str = ""
) -> BaasService:
    registry = EngineSandboxRegistry()
    registry.register(OpenClawSandboxProvider(workspace=cfg.WorkspaceConfig()))

    storage_path = MagicMock()
    storage_path.get_bolt_data_path.return_value = "bolt-data/staff/447172/b1"
    storage_path.get_skills_repo_path.return_value = "skills-repo/b1"

    whitelist = MagicMock()
    whitelist.is_bot_feature_enabled.return_value = home_dir_whitelist

    bot_repo = MagicMock(**{"get_by_id_and_owner.return_value": None})

    return BaasService(
        # The composer reads the same NAS layout and engine registry the
        # service does — sharing them is what makes this a payload test rather
        # than a wiring test.
        deploy_composer=ManagedDeployConfigComposer(
            storage_path=storage_path,
            sandbox_registry=registry,
            bot_repo=bot_repo,
        ),
        baas_api_base="http://test",
        tenant="test",
        template_uuid="TEMPLATE-x",
        bot_repo=bot_repo,
        bot_publish_repo=MagicMock(),
        system_config_service=MagicMock(**{"get_config.return_value": None}),
        storage_path=storage_path,
        device_binding_repo=MagicMock(),
        default_ttl_minutes=10080,
        sandbox_registry=registry,
        http_client=LocalHttpClient(),
        general_http_client=LocalHttpClient(base_url=""),
        secret_resolver=MagicMock(),
        common_whitelist_service=whitelist,
        outbound_rule_provider=NoopOutboundRuleProvider(),
        startup_script_reader=MagicMock(
            **{"get_body.return_value": startup_script}
        ),
    )


def _deploy_config(
    *,
    home_dir_whitelist: bool,
    bot_type: str = "service",
    stage: str | None = "online",
    migration_path: str = "/home/admin/nfs/bot-data/7/openclaw",
    startup_script: str = "",
) -> dict:
    service = _make_service(
        home_dir_whitelist=home_dir_whitelist, startup_script=startup_script
    )
    payload = service._build_create_bot_payload(
        bot={
            "bot_id": "b1",
            "bot_name": "bot-one",
            "entity_id": "447172",
            "entity_type": "staff",
            "active_engine": "openclaw",
            "bot_type": bot_type,
        },
        owner_id="447172",
        request_id="req1",
        device_count=1,
        migration_path=migration_path,
        stage=stage,
        version="1",
    )
    return payload["config"]["deploy_config"]


_SESSIONS_DIR_HOOK = (
    "su admin -c 'bash /home/admin/bin/bootstrap_minimal.sh' && "
    "(if [ -f /home/admin/bin/install_engine.sh ]; then "
    "bash /home/admin/bin/install_engine.sh "
    ">> /home/admin/logs/install_engine.log 2>&1; "
    "else echo '[install_engine] /home/admin/bin/install_engine.sh not found, "
    "skip'; fi) &&  "
    "su admin -c 'nohup /home/admin/bin/start_service.sh "
    "--token {token} --client_id {client_id} --bot_type service "
    "--engine openclaw --source_dir /home/admin/nfs/bot-data/7/openclaw "
    "--bot_id b1 --owner_id 447172 --entity_id 447172 --entity_type staff "
    "--stage online --version V1 --useNas false "
    "--set_read_only /home/admin/.openclaw/openclaw.json,"
    "/home/admin/.openclaw/workspace/config/mcporter.json,"
    "/home/admin/.openclaw/workspace/*.md,/home/admin/.mcporter/mcporter.json,"
    "/home/admin/.openclaw/agents/*/agent/models.json,"
    "/home/admin/.openclaw/workspace/skills/skills-local "
    ">> /home/admin/start.log 2>&1' && "
    "su admin -c 'nohup /home/admin/bin/starting_watchdog.sh "
    "--token {token} --client_id {client_id} "
    ">> /home/admin/logs/starting_watchdog.log 2>&1'"
)


@pytest.mark.unit
class TestGoldenDeployConfig:
    def test_sessions_dir_bot(self):
        assert _deploy_config(home_dir_whitelist=False) == {
            "after_create_cmd_hook": _SESSIONS_DIR_HOOK,
            "after_create_hook_wait_seconds": 10,
            "before_destroy_cmd_hook": None,
            "before_destroy_hook_wait_seconds": 10,
            "mount_points": [
                {
                    "remote_dir": "/agentclaw-sys",
                    "local_dir": "/mnt/sys",
                    "permission": "READ_ONLY",
                },
                {
                    "remote_dir": "/bolt-data/staff/447172/b1",
                    "local_dir": "/home/admin/nfs/bot-data",
                    "permission": "READ_WRITE",
                },
                {
                    "remote_dir": "/skills-repo/b1",
                    "local_dir": "/home/admin/.openclaw/workspace/skills/skills-repo",
                    "permission": "READ_ONLY",
                },
            ],
            "ttl_in_minutes": 10080,
            "outbound_operation_rule": {"header_operation_rules": []},
            "storage": {
                "type": "nas",
                "path": "/home/admin/.openclaw/agents",
                "storage_id": "dev_staff_447172_openclaw_b1_{device_uuid}",
                "quota": "1Gi",
                "permission": "0777",
            },
            "user_id": "447172",
            "tc_bot_id": "b1",
            "envs": {"AGENTCLAW_ENGINE": "openclaw"},
        }

    def test_home_dir_bot_switches_mounts_and_storage(self):
        deploy_config = _deploy_config(home_dir_whitelist=True)

        assert deploy_config["mount_points"] == [
            {
                "remote_dir": "/agentclaw-sys",
                "local_dir": "/mnt/sys",
                "permission": "READ_ONLY",
            },
            {
                "remote_dir": "/bolt-data/staff/447172/b1",
                "local_dir": "/opt/nfs/bot-data",
                "permission": "READ_WRITE",
            },
        ]
        assert deploy_config["storage"] == {
            "type": "nas",
            "path": "/home/admin",
            "storage_id": "dev_staff_447172_openclaw_b1_{device_uuid}",
            "quota": "1Gi",
            "permission": "0777",
        }
        assert " --useNas true" in deploy_config["after_create_cmd_hook"]
        assert (
            " --source_dir /opt/nfs/bot-data/7/openclaw"
            in deploy_config["after_create_cmd_hook"]
        )

    def test_personal_bot_without_migration_path(self):
        deploy_config = _deploy_config(
            home_dir_whitelist=False,
            bot_type="personal",
            stage=None,
            migration_path="",
        )
        hook = deploy_config["after_create_cmd_hook"]

        # No migration ⇒ no --source_dir; no stage ⇒ no --stage; an editable
        # bot is not locked ⇒ no --set_read_only.
        assert " --source_dir " not in hook
        assert " --stage " not in hook
        assert " --set_read_only " not in hook
        assert " --bot_type personal" in hook
        assert deploy_config["storage"]["path"] == "/home/admin/.openclaw/agents"

    def test_startup_script_wraps_the_platform_chain(self):
        hook = _deploy_config(
            home_dir_whitelist=False, startup_script="echo hi"
        )["after_create_cmd_hook"]

        platform, marker, user_stage = hook.partition("\n__OCB_RC=$?\n")

        assert platform == _SESSIONS_DIR_HOOK
        assert marker
        assert 'if [ "$__OCB_RC" -eq 0 ]; then' in user_stage
        assert "echo ZWNobyBoaQ== | base64 -d" in user_stage
        assert user_stage.endswith("exit $__OCB_RC\n")


@pytest.mark.unit
def test_golden_config_is_json_serializable():
    """The payload is posted as JSON — a non-serializable value fails at the
    wire, far from whatever put it there."""
    json.dumps(_deploy_config(home_dir_whitelist=False))
