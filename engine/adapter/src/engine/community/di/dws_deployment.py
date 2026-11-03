"""Opt-in container configuration. Profile supplies the owner-notification provider."""

import os
import time
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr

from engine.community.di.dws_hosting import HostingRuntimeConfig, build_dws_runtime
from engine.community.di.dws_management import build_management_endpoint
from engine.community.kernel.dws_connection import ConnectionIdentity
from engine.community.kernel.dws_hosting import Binding, Value
from engine.community.plugins.dws_hosting.client import DwsCliConnection
from engine.community.plugins.dws_hosting.context import DwsHostingContext
from engine.community.plugins.dws_hosting.database import HostingDatabase
from engine.community.plugins.dws_hosting.openclaw import OpenClawHostingEngine
from engine.community.plugins.dws_hosting.openclaw_transport import HostingGatewayConfig
from engine.community.plugins.dws_hosting.process import DwsProcess
from engine.community.plugins.dws_hosting.sender import DwsCliSender
from engine.community.plugins.dws_hosting.verification import BackendManagementVerifier
from engine.community.plugin_api.dws_management import ManagementProofVerifier


class HostingDeployment(Value):
    runtime: HostingRuntimeConfig
    binding: Binding
    identity: ConnectionIdentity
    dws_command: tuple[str, ...] = ("dws",)
    dws_config_dir: Path
    authorization_hosts: Annotated[tuple[str, ...], Field(min_length=1)]
    history_timezone: str = "Asia/Shanghai"
    gateway_url: str
    gateway_device_key: Path
    gateway_token: SecretStr
    mcporter_config: Path
    mcporter_command: tuple[str, ...] = ("mcporter",)
    management_backend_url: str = ""
    confirmed_contacts: dict[
        Annotated[str, Field(min_length=1, max_length=200)],
        Annotated[str, Field(min_length=1, max_length=512)],
    ] = Field(default_factory=dict, max_length=100)


def load_deployment() -> HostingDeployment | None:
    path = os.environ.get("DWS_HOSTING_CONFIG", "")
    if not path:
        return None
    source = Path(path)
    if not source.is_absolute():
        raise ValueError("absolute_hosting_config_required")
    # COSEC: deployment credentials must be in a private regular file, not browser input.
    HostingDatabase._check_private(source, directory=False)
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("hosting_config_too_large")
    return HostingDeployment.model_validate_json(raw)


def build_deployed_management(
    config: HostingDeployment,
    owner_sender_factory,
    *,
    verifier: ManagementProofVerifier | None = None,
    connection_factory=None,
):
    if not config.runtime.enabled:
        return None
    if config.runtime.bot_type != "personal" or config.runtime.engine != "openclaw":
        raise ValueError("unsupported_hosting_bot")
    if (
        not config.runtime.storage_qualified
        or not config.runtime.state_dir.is_absolute()
    ):
        raise ValueError("qualified_persistent_storage_required")
    if (config.binding.organization_id, config.binding.account_id) != (
        config.identity.organization_id,
        config.identity.user_id,
    ):
        raise ValueError("identity_mismatch")
    if not config.dws_config_dir.is_absolute():
        raise ValueError("absolute_dws_config_required")
    if verifier is None:
        verifier = BackendManagementVerifier(config.management_backend_url)
    environment = dict(os.environ)
    connection = (connection_factory or DwsCliConnection)(
        DwsProcess(config.dws_command, config.dws_config_dir, environment),
        config.identity,
        authorization_hosts=frozenset(config.authorization_hosts),
        history_timezone=config.history_timezone,
    )
    engine = OpenClawHostingEngine(
        HostingGatewayConfig(
            config.gateway_url,
            config.gateway_device_key,
            config.gateway_token.get_secret_value(),
        )
    )
    owner = owner_sender_factory(config, environment)

    def clock_ms():
        return int(time.time() * 1000)

    runtime = build_dws_runtime(
        config.runtime,
        config.binding,
        connection=connection,
        engine=engine,
        peer_sender=DwsCliSender(connection, config.binding),
        owner_sender=owner,
        clock_ms=clock_ms,
        context=DwsHostingContext(
            connection, clock_ms, confirmed_contacts=config.confirmed_contacts
        ),
        repair=engine,
    )
    return build_management_endpoint(
        runtime, connection, config.runtime.state_dir, verifier
    )
