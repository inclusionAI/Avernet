"""Explicit management assembly; a profile installs only its opted-in binding."""

from dataclasses import dataclass

from engine.community.core.dws_hosting.connection import HostingAuthorization
from engine.community.core.dws_hosting.management import (
    HostingManagement,
    HostingManagementService,
)
from engine.community.plugin_api.dws_management import (
    HostingManagementStore,
    ManagementProofVerifier,
)
from engine.community.plugins.dws_hosting.management import SqliteHostingManagementStore
from engine.community.plugins.dws_hosting.rollout import BackendHostingRollout


@dataclass(frozen=True)
class ManagementEndpoint:
    service: HostingManagementService
    store: HostingManagementStore
    bot_id: str
    owner_id: str
    verifier: ManagementProofVerifier


def build_management_endpoint(
    runtime, connection, state_dir, verifier: ManagementProofVerifier
) -> ManagementEndpoint:
    store = SqliteHostingManagementStore(state_dir)
    runtime.rollout = BackendHostingRollout(state_dir, runtime.binding, verifier)
    runtime.delivery.rollout_check = runtime.require_rollout
    service = HostingManagement(
        runtime,
        HostingAuthorization(runtime.binding, connection, runtime.state),
        store,
        clock_ms=runtime.clock_ms,
    )
    return ManagementEndpoint(
        service, store, runtime.binding.bot_id, runtime.binding.owner_id, verifier
    )
