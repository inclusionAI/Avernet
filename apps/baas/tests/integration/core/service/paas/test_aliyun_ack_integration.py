"""Integration smoke test for the Aliyun ACK Arca sandbox backend.

Wires the Arca provider via ``plugins.sandbox.arca: aliyun_ack``, reads the
ARCA device template from the SQLite database (seeded ``TEMPLATE_ARCA``),
resolves its ``arca_template_id`` to a YAML template file, and creates a
Deployment via ``AliyunAckSandboxPlugin`` -- all through the real
``ApplicationContainer``. ``CoreV1Api`` is stubbed so no live cluster is needed.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.bootstrap import ApplicationContainer
from secbaas.community.core.service.paas import ArcaPaasService, PaasServiceFactory
from secbaas.community.plugins.sandbox.arca.aliyun_ack import AliyunAckSandboxPlugin
from secbaas.community.plugins.sandbox.arca.aliyun_ack._client_manager import (
    AliyunAckClientManager,
)

# Seeded by _seed.py (it-sqlite overlay) under tenant "team_claw"
TEMPLATE_ARCA = "TEMPLATE-4d0e2849d7004111836333de782b95d8"
TEMPLATE_ID = "ALIYUN_ACK_DEFAULT"
TEST_TENANT = "team_claw"

_SANDBOX = "secbaas.community.plugins.sandbox.arca.aliyun_ack._sandbox"
_PLUGIN = "secbaas.community.plugins.sandbox.arca.aliyun_ack._sandbox_plugin"


class _FakeOwnerRef:
    def __init__(
        self, kind: str = "ReplicaSet", name: str = "openclaw-ack-uid-abcd"
    ) -> None:
        self.kind = kind
        self.name = name


class _FakeContainer:
    def __init__(self, name: str = "openclaw") -> None:
        self.name = name


class _FakePodSpec:
    def __init__(self, containers: list | None = None) -> None:
        self.containers = containers if containers is not None else [_FakeContainer()]


class _FakePod:
    def __init__(
        self, phase: str = "Running", name: str = "openclaw-ack-test-pod"
    ) -> None:
        self.status = MagicMock()
        self.status.phase = phase
        self.metadata = MagicMock()
        self.metadata.name = name
        self.metadata.labels = {"avernet.arcasandbox/template": TEMPLATE_ID}
        self.metadata.owner_references = [_FakeOwnerRef()]
        self.spec = _FakePodSpec()


class _FakePodList:
    def __init__(self, pods: list | None = None) -> None:
        self.items = pods if pods is not None else [_FakePod()]


def _select_aliyun_ack(bootstrap_init: ApplicationContainer) -> PaasServiceFactory:
    """Point the arca selector at aliyun_ack and supply the ACK cluster config."""
    from secbaas.community.config import ConfigLoader

    loader_cfg = ConfigLoader.load()
    ack_cluster = loader_cfg.user_config.get("aliyun_ack_cluster", {})
    bootstrap_init.config.from_dict(
        {
            "plugins": {"sandbox": {"arca": "aliyun_ack"}},
            "aliyun_ack_cluster": ack_cluster,
        }
    )
    bootstrap_init.services.paas_sandbox_plugins.reset()
    bootstrap_init.services.paas_service_factory.reset()
    return bootstrap_init.services.paas_service_factory()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_aliyun_ack_plugin_created_from_db_template(
    bootstrap_init: ApplicationContainer,
) -> None:
    """A device create on the ARCA device template dispatches to AliyunAckSandboxPlugin."""
    factory = _select_aliyun_ack(bootstrap_init)

    # Resolve the ARCA device template from the DB and build the PaaS service.
    service = factory.create(tenant_name=TEST_TENANT, template_uuid=TEMPLATE_ARCA)
    assert isinstance(service, ArcaPaasService)
    plugin = service._arca_sandbox_plugin
    assert isinstance(plugin, AliyunAckSandboxPlugin)

    # Stub k8s so create_sync_sandbox does not hit a real cluster.
    core = MagicMock()
    core.read_namespaced_pod.return_value = _FakePod()
    core.list_namespaced_pod.return_value = _FakePodList()
    apps = MagicMock()
    fake_create_from_yaml = MagicMock()
    with ExitStack() as stack:
        stack.enter_context(patch(f"{_PLUGIN}.CoreV1Api", MagicMock(return_value=core)))
        stack.enter_context(
            patch(f"{_SANDBOX}.CoreV1Api", MagicMock(return_value=core))
        )
        stack.enter_context(patch(f"{_PLUGIN}.create_from_yaml", fake_create_from_yaml))
        stack.enter_context(
            patch(f"{_SANDBOX}.AppsV1Api", MagicMock(return_value=apps))
        )
        stack.enter_context(
            patch.object(AliyunAckClientManager, "get_client", return_value=MagicMock())
        )
        sandbox = plugin.create_sync_sandbox(
            template_id=TEMPLATE_ID, image="test:latest"
        )

    assert fake_create_from_yaml.called
    from secbaas.community.spi.sandbox.arca import ArcaSandboxInfo

    info = sandbox.get_info()
    assert isinstance(info, ArcaSandboxInfo)
    assert info.sandbox_id == sandbox.sandbox_id
