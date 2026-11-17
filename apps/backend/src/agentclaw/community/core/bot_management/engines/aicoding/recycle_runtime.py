"""Recycle orchestration over existing engine policy and pinned device hooks."""

from __future__ import annotations

from agentclaw.community.core.service_bot.services.baas_service import (
    BaasBotNotFoundError,
)

from agentclaw.community.core.bot_management.engines.aicoding.recycle_state import (
    RecycleTargetChanged,
)
from agentclaw.community.core.bot_management.engines.registry import (
    resolve_restart_strategy,
)

from .restart_dependencies import BaasRecycleRuntime, RestartBackupDependencies


class AicodingRecycleRuntime:
    def __init__(
        self, devices, bots, backup_dependencies: RestartBackupDependencies, arca_transport
    ):
        self.devices, self.bots, self.dependencies = devices, bots, backup_dependencies
        self.arca_transport = arca_transport

    def target(self, bot):
        binding_id = bot.get("binding_id")
        if binding_id is None:
            return {"binding_id": None, "provider": None, "device_id": None}
        binding = self.devices().get_device(binding_id=binding_id)
        if binding is None:
            raise RecycleTargetChanged("Binding missing")
        props = binding.device_props or {}
        return {
            "binding_id": binding_id,
            "provider": binding.device_provider,
            "device_id": binding.device_id,
            "sandbox_id": props.get("sandbox_id"),
            "bot_uuid": props.get("bot_uuid") or binding.device_id,
        }

    def prepare(self, bot, record, save):
        if self.target(bot) != record["target"]:
            raise RecycleTargetChanged("Recycle target changed")
        physical_targets = None
        if record["target"]["provider"] in {"baas", "local"}:
            runtime = _baas_recycle_runtime(self.dependencies)

            physical_targets = _physical_inventory(
                runtime, bot_uuid=record["target"]["bot_uuid"]
            )
            if (
                record.get("physical_targets") is not None
                and record["physical_targets"] != physical_targets
            ):
                raise RecycleTargetChanged("Physical targets changed during backup")
            save(physical_targets=physical_targets)
        ctx, strategy = resolve_restart_strategy(bot)
        verify = strategy.prepare_recycle(
            ctx,
            record=record,
            save=save,
            binding_id=bot.get("binding_id"),
            device_service_provider=self.devices,
            bot_repository=self.bots,
            restart_dependencies=self.dependencies,
        )

        def check():
            current_bot = self.bots.get_by_id_and_owner(bot["bot_id"], bot["owner_id"])
            if self.target(current_bot) != record["target"]:
                raise RecycleTargetChanged("Recycle target changed")
            if physical_targets is not None and _physical_inventory(
                runtime, bot_uuid=record["target"]["bot_uuid"]
            ) != physical_targets:
                raise RecycleTargetChanged("Physical targets changed before release")
            if verify is not None:
                verify()

        return check

    def submit_release(self, target, request_id, owner_id):
        if target["binding_id"] is None:
            return {"confirmed": True}
        return submit_release(
            self.dependencies,
            self.arca_transport,
            target=target,
            request_id=request_id,
            owner_id=owner_id,
        )

    def observe_release(self, target, release, physical_targets):
        if target["binding_id"] is None:
            return "RELEASED"
        return observe_release(
            self.dependencies, self.arca_transport, target=target, release=release,
            physical_targets=physical_targets,
        )


def _physical_inventory(runtime: BaasRecycleRuntime, *, bot_uuid: str) -> list[str]:
    """Read every physical identity, including stopped/released logical rows."""
    devices = runtime.list_devices_by_bot_uuid(bot_uuid=bot_uuid)
    if not isinstance(devices, list):
        raise RecycleTargetChanged("Invalid physical inventory")
    identities = set()
    for device in devices:
        if not isinstance(device, dict):
            raise RecycleTargetChanged("Invalid physical target")
        identity = device.get("provider_device_id")
        if not isinstance(identity, str) or not identity:
            raise RecycleTargetChanged("Physical target identity missing")
        identities.add(identity)
    if not identities:
        raise RecycleTargetChanged("Physical targets missing")
    return sorted(identities)


def submit_baas_release(baas, *, target, request_id, owner_id):
    try:
        result = baas.destroy_bot(
            bot_uuid=target["bot_uuid"],
            operator=owner_id,
            request_id=request_id,
        )
    except BaasBotNotFoundError:
        # A missing logical Bot cannot prove orphan physical devices are gone.
        return {"request_id": request_id}
    return {"publish_id": result.get("publish_id"), "request_id": request_id}


def observe_baas_release(baas: BaasRecycleRuntime, *, physical_targets):
    # A logical RELEASED row, successful workflow or empty inventory may conceal
    # a failed provider destroy. Only the previously pinned physical IDs count.
    if not isinstance(physical_targets, list) or not physical_targets or any(
        not isinstance(identity, str) or not identity for identity in physical_targets
    ):
        return "UNKNOWN"
    # The pinned provider identities are the authoritative release evidence.
    # Logical Bot/workflow reads can disappear before or after physical cleanup.
    for identity in physical_targets:
        if baas.is_physical_device_destroyed(provider_device_id=identity) is not True:
            return "UNKNOWN"
    return "RELEASED"


def _baas_recycle_runtime(dependencies: RestartBackupDependencies) -> BaasRecycleRuntime:
    runtime = dependencies.baas_runtime()
    if not isinstance(runtime, BaasRecycleRuntime):
        raise RuntimeError("Coding recycle BaaS capabilities unavailable")
    return runtime


class ArcaRecycleTransport:
    """Provider primitives injected by composition, like ArcaRestartTransport.

    Missing capabilities fail closed; this object never selects another provider.
    """

    def __init__(self, *, destroy=None, is_destroyed=None):
        self.destroy, self.is_destroyed = destroy, is_destroyed

    def submit(self, target, request_id):
        if self.destroy is None or self.destroy(target["sandbox_id"]) is not True:
            raise RuntimeError("Arca recycle release unconfirmed")
        return {"request_id": request_id}

    def observe(self, target):
        if self.is_destroyed is None:
            return "UNKNOWN"
        return (
            "RELEASED" if self.is_destroyed(target["sandbox_id"]) is True else "UNKNOWN"
        )


def submit_release(dependencies, arca, *, target, request_id, owner_id):
    if target["provider"] == "arca":
        return arca().submit(target, request_id)
    if target["provider"] in {"baas", "local"}:
        return submit_baas_release(
            _baas_recycle_runtime(dependencies),
            target=target,
            request_id=request_id,
            owner_id=owner_id,
        )
    raise RuntimeError("Recycle provider unavailable")


def observe_release(dependencies, arca, *, target, release, physical_targets):
    if target["provider"] == "arca":
        return arca().observe(target)
    if target["provider"] in {"baas", "local"}:
        return observe_baas_release(
            _baas_recycle_runtime(dependencies), physical_targets=physical_targets,
        )
    return "UNKNOWN"
