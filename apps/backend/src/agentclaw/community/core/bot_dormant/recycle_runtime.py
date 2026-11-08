"""Recycle orchestration over existing engine policy and pinned device hooks."""

from __future__ import annotations

import time

from agentclaw.community.core.bot_dormant.recycle_state import RecycleTargetChanged
from agentclaw.community.core.bot_management.engines.aicoding.backup_operation import (
    BackupOperation,
    current_backup,
)
from agentclaw.community.core.bot_management.engines.registry import (
    resolve_restart_strategy,
)


class DeviceRecycleRuntime:
    def __init__(self, devices, bots, backup_dependencies):
        self.devices, self.bots, self.dependencies = devices, bots, backup_dependencies

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
            runtime = self.dependencies.baas_runtime()

            def inventory():
                devices = runtime.list_devices_by_bot_uuid(
                    bot_uuid=record["target"]["bot_uuid"]
                )
                if not isinstance(devices, list):
                    raise RecycleTargetChanged("Invalid physical inventory")
                result = set()
                for device in devices:
                    if not isinstance(device, dict):
                        raise RecycleTargetChanged("Invalid physical target")
                    if device.get("status") in {"RELEASED", "STOPPED"}:
                        continue
                    identity = device.get("provider_device_id")
                    if not isinstance(identity, str) or not identity:
                        raise RecycleTargetChanged("Physical target identity missing")
                    result.add(identity)
                if (
                    not devices
                    and runtime.get_bot(bot_uuid=record["target"]["bot_uuid"]).get(
                        "status"
                    )
                    != "RELEASED"
                ):
                    raise RecycleTargetChanged(
                        "Empty inventory is not release evidence"
                    )
                return sorted(result)

            physical_targets = inventory()
            if (
                record.get("physical_targets") is not None
                and record["physical_targets"] != physical_targets
            ):
                raise RecycleTargetChanged("Physical targets changed during backup")
            save(physical_targets=physical_targets)
        ctx, strategy = resolve_restart_strategy(bot)
        started = record.get("backup_started_at", time.time())
        policy = record.get("fail_block")
        if policy is None:
            # The existing strategy owns config interpretation, including the
            # default-false fallback. Non-coding strategies never resolve it.
            reader = getattr(strategy, "_fastdisk_backup_blocking", lambda: False)
            policy = reader()
            policy = save(fail_block=policy)["fail_block"]
        operation = BackupOperation(
            operation_id=record["operation_id"],
            started_at=started,
            confirmed=set(record.get("fastdisk_targets") or []),
            persist=save,
        )
        token = current_backup.set(operation)
        try:
            verify = strategy.prepare_restart(
                ctx,
                binding_id=bot.get("binding_id"),
                device_service_provider=self.devices,
                bot_repository=self.bots,
                restart_dependencies=self.dependencies,
                operation_id=record["operation_id"],
                strict_deadline=time.monotonic()
                + max(0, 300 - (time.time() - started)),
                blocking_enabled=lambda: policy,
            )
        finally:
            current_backup.reset(token)

        def check():
            if (
                self.target(
                    self.bots.get_by_id_and_owner(bot["bot_id"], bot["owner_id"])
                )
                != record["target"]
            ):
                raise RecycleTargetChanged("Recycle target changed")
            if physical_targets is not None and inventory() != physical_targets:
                raise RecycleTargetChanged("Physical targets changed before release")
            token = current_backup.set(operation)
            try:
                if verify is not None:
                    verify()
            finally:
                current_backup.reset(token)

        return check

    def submit_release(self, target, request_id, owner_id):
        if target["binding_id"] is None:
            return {"confirmed": True}
        return self.devices().submit_recycle_release(
            target=target,
            request_id=request_id,
            owner_id=owner_id,
        )

    def observe_release(self, target, release):
        if target["binding_id"] is None:
            return "RELEASED"
        return self.devices().observe_recycle_release(target=target, release=release)
