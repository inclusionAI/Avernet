"""Shared Core operation for recycling a personal managed-cloud Bot."""

from __future__ import annotations

from injector import inject
from agentclaw.community.core.bot_management.engines.registry import (
    resolve_restart_strategy,
)
from agentclaw.community.core.bot_management.engines.restart_contract import (
    BaasRecycleRuntime, RecycleServices, RecycleTargetChanged,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasBotNotFoundError

from agentclaw.community.core.bot_dormant.protocols import BotServiceProtocol
from agentclaw.community.core.bot_dormant.types import BotLifecycleResult
from agentclaw.community.core.bot_management.services.bot_service import (
    BotInvalidLifecycleStateError,
    BotNotFoundError,
    BotOperationNotAllowedError,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.passport import PassportPlugin


logger = get_logger()


class RecycleReleaseFailed(RuntimeError):
    """The Bot resource could not be confirmed released."""


class RecycleBotService:
    """Release one personal Bot resource and mark the Bot as recycled."""

    @inject
    def __init__(
        self,
        bot_service: BotServiceProtocol,
        passport_plugin: PassportPlugin,
        recycle_services: RecycleServices | None = None,
    ) -> None:
        self._bot_service = bot_service
        self._passport = passport_plugin
        self._recycle_services = recycle_services

    def submit(self, *, bot_id, owner_id, owner_name=None, request_id="",
               source="openapi", reason=None):
        bot = self._bot_service.get_bot(bot_id=bot_id, user_id=owner_id)
        if not bot:
            raise BotNotFoundError("Bot not found")
        self.ensure_supported(bot)
        if self._recycle_services is None:
            from agentclaw.community.core.bot_management.engines.restart_contract import RecycleUnavailable
            raise RecycleUnavailable("Recycle queue unavailable")
        metadata = {} if source == "openapi" and reason is None else {
            "source": source, "reason": reason,
        }
        result = self._recycle_services.operations().submit(
            bot_id=bot_id, owner_id=owner_id, owner_name=owner_name, request_id=request_id,
            **metadata,
        )
        if result.pop("_synchronous", False):
            completed = self.recycle(
                bot_id=bot_id, owner_id=owner_id, owner_name=owner_name,
            )
            return {
                **completed.__dict__,
                "operation_id": None,
                "phase": None,
            }
        return result

    def get_operation(self, *, bot_id, owner_id, operation_id):
        if self._recycle_services is None:
            raise BotNotFoundError("Recycle operation not found")
        return self._recycle_services.operations().get_operation(
            bot_id=bot_id, owner_id=owner_id, operation_id=operation_id,
        )

    def recycle(
        self,
        *,
        bot_id: str,
        owner_id: str,
        owner_name: str | None = None,
    ) -> BotLifecycleResult:
        bot = self._bot_service.get_bot(bot_id=bot_id, user_id=owner_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")
        self.ensure_supported(bot)
        from agentclaw.community.core.bot_management.engines.registry import (
            assert_lifecycle_mutable,
        )

        assert_lifecycle_mutable(bot)

        status = str(bot.get("status") or "")
        if status == "RECYCLED":
            return BotLifecycleResult(
                bot_id=bot_id,
                owner_id=owner_id,
                status="RECYCLED",
                changed=False,
            )
        if status != "ACTIVE":
            raise BotInvalidLifecycleStateError(
                bot_id=bot_id,
                current_status=status or "UNKNOWN",
            )

        stop_kwargs = {
            "bot_id": bot_id,
            "user_id": owner_id,
            "release_reason": "dormant_recycle",
        }
        if owner_name:
            stop_kwargs["nick_name"] = owner_name
        release_ok = self._bot_service.stop_bot(**stop_kwargs)
        if release_ok is False:
            raise RecycleReleaseFailed(
                f"stop_bot reported release failure for bot_id={bot_id}"
            )

        self._bot_service.update_status(
            bot_id=bot_id,
            user_id=owner_id,
            status="RECYCLED",
        )
        try:
            self._passport.freeze_agent_passport(
                bot_id=bot_id,
                owner_workno=owner_id,
                reason="dormant recycle",
            )
        except Exception as exc:
            logger.warning(
                "[RecycleBotService.recycle] passport freeze failed "
                "bot_id=%s owner_id=%s error=%s",
                bot_id,
                owner_id,
                exc,
            )

        return BotLifecycleResult(
            bot_id=bot_id,
            owner_id=owner_id,
            status="RECYCLED",
            changed=True,
        )

    def ensure_supported(self, bot: dict) -> None:
        """Reject Bot kinds outside the dormant lifecycle boundary."""
        if bot.get("bot_type") != "personal":
            raise BotOperationNotAllowedError(
                "dormant recycle only supports personal bots"
            )
        if self._bot_service.is_teclaw_bot(bot.get("active_engine")):
            raise BotOperationNotAllowedError(
                "teclaw bots use their engine-owned lifecycle"
            )


class ManagedRecycleRuntime:
    def __init__(
        self, devices, bots, backup_dependencies, arca_transport
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

    def should_queue(self, bot):
        ctx, strategy = resolve_restart_strategy(bot)
        return strategy.should_async_recycle(
            ctx,
            binding_id=bot.get("binding_id"),
            device_service_provider=self.devices,
            restart_dependencies=self.dependencies,
            operation_id="recycle-admission",
        )

    def prepare(self, bot, record, save):
        if self.target(bot) != record["target"]:
            raise RecycleTargetChanged("Recycle target changed")
        if record["target"]["provider"] == "arca":
            # Backup may quiesce writes; reject missing release/observation hooks first.
            self.arca_transport().check_capabilities()
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


def _baas_recycle_runtime(dependencies) -> BaasRecycleRuntime:
    runtime = dependencies.baas_runtime()
    if not isinstance(runtime, BaasRecycleRuntime):
        raise RuntimeError("Recycle BaaS capabilities unavailable")
    return runtime


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
