from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Callable, Optional

from agentclaw.community.core.bot_management.engines import resolve_provisioning
from agentclaw.community.core.bot_management.utils import clear_baas_publish_failure_ext
from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.events.bus import get_event_bus
from agentclaw.community.core.events.types import BaasPublishCompletedEvent
from agentclaw.community.core.service_bot.services.arca_image_pin import (
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
RESTART_REQUEST_ID_KEY = "restart_request_id"
RESTART_WORKFLOW_BASELINE_KEY = "restart_workflow_baseline"
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
    publish_id: int | None,
    started_at_epoch_s: float,
    bot_uuid: str | None,
    image_policy_on_success: str | None = None,
    request_id: str | None = None,
    workflow_baseline: int | None = None,
) -> dict:
    payload = {
        "binding_id": binding_id,
        "bot_id": bot_id,
        "owner_id": owner_id,
        "started_at_epoch_s": started_at_epoch_s,
        "bot_uuid": bot_uuid,
    }
    if publish_id is not None:
        payload["publish_id"] = publish_id
    if image_policy_on_success is not None:
        payload["image_policy_on_success"] = image_policy_on_success
    if request_id is not None:
        payload["request_id"] = request_id
    if workflow_baseline is not None:
        payload["workflow_baseline"] = workflow_baseline
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


def _optional_int(payload: Optional[dict], key: str) -> int | None:
    if not isinstance(payload, dict):
        raise ValueError("payload must be dict")
    value = payload.get(key)
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        raise ValueError(f"field {key} must be int or None")
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
        self._baas_service = baas_service
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
            request_id,
            workflow_baseline,
        ) = parsed

        binding = self._binding_repository.get_by_id(binding_id)
        if request_id is not None:
            props = getattr(binding, "device_props", None) or {}
            current_request_id = props.get(RESTART_REQUEST_ID_KEY)
            if current_request_id is None:
                # The task is persisted before the external BaaS mutation. A
                # worker may observe it in the small window before the Binding
                # intent is committed; retry instead of dropping the only
                # recovery mechanism. Preparation failures deliberately clear
                # the intent, so those orphan tasks age out without side effects.
                if _business_timed_out(
                    started_at_epoch_s=started_at_epoch_s,
                    timeout_s=_RESTART_PUBLISH_TIMEOUT_SECONDS,
                    clock=self._clock,
                ):
                    return Complete()
                return Reschedule(self._poll_delay_seconds)
            if current_request_id != request_id:
                return Complete()

        if publish_id is None:
            publish_id = self._resolve_or_adopt_publish_id(
                binding=binding,
                binding_id=binding_id,
                bot_uuid=bot_uuid,
                request_id=request_id,
                workflow_baseline=workflow_baseline,
            )
            if isinstance(publish_id, Fail):
                self._persist_failed(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    binding_id=binding_id,
                    publish_id=None,
                    message=publish_id.error,
                )
                self._clear_restart_recovery_intent(binding_id=binding_id)
                return publish_id
            if isinstance(publish_id, (Complete, Reschedule, Retry)):
                if _business_timed_out(
                    started_at_epoch_s=started_at_epoch_s,
                    timeout_s=_RESTART_PUBLISH_TIMEOUT_SECONDS,
                    clock=self._clock,
                ):
                    self._persist_failed(
                        bot_id=bot_id,
                        owner_id=owner_id,
                        binding_id=binding_id,
                        publish_id=None,
                        message="BaaS restart could not adopt an accepted workflow",
                    )
                    self._clear_restart_recovery_intent(binding_id=binding_id)
                    return Complete()
                return publish_id

        image_policy_on_success = self._resolve_image_policy_on_success(
            binding=binding,
            publish_id=publish_id,
            payload_value=image_policy_on_success,
            request_id=request_id,
        )
        preflight = self._preflight(
            binding=binding,
            publish_id=publish_id,
            request_id=request_id,
        )
        if preflight is not None:
            return preflight
        bot = None
        if self._bot_repository is not None:
            bot = self._bot_repository.get_by_binding_id(binding_id)
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
            self._clear_restart_recovery_intent(binding_id=binding_id)
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
    ) -> tuple[
        int,
        str,
        str,
        int | None,
        int | float,
        str | None,
        str | None,
        str | None,
        int | None,
    ] | Fail:
        try:
            return (
                _require_int(payload, "binding_id"),
                _require_str(payload, "bot_id"),
                _require_str(payload, "owner_id"),
                _optional_int(payload, "publish_id"),
                _require_number(payload, "started_at_epoch_s"),
                _require_optional_str(payload, "bot_uuid"),
                _optional_str(payload, "image_policy_on_success"),
                _optional_str(payload, "request_id"),
                _optional_int(payload, "workflow_baseline"),
            )
        except (KeyError, ValueError) as exc:
            return Fail(f"invalid payload: {exc}")

    def _resolve_or_adopt_publish_id(
        self,
        *,
        binding: Any,
        binding_id: int,
        bot_uuid: str | None,
        request_id: str | None,
        workflow_baseline: int | None,
    ) -> int | TaskOutcome:
        """Resolve the stored workflow id or adopt the one issued after baseline."""
        if binding is None:
            return Complete()
        props = getattr(binding, "device_props", None) or {}
        stored = props.get("restart_publish_id")
        if stored is not None:
            try:
                return int(stored)
            except (TypeError, ValueError):
                return Fail(f"invalid restart_publish_id: {stored!r}")
        if request_id is None or workflow_baseline is None or not bot_uuid:
            return Retry("restart workflow id is not persisted yet")
        if self._baas_service is None:
            return Retry("restart workflow adoption service unavailable")
        try:
            workflows = self._baas_service.list_bot_publishes(bot_uuid)
        except Exception as exc:
            return Retry(f"restart workflow adoption query failed: {exc}")
        candidates = [
            workflow
            for workflow in (workflows or [])
            if isinstance(workflow, dict)
            and str(workflow.get("id", "")).isdigit()
            and int(workflow["id"]) > workflow_baseline
            and workflow.get("publish_type") in {"UPDATE", "RESTART", "CREATE"}
        ]
        if not candidates:
            return Reschedule(self._poll_delay_seconds)
        if len(candidates) > 1:
            ids = sorted(int(workflow["id"]) for workflow in candidates)
            return Fail(
                f"restart workflow adoption is ambiguous for bot_uuid={bot_uuid}: {ids}"
            )
        publish_id = int(candidates[0]["id"])
        self._binding_repository.update_device_props(
            binding_id=binding_id,
            props={
                "publish_id": str(publish_id),
                "restart_publish_id": str(publish_id),
            },
        )
        logger.info(
            "[BaasRestartPublishPollHandler] adopted restart workflow: "
            "binding_id=%s request_id=%s publish_id=%s",
            binding_id,
            request_id,
            publish_id,
        )
        return publish_id

    def _clear_restart_recovery_intent(self, *, binding_id: int) -> None:
        self._binding_repository.update_device_props(
            binding_id=binding_id,
            props={
                RESTART_REQUEST_ID_KEY: None,
                RESTART_WORKFLOW_BASELINE_KEY: None,
                RESTART_IMAGE_POLICY_ON_SUCCESS_KEY: None,
            },
        )

    @staticmethod
    def _resolve_image_policy_on_success(
        *,
        binding: Any,
        publish_id: int,
        payload_value: str | None,
        request_id: str | None,
    ) -> str | None:
        """Read the restart intent from Binding, with old-task compatibility."""
        props = getattr(binding, "device_props", None) or {}
        request_matches = (
            request_id is not None
            and props.get(RESTART_REQUEST_ID_KEY) == request_id
        )
        if request_matches or _payload_publish_id_matches(
            binding, publish_id, "restart_publish_id"
        ):
            if RESTART_IMAGE_POLICY_ON_SUCCESS_KEY in props:
                value = props.get(RESTART_IMAGE_POLICY_ON_SUCCESS_KEY)
                return value if isinstance(value, str) else None
        return payload_value

    @staticmethod
    def _preflight(
        *, binding: Any, publish_id: int, request_id: str | None
    ) -> TaskOutcome | None:
        if binding is None:
            return Complete()
        if getattr(binding, "device_provider", None) != "baas":
            return Complete()
        props = getattr(binding, "device_props", None) or {}
        request_matches = (
            request_id is not None
            and props.get(RESTART_REQUEST_ID_KEY) == request_id
        )
        if not request_matches and not _payload_publish_id_matches(
            binding, publish_id, "restart_publish_id"
        ):
            return Complete()
        # ACTIVE can belong to the runtime that existed before this restart.
        # It is therefore not proof that the current BaaS publish succeeded;
        # matching restart tasks must still poll that publish. FAILED remains a
        # terminal persisted result for the current binding.
        if getattr(binding, "status", None) == DeviceBindingStatus.FAILED.value:
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
            self._clear_restart_recovery_intent(binding_id=binding_id)
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
            except Exception as exc:
                logger.warning(
                    "[BaasRestartPublishPollHandler] default-image persistence "
                    "failed for bot_id=%s publish_id=%s: %s",
                    bot_id,
                    publish_id,
                    exc,
                )
                return Retry(str(exc))
        # Clear only after every success-side persistence step has completed.
        # A failure above keeps the durable request/baseline/policy for replay.
        self._clear_restart_recovery_intent(binding_id=binding_id)
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
        publish_id: int | None,
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
        publish_id: int | None,
        failure_message: str | None = None,
    ) -> None:
        if self._bot_repository is not None:
            updated_bot = self._bot_repository.update_by_owner(
                bot_id, owner_id, {"status": status}
            )
            if updated_bot is None:
                raise RuntimeError(f"Bot status update did not match: bot_id={bot_id}")
            for _attempt in range(3):
                snapshot = self._get_current_ext_snapshot(
                    bot_id=bot_id, owner_id=owner_id
                )
                if snapshot is None:
                    break
                current_ext, expected_ext = snapshot
                updated_ext = self._build_bot_ext(
                    current_ext=current_ext,
                    status=status,
                    publish_id=publish_id,
                    failure_message=failure_message,
                )
                if updated_ext == current_ext:
                    break
                updated = self._bot_repository.compare_and_set_ext(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    expected_ext=expected_ext,
                    ext=updated_ext,
                )
                if updated is not None:
                    break
            else:
                raise RuntimeError(
                    f"Bot restart ext CAS conflicted repeatedly: bot_id={bot_id}"
                )
        self._binding_repository.update_status(
            binding_id=binding_id,
            status=status,
        )

    @staticmethod
    def _build_bot_ext(
        *,
        current_ext: dict,
        status: str,
        publish_id: int | None,
        failure_message: str | None = None,
    ) -> dict:
        restart_publish_id = str(publish_id) if publish_id is not None else None
        if status == DeviceBindingStatus.ACTIVE.value:
            ext = clear_baas_publish_failure_ext(current_ext)
            if restart_publish_id is not None:
                ext["restart_publish_id"] = restart_publish_id
            return ext
        elif status == DeviceBindingStatus.FAILED.value:
            ext = clear_baas_publish_failure_ext(current_ext)
            ext["start_status"] = "FAILED"
            ext["start_message"] = (
                failure_message
                or f"BaaS publish FAILED: publish_id={restart_publish_id}"
            )
            if restart_publish_id is not None:
                ext["restart_publish_id"] = restart_publish_id
            return ext
        return current_ext

    def _get_current_ext_snapshot(
        self, *, bot_id: str, owner_id: str
    ) -> tuple[dict, dict | None] | None:
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
        return (dict(ext) if isinstance(ext, dict) else {}, ext)

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
                baas_service=self._baas_service,
                baas_device_service=self._baas_device_service,
                bot_repository=self._bot_repository,
                publish_repository=self._publish_repository,
                template_service=self._template_service,
            )
        )
