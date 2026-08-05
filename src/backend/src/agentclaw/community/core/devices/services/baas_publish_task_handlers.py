from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Callable, Optional

from agentclaw.community.core.bot_management.engines import resolve_provisioning
from agentclaw.community.core.bot_management.utils import clear_baas_publish_failure_ext
from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.events.bus import get_event_bus
from agentclaw.community.core.events.types import BaasPublishCompletedEvent
from agentclaw.community.core.service_bot.services.arka_image_pin import (
    persist_default_image_policy,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.types import (
    Complete,
    Fail,
    Reschedule,
    Retry,
    TaskOutcome,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.log import get_logger

BAAS_CREATE_PUBLISH_POLL_TASK = "baas.create.publish_poll"
BAAS_CREATE_INIT_TASK = "baas.create.init"
BAAS_RESTART_PUBLISH_POLL_TASK = "baas.restart.publish_poll"
RESTART_IMAGE_POLICY_ON_SUCCESS_KEY = "restart_image_policy_on_success"
_DEFAULT_IMAGE_POLICY_VALUE = "default"
_CREATE_PUBLISH_TIMEOUT_SECONDS = 600
_RESTART_PUBLISH_TIMEOUT_SECONDS = 600
_CREATE_INIT_DEADLINE_SECONDS = 86400
_PUBLISH_PROGRESS_TRANSIENT_ERROR = "get_publish_progress transient error"

logger = get_logger()


def _publish_baas_completed(
    *,
    binding_id: int,
    bot_id: str,
    owner_id: str,
    publish_id: int,
    publish_kind: str,
) -> None:
    """Publish an identity-only wake-up after the guarded BaaS success path."""

    get_event_bus().publish(
        BaasPublishCompletedEvent(
            binding_id=binding_id,
            bot_id=bot_id,
            owner_id=owner_id,
            publish_id=publish_id,
            publish_kind=publish_kind,
        )
    )


def build_create_publish_poll_payload(
    *,
    binding_id: int,
    bot_id: str,
    owner_id: str,
    publish_id: int,
    started_at_epoch_s: float,
) -> dict:
    return {
        "binding_id": binding_id,
        "bot_id": bot_id,
        "owner_id": owner_id,
        "publish_id": publish_id,
        "started_at_epoch_s": started_at_epoch_s,
    }


def build_create_init_payload(
    *,
    binding_id: int,
    bot_id: str,
    owner_id: str,
    publish_id: int,
) -> dict:
    return {
        "binding_id": binding_id,
        "bot_id": bot_id,
        "owner_id": owner_id,
        "publish_id": publish_id,
    }


def build_restart_publish_poll_payload(
    *,
    binding_id: int,
    bot_id: str,
    owner_id: str,
    publish_id: int,
    started_at_epoch_s: float,
    bot_uuid: str | None,
    image_policy_on_success: str | None = None,
) -> dict:
    payload = {
        "binding_id": binding_id,
        "bot_id": bot_id,
        "owner_id": owner_id,
        "publish_id": publish_id,
        "started_at_epoch_s": started_at_epoch_s,
        "bot_uuid": bot_uuid,
    }
    if image_policy_on_success is not None:
        payload["image_policy_on_success"] = image_policy_on_success
    return payload


def _require_int(payload: Optional[dict], key: str) -> int:
    if not isinstance(payload, dict) or key not in payload:
        raise ValueError(f"missing required field: {key}")
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"field {key} must be int")
    return value


def _require_str(payload: Optional[dict], key: str) -> str:
    if not isinstance(payload, dict) or key not in payload:
        raise ValueError(f"missing required field: {key}")
    value = payload[key]
    if not isinstance(value, str):
        raise ValueError(f"field {key} must be str")
    return value


def _require_number(payload: Optional[dict], key: str) -> int | float:
    if not isinstance(payload, dict) or key not in payload:
        raise ValueError(f"missing required field: {key}")
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"field {key} must be int or float")
    return value


def _require_optional_str(payload: Optional[dict], key: str) -> str | None:
    if not isinstance(payload, dict) or key not in payload:
        raise ValueError(f"missing required field: {key}")
    value = payload[key]
    if value is not None and not isinstance(value, str):
        raise ValueError(f"field {key} must be str or None")
    return value


def _optional_str(payload: Optional[dict], key: str) -> str | None:
    if not isinstance(payload, dict):
        raise ValueError("payload must be dict")
    value = payload.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"field {key} must be str or None")
    return value


def _business_timed_out(
    started_at_epoch_s: float,
    timeout_s: float,
    clock: Callable[[], float],
) -> bool:
    return (clock() - started_at_epoch_s) >= timeout_s


def _binding_is_terminal(binding: Any) -> bool:
    if binding is None:
        return True
    return getattr(binding, "status", None) in {
        DeviceBindingStatus.ACTIVE.value,
        DeviceBindingStatus.FAILED.value,
    }


def _payload_publish_id_matches(binding: Any, publish_id: int, prop_key: str) -> bool:
    props = getattr(binding, "device_props", None) or {}
    current_publish_id = props.get(prop_key)
    if current_publish_id is None:
        return False
    return str(current_publish_id) == str(publish_id)


class BaasCreatePublishPollHandler:
    def __init__(
        self,
        *,
        binding_repository: Any,
        baas_service: Any,
        task_queue_service: Any,
        baas_device_service: Any | None = None,
        poll_delay_seconds: float = 5.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._binding_repository = binding_repository
        self._baas_service = baas_service
        self._baas_device_service = baas_device_service or baas_service
        self._task_queue_service = task_queue_service
        self._poll_delay_seconds = poll_delay_seconds
        self._clock = clock

    @property
    def task_type(self) -> str:
        return BAAS_CREATE_PUBLISH_POLL_TASK

    def handle(self, payload: Optional[dict]) -> TaskOutcome:
        try:
            binding_id = _require_int(payload, "binding_id")
            bot_id = _require_str(payload, "bot_id")
            owner_id = _require_str(payload, "owner_id")
            publish_id = _require_int(payload, "publish_id")
            started_at_epoch_s = _require_number(payload, "started_at_epoch_s")
        except (KeyError, ValueError) as exc:
            return Fail(f"invalid payload: {exc}")

        binding = self._binding_repository.get_by_id(binding_id)
        if _binding_is_terminal(binding):
            return Complete()
        if getattr(binding, "device_provider", None) != "baas":
            return Complete()
        if not _payload_publish_id_matches(binding, publish_id, "publish_id"):
            return Complete()
        if _business_timed_out(
            started_at_epoch_s=started_at_epoch_s,
            timeout_s=_CREATE_PUBLISH_TIMEOUT_SECONDS,
            clock=self._clock,
        ):
            self._baas_device_service._mark_service_start_failed(
                binding_id=binding_id,
                error=(
                    "BaaS publish polling timeout after "
                    f"{_CREATE_PUBLISH_TIMEOUT_SECONDS}s (publish_id={publish_id})"
                ),
            )
            return Complete()

        status = self._baas_device_service.poll_publish_once(publish_id=publish_id)
        if status is None:
            return Retry(_PUBLISH_PROGRESS_TRANSIENT_ERROR)
        if status == "PENDING":
            return Reschedule(self._poll_delay_seconds)
        if status == "FAILED":
            self._baas_device_service._mark_service_start_failed(
                binding_id=binding_id,
                error=f"BaaS publish FAILED: publish_id={publish_id}",
            )
            return Complete()
        if status == "ACTIVE":
            self._task_queue_service.enqueue(
                BAAS_CREATE_INIT_TASK,
                build_create_init_payload(
                    binding_id=binding_id,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    publish_id=publish_id,
                ),
                deadline_seconds=_CREATE_INIT_DEADLINE_SECONDS,
            )
            return Complete()
        return Retry(f"unexpected publish status: {status}")


class BaasCreateInitTaskHandler:
    def __init__(self, *, binding_repository: Any, baas_device_service: Any) -> None:
        self._binding_repository = binding_repository
        self._baas_device_service = baas_device_service

    @property
    def task_type(self) -> str:
        return BAAS_CREATE_INIT_TASK

    def handle(self, payload: Optional[dict]) -> TaskOutcome:
        try:
            binding_id = _require_int(payload, "binding_id")
            _require_str(payload, "bot_id")
            _require_str(payload, "owner_id")
            publish_id = _require_int(payload, "publish_id")
        except ValueError as exc:
            return Fail(f"invalid payload: {exc}")

        binding = self._binding_repository.get_by_id(binding_id)
        if binding is None:
            return Complete()
        if getattr(binding, "device_provider", None) != "baas":
            return Complete()
        if not _payload_publish_id_matches(binding, publish_id, "publish_id"):
            return Complete()
        if getattr(binding, "status", None) == DeviceBindingStatus.ACTIVE.value:
            _publish_baas_completed(
                binding_id=binding_id,
                bot_id=payload["bot_id"],
                owner_id=payload["owner_id"],
                publish_id=publish_id,
                publish_kind="create",
            )
            return Complete()
        if getattr(binding, "status", None) == DeviceBindingStatus.FAILED.value:
            return Complete()
        success, message = self._baas_device_service.run_create_init_once(
            binding_id=binding_id,
            bot_id=payload["bot_id"],
            owner_id=payload["owner_id"],
            publish_id=publish_id,
        )
        if not success:
            self._baas_device_service._mark_service_start_failed(
                binding_id=binding_id,
                error=message,
            )
        else:
            _publish_baas_completed(
                binding_id=binding_id,
                bot_id=payload["bot_id"],
                owner_id=payload["owner_id"],
                publish_id=publish_id,
                publish_kind="create",
            )
        return Complete()


class BaasRestartPublishPollHandler:
    def __init__(
        self,
        *,
        binding_repository: Any,
        baas_service: Any | None = None,
        baas_device_service: Any | None = None,
        bot_repository: Any | None = None,
        publish_repository: Any | None = None,
        template_service: Any | None = None,
        poll_delay_seconds: float = 10.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._binding_repository = binding_repository
        self._baas_device_service = baas_device_service or baas_service
        self._bot_repository = bot_repository
        self._publish_repository = publish_repository
        self._template_service = template_service
        self._poll_delay_seconds = poll_delay_seconds
        self._clock = clock

    @property
    def task_type(self) -> str:
        return BAAS_RESTART_PUBLISH_POLL_TASK

    def handle(self, payload: Optional[dict]) -> TaskOutcome:
        parsed = self._parse_payload(payload)
        if isinstance(parsed, Fail):
            return parsed
        (
            binding_id,
            bot_id,
            owner_id,
            publish_id,
            started_at_epoch_s,
            bot_uuid,
            image_policy_on_success,
        ) = parsed

        binding = self._binding_repository.get_by_id(binding_id)
        image_policy_on_success = self._resolve_image_policy_on_success(
            binding=binding,
            publish_id=publish_id,
            payload_value=image_policy_on_success,
        )
        if (
            binding is not None
            and getattr(binding, "status", None) == DeviceBindingStatus.ACTIVE.value
            and getattr(binding, "device_provider", None) == "baas"
            and _payload_publish_id_matches(
                binding,
                publish_id,
                "restart_publish_id",
            )
        ):
            return self._finalize_success(
                binding_id=binding_id,
                bot_id=bot_id,
                owner_id=owner_id,
                publish_id=publish_id,
                image_policy_on_success=image_policy_on_success,
            )
        preflight = self._preflight(binding=binding, publish_id=publish_id)
        if preflight is not None:
            return preflight
        bot = None
        if self._bot_repository is not None:
            bot = self._bot_repository.get_by_binding_id(binding_id)
        if isinstance(bot, dict) and bot.get("status") in {
            DeviceBindingStatus.ACTIVE.value,
            DeviceBindingStatus.FAILED.value,
        }:
            return Complete()
        if _business_timed_out(
            started_at_epoch_s=started_at_epoch_s,
            timeout_s=_RESTART_PUBLISH_TIMEOUT_SECONDS,
            clock=self._clock,
        ):
            self._persist_failed(
                bot_id=bot_id,
                owner_id=owner_id,
                binding_id=binding_id,
                publish_id=publish_id,
                message=(
                    "BaaS publish timeout after "
                    f"{_RESTART_PUBLISH_TIMEOUT_SECONDS}s (publish_id={publish_id})"
                ),
            )
            return Complete()
        if self._baas_device_service is None:
            return Retry("restart publish status service unavailable")

        status = self._baas_device_service.poll_publish_once(publish_id=publish_id)
        if status is None:
            return Retry(_PUBLISH_PROGRESS_TRANSIENT_ERROR)
        if status == DeviceBindingStatus.PENDING.value:
            return Reschedule(self._poll_delay_seconds)
        return self._handle_terminal_restart_status(
            status=status,
            bot_id=bot_id,
            owner_id=owner_id,
            binding_id=binding_id,
            publish_id=publish_id,
            bot_uuid=bot_uuid,
            bot=bot,
            image_policy_on_success=image_policy_on_success,
        )

    def _parse_payload(
        self,
        payload: Optional[dict],
    ) -> tuple[int, str, str, int, int | float, str | None, str | None] | Fail:
        try:
            return (
                _require_int(payload, "binding_id"),
                _require_str(payload, "bot_id"),
                _require_str(payload, "owner_id"),
                _require_int(payload, "publish_id"),
                _require_number(payload, "started_at_epoch_s"),
                _require_optional_str(payload, "bot_uuid"),
                _optional_str(payload, "image_policy_on_success"),
            )
        except (KeyError, ValueError) as exc:
            return Fail(f"invalid payload: {exc}")

    @staticmethod
    def _resolve_image_policy_on_success(
        *,
        binding: Any,
        publish_id: int,
        payload_value: str | None,
    ) -> str | None:
        """Read the restart intent from Binding, with old-task compatibility."""
        if _payload_publish_id_matches(binding, publish_id, "restart_publish_id"):
            props = getattr(binding, "device_props", None) or {}
            if RESTART_IMAGE_POLICY_ON_SUCCESS_KEY in props:
                value = props.get(RESTART_IMAGE_POLICY_ON_SUCCESS_KEY)
                return value if isinstance(value, str) else None
        return payload_value

    @staticmethod
    def _preflight(*, binding: Any, publish_id: int) -> TaskOutcome | None:
        if _binding_is_terminal(binding):
            return Complete()
        if getattr(binding, "device_provider", None) != "baas":
            return Complete()
        if not _payload_publish_id_matches(binding, publish_id, "restart_publish_id"):
            return Complete()
        return None

    def _handle_terminal_restart_status(
        self,
        *,
        status: str,
        bot_id: str,
        owner_id: str,
        binding_id: int,
        publish_id: int,
        bot_uuid: str | None,
        bot: Any,
        image_policy_on_success: str | None = None,
    ) -> TaskOutcome:
        if status == DeviceBindingStatus.ACTIVE.value:
            codefuse_token = self._read_codefuse_token(bot_id=bot_id, bot=bot)
            write_err = (
                self._baas_device_service.refresh_codefuse_token_on_publish_success(
                    bot_uuid=bot_uuid,
                    codefuse_token=codefuse_token,
                )
            )
            if write_err is not None:
                logger.warning(
                    "[BaasRestartPublishPollHandler] codefuse refresh failed for "
                    "bot_id=%s publish_id=%s: %s",
                    bot_id,
                    publish_id,
                    write_err,
                )
                self._persist_failed(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    binding_id=binding_id,
                    publish_id=publish_id,
                )
                return Complete()
            self._persist_restart_status(
                bot_id=bot_id,
                owner_id=owner_id,
                binding_id=binding_id,
                status=DeviceBindingStatus.ACTIVE.value,
                publish_id=publish_id,
            )
            return self._finalize_success(
                binding_id=binding_id,
                bot_id=bot_id,
                owner_id=owner_id,
                publish_id=publish_id,
                image_policy_on_success=image_policy_on_success,
            )
        if status == DeviceBindingStatus.FAILED.value:
            self._persist_failed(
                bot_id=bot_id,
                owner_id=owner_id,
                binding_id=binding_id,
                publish_id=publish_id,
            )
            return Complete()
        return Retry(f"unexpected publish status: {status}")

    def _finalize_success(
        self,
        *,
        binding_id: int,
        bot_id: str,
        owner_id: str,
        publish_id: int,
        image_policy_on_success: str | None,
    ) -> TaskOutcome:
        if image_policy_on_success == _DEFAULT_IMAGE_POLICY_VALUE:
            if self._bot_repository is None or self._publish_repository is None:
                return Retry("default-image persistence service unavailable")
            try:
                persist_default_image_policy(
                    bot_repository=self._bot_repository,
                    publish_repository=self._publish_repository,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    env=get_current_env(),
                )
                # Clear only after Bot + Draft have accepted DEFAULT. Failure
                # keeps the durable intent for task replay/process recovery.
                self._binding_repository.update_device_props(
                    binding_id=binding_id,
                    props={RESTART_IMAGE_POLICY_ON_SUCCESS_KEY: None},
                )
            except Exception as exc:
                logger.warning(
                    "[BaasRestartPublishPollHandler] default-image persistence "
                    "failed for bot_id=%s publish_id=%s: %s",
                    bot_id,
                    publish_id,
                    exc,
                )
                return Retry(str(exc))
        _publish_baas_completed(
            binding_id=binding_id,
            bot_id=bot_id,
            owner_id=owner_id,
            publish_id=publish_id,
            publish_kind="restart",
        )
        return Complete()

    def _persist_failed(
        self,
        *,
        bot_id: str,
        owner_id: str,
        binding_id: int,
        publish_id: int,
        message: str | None = None,
    ) -> None:
        self._persist_restart_status(
            bot_id=bot_id,
            owner_id=owner_id,
            binding_id=binding_id,
            status=DeviceBindingStatus.FAILED.value,
            publish_id=publish_id,
            failure_message=message,
        )

    def _persist_restart_status(
        self,
        *,
        bot_id: str,
        owner_id: str,
        binding_id: int,
        status: str,
        publish_id: int,
        failure_message: str | None = None,
    ) -> None:
        update_data = self._build_bot_update(
            bot_id=bot_id,
            owner_id=owner_id,
            status=status,
            publish_id=publish_id,
            failure_message=failure_message,
        )
        if self._bot_repository is not None:
            self._bot_repository.update_by_owner(bot_id, owner_id, update_data)
        self._binding_repository.update_status(
            binding_id=binding_id,
            status=status,
        )

    def _build_bot_update(
        self,
        *,
        bot_id: str,
        owner_id: str,
        status: str,
        publish_id: int,
        failure_message: str | None = None,
    ) -> dict:
        update_data: dict = {"status": status}
        current_ext = self._get_current_ext(bot_id=bot_id, owner_id=owner_id)
        if current_ext is None:
            return update_data

        restart_publish_id = str(publish_id)
        if status == DeviceBindingStatus.ACTIVE.value:
            ext = clear_baas_publish_failure_ext(current_ext)
            ext["restart_publish_id"] = restart_publish_id
            if ext != current_ext:
                update_data["ext"] = ext
        elif status == DeviceBindingStatus.FAILED.value:
            ext = clear_baas_publish_failure_ext(current_ext)
            ext["start_status"] = "FAILED"
            ext["start_message"] = (
                failure_message
                or f"BaaS publish FAILED: publish_id={restart_publish_id}"
            )
            ext["restart_publish_id"] = restart_publish_id
            update_data["ext"] = ext
        return update_data

    def _get_current_ext(self, *, bot_id: str, owner_id: str) -> dict | None:
        if self._bot_repository is None:
            return None
        getter = getattr(self._bot_repository, "get_by_id_and_owner", None)
        if getter is None:
            return None
        try:
            bot = getter(bot_id, owner_id)
        except Exception as exc:
            logger.warning(
                "[BaasRestartPublishPollHandler] failed to read bot ext "
                "for bot_id=%s owner_id=%s: %s",
                bot_id,
                owner_id,
                exc,
            )
            return None
        ext = (bot or {}).get("ext") if isinstance(bot, dict) else None
        return dict(ext) if isinstance(ext, dict) else {}

    def _read_codefuse_token(self, *, bot_id: str, bot: Any) -> str | None:
        if not isinstance(bot, dict):
            return None
        base_ctx, strategy = resolve_provisioning(
            bot_id=bot_id,
            owner_id=bot.get("owner_id") or "",
            active_engine=bot.get("active_engine"),
            bot_type=bot.get("bot_type") or "",
            template_type=bot.get("template_type"),
            template_config=None,
        )
        # Fast no-op for engines/templates that never deploy runtime tokens.
        if not strategy.should_encrypt_template_token(base_ctx):
            return None
        if self._template_service is None:
            return None
        try:
            template_config = self._template_service.get_template_config(bot_id)
        except Exception as exc:
            logger.warning(
                "[BaasRestartPublishPollHandler] failed to reload template config "
                "for bot_id=%s: %s",
                bot_id,
                exc,
            )
            return None
        if not isinstance(template_config, dict):
            return None
        ctx = replace(base_ctx, template_config=template_config)
        return strategy.extract_runtime_token(ctx)


class BaasPublishTaskLifecycle(LifecycleBase):
    def __init__(
        self,
        *,
        registry: HandlerRegistry,
        binding_repository: Any,
        baas_service: Any,
        task_queue_service: Any,
        baas_device_service: Any,
        bot_repository: Any,
        publish_repository: Any | None = None,
        template_service: Any = None,
    ) -> None:
        self._registry = registry
        self._binding_repository = binding_repository
        self._baas_service = baas_service
        self._task_queue_service = task_queue_service
        self._baas_device_service = baas_device_service
        self._bot_repository = bot_repository
        self._publish_repository = publish_repository
        self._template_service = template_service

    async def bootstrap(self) -> None:
        self._registry.register(
            BaasCreatePublishPollHandler(
                binding_repository=self._binding_repository,
                baas_service=self._baas_service,
                task_queue_service=self._task_queue_service,
                baas_device_service=self._baas_device_service,
            )
        )
        self._registry.register(
            BaasCreateInitTaskHandler(
                binding_repository=self._binding_repository,
                baas_device_service=self._baas_device_service,
            )
        )
        self._registry.register(
            BaasRestartPublishPollHandler(
                binding_repository=self._binding_repository,
                baas_device_service=self._baas_device_service,
                bot_repository=self._bot_repository,
                publish_repository=self._publish_repository,
                template_service=self._template_service,
            )
        )
