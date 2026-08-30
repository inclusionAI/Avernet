"""The deploy-composer seam: the contract, and what each side of it owes.

The golden payload test next door proves the managed composer still builds what
it always built. This file covers the seam itself — that the ABC admits no
half-implementation, that ``BaasService`` asks the composer instead of deciding
for itself, and that the unimplemented ACK composer fails loudly rather than
quietly composing managed values on a runtime they do not fit.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.service_bot.services.deploy.ack_composer import (
    AckDeployConfigComposer,
)
from agentclaw.community.core.service_bot.services.deploy.deploy_config_composer import (
    BotDeployContext,
    DeployConfigComposer,
)
from agentclaw.community.core.service_bot.services.deploy.deploy_models import (
    MountPermission,
    MountPointEntry,
    Storage,
    StorageType,
)
from agentclaw.community.core.service_bot.services.deploy.managed_composer import (
    ManagedDeployConfigComposer,
)
from agentclaw.community.kernel.deploy_runtime import DeployRuntime
from agentclaw.community.plugins.local.http_client import LocalHttpClient
from agentclaw.community.plugins.local.outbound_rules import NoopOutboundRuleProvider

_CTX = BotDeployContext(
    bot_id="b1",
    owner_id="owner1",
    entity_id="u1",
    entity_type="staff",
    bot_type="service",
    engine="openclaw",
    migration_path="/tmp/migration",
    mount_home_dir_storage=False,
)

_COMPOSER_METHODS = ("build_start_command", "build_mount_points", "build_storage")


class _StubComposer(DeployConfigComposer):
    """A composer that answers "nothing" to all three questions."""

    def __init__(self, storage: Storage | None = None) -> None:
        self._storage = storage
        self.contexts: list[BotDeployContext] = []

    @property
    def name(self) -> DeployRuntime:
        return DeployRuntime.MANAGED

    def build_start_command(self, ctx: BotDeployContext) -> str:
        self.contexts.append(ctx)
        return "echo started"

    def build_mount_points(self, ctx: BotDeployContext) -> list[MountPointEntry]:
        self.contexts.append(ctx)
        return [
            MountPointEntry(
                remote_dir="/stub",
                local_dir="/mnt/stub",
                permission=MountPermission.READ_ONLY,
            )
        ]

    def build_storage(self, ctx: BotDeployContext) -> Storage | None:
        self.contexts.append(ctx)
        return self._storage


def _make_service(composer: DeployConfigComposer) -> BaasService:
    whitelist = MagicMock()
    whitelist.is_bot_feature_enabled.return_value = False
    return BaasService(
        deploy_composer=composer,
        baas_api_base="http://test",
        tenant="test",
        template_uuid="TEMPLATE-x",
        bot_repo=MagicMock(**{"get_by_id_and_owner.return_value": None}),
        bot_publish_repo=MagicMock(),
        system_config_service=MagicMock(**{"get_config.return_value": None}),
        storage_path=MagicMock(),
        device_binding_repo=MagicMock(),
        default_ttl_minutes=10080,
        sandbox_registry=MagicMock(),
        http_client=LocalHttpClient(),
        general_http_client=LocalHttpClient(base_url=""),
        secret_resolver=MagicMock(),
        common_whitelist_service=whitelist,
        outbound_rule_provider=NoopOutboundRuleProvider(),
        startup_script_reader=MagicMock(**{"get_body.return_value": ""}),
    )


def _build_payload(service: BaasService, **overrides) -> dict:
    kwargs = dict(
        bot={
            "bot_id": "b1",
            "bot_name": "bot-one",
            "entity_id": "u1",
            "entity_type": "staff",
            "active_engine": "openclaw",
            "bot_type": "service",
        },
        owner_id="owner1",
        request_id="req1",
        device_count=1,
        migration_path="/tmp/migration",
    )
    kwargs.update(overrides)
    return service._build_create_bot_payload(**kwargs)["config"]["deploy_config"]


@pytest.mark.unit
class TestTheContract:
    @pytest.mark.parametrize(
        "composer_cls", [ManagedDeployConfigComposer, AckDeployConfigComposer]
    )
    def test_both_implementations_are_complete(self, composer_cls):
        """An ABC with an unimplemented method is a class that cannot be built —
        which is the point of using one rather than a Protocol here."""
        assert not inspect.isabstract(composer_cls)
        for method in (*_COMPOSER_METHODS, "name"):
            assert hasattr(composer_cls, method), method

    def test_a_partial_implementation_cannot_be_instantiated(self):
        class Partial(DeployConfigComposer):
            @property
            def name(self) -> str:
                return "partial"

            def build_start_command(self, ctx):
                return ""

        with pytest.raises(TypeError, match="build_mount_points|build_storage"):
            Partial()

    def test_names_are_the_runtime_they_build_for(self):
        """The name is what the boot log prints and what ``deploy_runtime``
        selects; typing it as the enum is what stops the two from drifting."""
        assert (
            ManagedDeployConfigComposer(
                storage_path=MagicMock(),
                sandbox_registry=MagicMock(),
                bot_repo=MagicMock(),
            ).name
            is DeployRuntime.MANAGED
        )
        assert AckDeployConfigComposer().name is DeployRuntime.ACK


@pytest.mark.unit
class TestBaasServiceDelegates:
    def test_the_payload_carries_what_the_composer_returned(self):
        composer = _StubComposer(
            storage=Storage(
                type=StorageType.NAS,
                path="/stub/home",
                storage_id="stub-storage",
                quota="1Gi",
                permission="0777",
            )
        )

        deploy_config = _build_payload(_make_service(composer))

        assert deploy_config["after_create_cmd_hook"] == "echo started"
        assert deploy_config["mount_points"] == [
            {
                "remote_dir": "/stub",
                "local_dir": "/mnt/stub",
                "permission": "READ_ONLY",
            }
        ]
        assert deploy_config["storage"]["storage_id"] == "stub-storage"

    def test_no_storage_drops_the_key_rather_than_sending_an_empty_block(self):
        deploy_config = _build_payload(_make_service(_StubComposer(storage=None)))

        assert "storage" not in deploy_config

    def test_every_call_sees_the_same_bot(self):
        """All three answers describe one container; composing them from
        different contexts is how mounts and storage drift apart."""
        composer = _StubComposer()

        _build_payload(_make_service(composer))

        assert len(composer.contexts) == 3
        first = composer.contexts[0]
        for ctx in composer.contexts[1:]:
            assert ctx.bot_id == first.bot_id
            assert ctx.owner_id == first.owner_id
            assert ctx.engine == first.engine
            assert ctx.mount_home_dir_storage == first.mount_home_dir_storage

    def test_the_startup_script_stage_wraps_whatever_the_composer_returns(self):
        """Issue #926 is the service's, not each composer's: a deployment that
        writes its own start command still runs its bots' stored scripts."""
        composer = _StubComposer()
        service = _make_service(composer)
        service._startup_script_reader = MagicMock(
            **{"get_body.return_value": "echo hi"}
        )

        hook = _build_payload(service)["after_create_cmd_hook"]

        assert hook.startswith("echo started\n__OCB_RC=$?\n")
        assert "echo ZWNobyBoaQ== | base64 -d" in hook
        assert hook.endswith("exit $__OCB_RC\n")


@pytest.mark.unit
class TestAckComposer:
    def test_build_start_command_uses_engine_bot_id_and_owner_from_context(self):
        """The start command is a single ``nohup`` that carries the engine,
        bot_id and owner_id from the context — not the managed image's
        four-step chain."""
        cmd = AckDeployConfigComposer().build_start_command(_CTX)

        assert "start_service.sh" in cmd
        assert "--engine openclaw" in cmd
        assert "--bot_id b1" in cmd
        assert "--owner_id owner1" in cmd

    def test_build_start_command_keeps_token_and_client_id_as_placeholders(self):
        """``{token}`` and ``{client_id}`` stay as literal placeholders for
        BaaS to substitute at dispatch — the backend cannot know them at
        compose time."""
        cmd = AckDeployConfigComposer().build_start_command(_CTX)

        assert "{token}" in cmd
        assert "{client_id}" in cmd

    def test_build_mount_points_returns_no_bind_mounts(self):
        """The ACK pod's volumes come from the ``storage`` block, not from
        pre-existing shared directories."""
        assert AckDeployConfigComposer().build_mount_points(_CTX) == []

    def test_build_storage_returns_nas_volume(self):
        """The bot's persistent state lives on a NAS volume at
        ``/home/admin``. ``storage_id`` carries the ``bot_id`` so BaaS can
        re-attach the same volume on the next start."""
        storage = AckDeployConfigComposer().build_storage(_CTX)

        assert storage is not None
        assert str(storage.type) == "nas"
        assert storage.path == "/home/admin"
        assert storage.storage_id == "b1"
        assert storage.quota == "1Gi"
        assert storage.permission == "0777"
