"""Integration smoke test for the Aliyun ACK Arca sandbox backend.

Wires the Arca provider via ``plugins.sandbox.arca: aliyun_ack``, reads the
ARCA device template from the SQLite database (seeded ``TEMPLATE_ARCA``),
resolves its ``arca_template_id`` against ``user_config.aliyun_ack_template``,
and creates an ACK Pod via ``AliyunAckSandboxPlugin`` -- all through the real
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
TEMPLATE_ID = "ALIYUN_ACK_TEMPLATE_default"
TEST_TENANT = "team_claw"

_SANDBOX = "secbaas.community.plugins.sandbox.arca.aliyun_ack._sandbox"
_PLUGIN = "secbaas.community.plugins.sandbox.arca.aliyun_ack._sandbox_plugin"


class _FakePod:
    def __init__(self, phase: str = "Running") -> None:
        self.status = MagicMock()
        self.status.phase = phase
        self.metadata = MagicMock()
        self.metadata.labels = {"avernet.arcasandbox/template": TEMPLATE_ID}


def _select_aliyun_ack(bootstrap_init: ApplicationContainer) -> PaasServiceFactory:
    """Point the arca selector at aliyun_ack and supply the ACK template map."""
    from secbaas.community.config import ConfigLoader

    loader_cfg = ConfigLoader.load()
    ack_template = loader_cfg.user_config.get("aliyun_ack_template", {})
    bootstrap_init.config.from_dict(
        {
            "plugins": {"sandbox": {"arca": "aliyun_ack"}},
            "aliyun_ack_template": ack_template,
        }
    )
    return bootstrap_init.services.paas_service_factory()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_aliyun_ack_plugin_created_from_db_template(
    bootstrap_init: ApplicationContainer,
) -> None:
    """A device create on the ARCA device template dispatches to AliyunAckSandboxPlugin."""
    # Ensure lazy k8s bindings exist so we can patch CoreV1Api.
    import importlib

    for mod_name in (_SANDBOX, _PLUGIN):
        importlib.import_module(mod_name)._import_k8s()

    factory = _select_aliyun_ack(bootstrap_init)

    # Resolve the ARCA device template from the DB and build the PaaS service.
    service = factory.create(tenant_name=TEST_TENANT, template_uuid=TEMPLATE_ARCA)
    assert isinstance(service, ArcaPaasService)
    plugin = service._arca_sandbox_plugin
    assert isinstance(plugin, AliyunAckSandboxPlugin)

    # Stub k8s so create_sync_sandbox does not hit a real cluster.
    core = MagicMock()
    core.read_namespaced_pod.return_value = _FakePod()
    with ExitStack() as stack:
        stack.enter_context(patch(f"{_PLUGIN}.CoreV1Api", MagicMock(return_value=core)))
        stack.enter_context(
            patch(f"{_SANDBOX}.CoreV1Api", MagicMock(return_value=core))
        )
        stack.enter_context(
            patch.object(AliyunAckClientManager, "get_client", return_value=MagicMock())
        )
        sandbox = plugin.create_sync_sandbox(
            template_id=TEMPLATE_ID, image="test:latest"
        )

    assert core.create_namespaced_pod.called
    assert core.create_namespaced_service.called
    from secbaas.community.spi.sandbox.arca import ArcaSandboxInfo

    info = sandbox.get_info()
    assert isinstance(info, ArcaSandboxInfo)
    assert info.sandbox_id == sandbox.sandbox_id
