"""Desktop BaaS publish polling, Pool confirmation, and terminal fencing."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.devices.protocols import (
    LAYOUT_CONFIRMED_STARTUP_IDENTITY_KEY,
)
from agentclaw.community.core.devices.services.baas_container_init import (
    BaasContainerInitializer,
)
from agentclaw.community.core.events.bus import RequiredEventDeliveryError, get_event_bus
from agentclaw.community.core.events.types import BaasPublishCompletedEvent
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.devices import (
    DeviceBindingRepository,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.log import get_logger


logger = get_logger()

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_service import DeviceService


class DesktopLayoutConfirmationStatus(StrEnum):
    CONFIRMED = "confirmed"
    PENDING = "pending"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class DesktopTerminalTransitionStatus(StrEnum):
    COMMITTED = "committed"
    RETRY = "retry"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class DesktopPublishLifecycleHooks:
    query_publish_status: Callable[[str], str]
    confirm_layout_initialization: Callable[..., tuple[DesktopLayoutConfirmationStatus, bool]]
    transition_restart_terminal: Callable[..., DesktopTerminalTransitionStatus]
    trigger_pool_data_init: Callable[..., None]
    request_runtime_projection: Callable[..., None]
    trigger_device_alive: Callable[[str], bool]
    update_local_status: Callable[[str, str, str, str], None]


class DesktopPublishLifecycle:
    """Own the asynchronous Desktop publish state machine behind one poll seam."""

    def __init__(
        self,
        *,
        baas: BaasService,
        binding_repository: DeviceBindingRepository,
        bot_repository: BotRepository,
        device_service: "DeviceService",
        hooks: DesktopPublishLifecycleHooks,
    ) -> None:
        self._baas = baas
        self._binding_repo = binding_repository
        self._bot_repo = bot_repository
        self._device_service = device_service
        self._hooks = hooks

    def poll_publish_progress(
        self,
        *,
        publish_id: str,
        binding_id: str,
        bot_id: str,
        owner_id: str,
        device_id: str,
        engine_type: str,
        poll_interval_seconds: float,
        default_timeout_seconds: float,
        extended_timeout_seconds: float,
        restart_tracking: (
            tuple[dict[str, Any], dict[str, Any], str | None] | None
        ) = None,
        observe_only: bool = False,
        restart_publish_id: str | None = None,
    ) -> None:
        poll_timeout = (
            extended_timeout_seconds
            if engine_type and engine_type != DEFAULT_ENGINE_TYPE
            else default_timeout_seconds
        )
        start_time = time.monotonic()
        final_status = "FAILED"
        layout_watchdog_dispatched = False
        terminal_persistence_pending = False
        restart_completion_pending: BaasPublishCompletedEvent | None = None

        logger.info(
            "[DesktopPublishLifecycle] start polling publish_id=%s bot_id=%s "
            "device_id=%s engine_type=%s timeout=%ds",
            publish_id,
            bot_id,
            device_id,
            engine_type,
            poll_timeout,
        )

        try:
            while (time.monotonic() - start_time) < poll_timeout:
                time.sleep(poll_interval_seconds)

                if restart_completion_pending is not None:
                    try:
                        get_event_bus().publish(restart_completion_pending)
                    except RequiredEventDeliveryError:
                        logger.exception(
                            "[DesktopPublishLifecycle] required restart completion "
                            "hand-off failed; will retry: bot_id=%s publish_id=%s",
                            bot_id,
                            publish_id,
                        )
                        continue
                    return

                if restart_tracking is not None:
                    bot_ext_patch, binding_props_patch, expected_publish_id = (
                        restart_tracking
                    )
                    try:
                        prepared = self._binding_repo.prepare_baas_desktop_restart(
                            binding_id=int(binding_id),
                            bot_id=bot_id,
                            owner_id=owner_id,
                            expected_publish_id=expected_publish_id,
                            bot_ext_patch=bot_ext_patch,
                            binding_props_patch=binding_props_patch,
                        )
                    except Exception:
                        logger.exception(
                            "[DesktopPublishLifecycle] restart tracking persistence "
                            "retry failed: bot_id=%s publish_id=%s",
                            bot_id,
                            publish_id,
                        )
                        continue
                    restart_tracking = None
                    if not prepared:
                        current = self._binding_repo.get_by_id(int(binding_id))
                        observe_only = not self.restart_identity_matches(
                            current,
                            publish_id,
                        )

                try:
                    status = self._hooks.query_publish_status(publish_id)
                except Exception as error:
                    logger.warning(
                        "[DesktopPublishLifecycle] query failed (will retry): %s",
                        error,
                    )
                    continue

                if status == "SUCCESS":
                    if observe_only:
                        logger.info(
                            "[DesktopPublishLifecycle] observe-only restart "
                            "succeeded: bot_id=%s publish_id=%s",
                            bot_id,
                            publish_id,
                        )
                        return
                    try:
                        layout_status, watchdog_dispatched = (
                            self._hooks.confirm_layout_initialization(
                                publish_id=publish_id,
                                binding_id=binding_id,
                                bot_id=bot_id,
                                owner_id=owner_id,
                                device_id=device_id,
                                dispatch_watchdog=not layout_watchdog_dispatched,
                            )
                        )
                    except Exception:
                        logger.exception(
                            "[DesktopPublishLifecycle] layout confirmation failed "
                            "transiently; will retry: bot_id=%s publish_id=%s",
                            bot_id,
                            publish_id,
                        )
                        continue
                    layout_watchdog_dispatched |= watchdog_dispatched
                    if layout_status == DesktopLayoutConfirmationStatus.PENDING:
                        continue
                    if layout_status == DesktopLayoutConfirmationStatus.SUPERSEDED:
                        logger.info(
                            "[DesktopPublishLifecycle] publish superseded; stopping "
                            "without writes: bot_id=%s publish_id=%s",
                            bot_id,
                            publish_id,
                        )
                        return
                    if layout_status == DesktopLayoutConfirmationStatus.FAILED:
                        if restart_publish_id is not None:
                            transition = self._hooks.transition_restart_terminal(
                                binding_id=binding_id,
                                bot_id=bot_id,
                                owner_id=owner_id,
                                publish_id=restart_publish_id,
                                status="FAILED",
                            )
                            if transition in {
                                DesktopTerminalTransitionStatus.COMMITTED,
                                DesktopTerminalTransitionStatus.SUPERSEDED,
                            }:
                                return
                            terminal_persistence_pending = True
                            continue
                        final_status = "FAILED"
                        break
                    if restart_publish_id is not None:
                        transition = self._hooks.transition_restart_terminal(
                            binding_id=binding_id,
                            bot_id=bot_id,
                            owner_id=owner_id,
                            publish_id=restart_publish_id,
                            status="ACTIVE",
                        )
                        if transition is DesktopTerminalTransitionStatus.COMMITTED:
                            self._hooks.trigger_pool_data_init(
                                bot_id=bot_id,
                                owner_id=owner_id,
                                device_id=device_id,
                                binding_id=binding_id,
                            )
                            self._hooks.request_runtime_projection(
                                bot_id=bot_id,
                                owner_id=owner_id,
                                binding_id=binding_id,
                            )
                            restart_completion_pending = BaasPublishCompletedEvent(
                                binding_id=int(binding_id),
                                bot_id=bot_id,
                                owner_id=owner_id,
                                publish_id=int(restart_publish_id),
                                publish_kind="restart",
                            )
                            try:
                                get_event_bus().publish(restart_completion_pending)
                            except RequiredEventDeliveryError:
                                logger.exception(
                                    "[DesktopPublishLifecycle] required restart "
                                    "completion hand-off failed; will retry: "
                                    "bot_id=%s publish_id=%s",
                                    bot_id,
                                    publish_id,
                                )
                                continue
                            return
                        if transition is DesktopTerminalTransitionStatus.SUPERSEDED:
                            return
                        terminal_persistence_pending = True
                        continue
                    final_status = "ACTIVE"
                    if not self._hooks.trigger_device_alive(device_id):
                        logger.warning(
                            "[DesktopPublishLifecycle] device-alive trigger failed; "
                            "falling back to local ACTIVE: bot_id=%s device_id=%s",
                            bot_id,
                            device_id,
                        )
                        final_status = "ACTIVE_FALLBACK"
                    break

                if status == "FAILED":
                    if restart_publish_id is not None:
                        transition = self._hooks.transition_restart_terminal(
                            binding_id=binding_id,
                            bot_id=bot_id,
                            owner_id=owner_id,
                            publish_id=restart_publish_id,
                            status="FAILED",
                        )
                        if transition is DesktopTerminalTransitionStatus.COMMITTED:
                            return
                        if transition is DesktopTerminalTransitionStatus.SUPERSEDED:
                            logger.info(
                                "[DesktopPublishLifecycle] failed publish superseded; "
                                "stopping: bot_id=%s publish_id=%s",
                                bot_id,
                                publish_id,
                            )
                            return
                        terminal_persistence_pending = True
                        continue
                    if observe_only:
                        logger.warning(
                            "[DesktopPublishLifecycle] observe-only restart failed: "
                            "bot_id=%s publish_id=%s",
                            bot_id,
                            publish_id,
                        )
                        return
                    final_status = "FAILED"
                    break

            logger.info(
                "[DesktopPublishLifecycle] done publish_id=%s final_status=%s "
                "elapsed=%.1fs",
                publish_id,
                final_status,
                time.monotonic() - start_time,
            )

            if restart_tracking is not None or observe_only:
                logger.warning(
                    "[DesktopPublishLifecycle] stopped without local writes: "
                    "bot_id=%s publish_id=%s tracking_pending=%s observe_only=%s",
                    bot_id,
                    publish_id,
                    restart_tracking is not None,
                    observe_only,
                )
                return
            if restart_completion_pending is not None:
                logger.error(
                    "[DesktopPublishLifecycle] restart completion hand-off could "
                    "not be delivered before timeout: bot_id=%s publish_id=%s",
                    bot_id,
                    publish_id,
                )
                return
            if terminal_persistence_pending:
                logger.error(
                    "[DesktopPublishLifecycle] terminal restart status could not "
                    "be persisted before timeout: bot_id=%s publish_id=%s",
                    bot_id,
                    publish_id,
                )
                return

        except Exception as error:
            logger.error(
                "[DesktopPublishLifecycle] unexpected error: %s",
                error,
            )
            if restart_publish_id is not None:
                logger.error(
                    "[DesktopPublishLifecycle] guarded restart poll aborted without "
                    "lifecycle writes: bot_id=%s publish_id=%s",
                    bot_id,
                    publish_id,
                )
                return
            final_status = "FAILED"

        elapsed = time.monotonic() - start_time
        timed_out = final_status == "FAILED" and elapsed >= poll_timeout
        if timed_out:
            logger.info(
                "[DesktopPublishLifecycle] poll timed out; delegating to periodic "
                "scan: publish_id=%s bot_id=%s",
                publish_id,
                bot_id,
            )
            final_status = "PENDING_DOWNLOADING"

        if final_status not in {"ACTIVE", "PENDING_DOWNLOADING"}:
            effective_status = (
                "ACTIVE" if final_status == "ACTIVE_FALLBACK" else final_status
            )
            self._hooks.update_local_status(
                binding_id,
                bot_id,
                owner_id,
                effective_status,
            )

        if final_status in {"ACTIVE", "ACTIVE_FALLBACK"}:
            start_status = "SUCCEEDED"
        elif final_status == "PENDING_DOWNLOADING":
            start_status = "DOWNLOADING"
        else:
            start_status = "FAILED"

        try:
            bot = self._bot_repo.get_by_id_and_owner(
                bot_id=bot_id,
                owner_id=owner_id,
            )
            if bot:
                current_ext = bot.get("ext") or {}
                current_ext["start_status"] = start_status
                if start_status == "DOWNLOADING":
                    current_ext["start_message"] = "镜像下载中，请耐心等待..."
                self._bot_repo.update_by_owner(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    update_data={"ext": current_ext},
                )
        except Exception as error:
            logger.warning(
                "[DesktopPublishLifecycle] ext update failed: bot_id=%s error=%s",
                bot_id,
                error,
            )
        if final_status in {"ACTIVE", "ACTIVE_FALLBACK"}:
            self._hooks.trigger_pool_data_init(
                bot_id=bot_id,
                owner_id=owner_id,
                device_id=device_id,
                binding_id=binding_id,
            )

    @staticmethod
    def restart_identity_matches(binding: Any, publish_id: object) -> bool:
        if binding is None or binding.status not in {
            DeviceBindingStatus.PENDING.value,
            DeviceBindingStatus.ACTIVE.value,
        }:
            return False
        props = binding.device_props or {}
        if not isinstance(props, dict):
            return False
        current_publish_id = props.get("restart_publish_id") or props.get(
            "publish_id"
        )
        return current_publish_id is not None and str(current_publish_id) == str(
            publish_id
        )

    def confirm_layout_initialization(
        self,
        *,
        publish_id: str,
        binding_id: str,
        bot_id: str,
        owner_id: str,
        device_id: str,
        dispatch_watchdog: bool,
    ) -> tuple[DesktopLayoutConfirmationStatus, bool]:
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            return DesktopLayoutConfirmationStatus.PENDING, False
        ext = bot.get("ext") or {}
        if not isinstance(ext, dict) or ext.get("skills_layout") != "pool":
            return DesktopLayoutConfirmationStatus.CONFIRMED, False

        try:
            binding = self._binding_repo.get_by_id(int(binding_id))
        except Exception:
            logger.exception(
                "[DesktopPublishLifecycle] layout confirmation binding lookup "
                "failed: bot_id=%s publish_id=%s",
                bot_id,
                publish_id,
            )
            return DesktopLayoutConfirmationStatus.PENDING, False
        if binding is None:
            return DesktopLayoutConfirmationStatus.FAILED, False
        if binding.status in {
            DeviceBindingStatus.RELEASED.value,
            DeviceBindingStatus.STOPPED.value,
        }:
            return DesktopLayoutConfirmationStatus.SUPERSEDED, False

        props = binding.device_props or {}
        if not isinstance(props, dict):
            return DesktopLayoutConfirmationStatus.FAILED, False
        current_publish_id = props.get("restart_publish_id") or props.get(
            "publish_id"
        )
        if current_publish_id is None:
            return DesktopLayoutConfirmationStatus.FAILED, False
        if str(current_publish_id) != str(publish_id):
            return DesktopLayoutConfirmationStatus.SUPERSEDED, False
        if binding.status == DeviceBindingStatus.FAILED.value:
            return DesktopLayoutConfirmationStatus.FAILED, False
        if str(props.get(LAYOUT_CONFIRMED_STARTUP_IDENTITY_KEY) or "") == str(
            publish_id
        ):
            return DesktopLayoutConfirmationStatus.CONFIRMED, False

        callback_token = props.get("callback_token")
        if not isinstance(callback_token, str) or not callback_token:
            return DesktopLayoutConfirmationStatus.FAILED, False
        if dispatch_watchdog:
            try:
                BaasContainerInitializer(self._baas).dispatch_watchdog(
                    bot_uuid=device_id,
                    client_id=device_id,
                    token=callback_token,
                    startup_identity=str(publish_id),
                )
            except Exception:
                logger.exception(
                    "[DesktopPublishLifecycle] layout watchdog dispatch failed: "
                    "bot_id=%s publish_id=%s",
                    bot_id,
                    publish_id,
                )
                return DesktopLayoutConfirmationStatus.PENDING, False
            return DesktopLayoutConfirmationStatus.PENDING, True
        return DesktopLayoutConfirmationStatus.PENDING, False

    def transition_restart_terminal_if_current(
        self,
        *,
        binding_id: str,
        bot_id: str,
        owner_id: str,
        publish_id: str,
        status: str,
    ) -> DesktopTerminalTransitionStatus:
        if status not in {"ACTIVE", "FAILED"}:
            raise ValueError(f"unsupported Desktop restart status: {status}")
        try:
            bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        except Exception:
            logger.exception(
                "[DesktopPublishLifecycle] restart terminal Bot lookup failed: "
                "bot_id=%s publish_id=%s status=%s",
                bot_id,
                publish_id,
                status,
            )
            return DesktopTerminalTransitionStatus.RETRY
        if bot is None:
            return DesktopTerminalTransitionStatus.RETRY
        raw_ext = bot.get("ext")
        if raw_ext is not None and not isinstance(raw_ext, dict):
            return DesktopTerminalTransitionStatus.RETRY
        terminal_ext = dict(raw_ext) if isinstance(raw_ext, dict) else {}
        terminal_ext["start_status"] = (
            "SUCCEEDED" if status == "ACTIVE" else "FAILED"
        )
        try:
            committed = self._binding_repo.transition_baas_restart_terminal(
                binding_id=int(binding_id),
                bot_id=bot_id,
                owner_id=owner_id,
                publish_id=publish_id,
                request_id=None,
                status=status,
                expected_bot_ext=raw_ext,
                bot_ext=terminal_ext,
            )
        except Exception:
            logger.exception(
                "[DesktopPublishLifecycle] guarded restart terminal transition "
                "failed: bot_id=%s publish_id=%s status=%s",
                bot_id,
                publish_id,
                status,
            )
            return DesktopTerminalTransitionStatus.RETRY
        if committed:
            return DesktopTerminalTransitionStatus.COMMITTED
        try:
            binding = self._binding_repo.get_by_id(int(binding_id))
        except Exception:
            logger.exception(
                "[DesktopPublishLifecycle] restart identity recheck failed: "
                "bot_id=%s publish_id=%s",
                bot_id,
                publish_id,
            )
            return DesktopTerminalTransitionStatus.RETRY
        if self.restart_identity_matches(binding, publish_id):
            return DesktopTerminalTransitionStatus.RETRY
        return DesktopTerminalTransitionStatus.SUPERSEDED

    def trigger_pool_data_init_after_activation(
        self,
        *,
        bot_id: str,
        owner_id: str,
        binding_id: int | str,
        device_id: str,
    ) -> None:
        try:
            bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
            if (
                bot is None
                or bot.get("status") != DeviceBindingStatus.ACTIVE.value
                or str(bot.get("binding_id")) != str(binding_id)
            ):
                return
            ext = bot.get("ext") or {}
            if not isinstance(ext, dict) or ext.get("skills_layout") != "pool":
                return
            self._device_service.trigger_data_init_on_device_ready(
                device_id=device_id,
                binding_id=int(binding_id),
                require_pool_confirmation=True,
            )
        except Exception:
            logger.exception(
                "[DesktopPublishLifecycle] Pool data-init readiness trigger failed: "
                "bot_id=%s binding_id=%s device_id=%s",
                bot_id,
                binding_id,
                device_id,
            )
