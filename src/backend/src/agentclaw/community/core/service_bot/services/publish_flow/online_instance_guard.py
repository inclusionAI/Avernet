"""Guard against duplicate online instances in the restart flow.

When a restart targets a FAILED online bot, the recreate path mints a new
FIRST_RELEASE with a brand-new bot + binding + device. If the existing binding
is still in ACTIVE or FAILED state, re-creating would produce duplicate
``baas_device`` online rows. This module provides a read-only check that
raises before the duplicate is created."""
from __future__ import annotations

from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.log import get_logger

logger = get_logger()


class DuplicateOnlineInstanceError(Exception):
    """An online instance already occupies the slot for this publish+stage."""


# Binding statuses that indicate the slot is still occupied and a recreate
# must NOT proceed — doing so would leave a second online row in baas_device.
_OCCUPIED_STATUSES = frozenset({
    DeviceBindingStatus.ACTIVE,
    DeviceBindingStatus.FAILED,
})


def check_existing_online_instance(
    publish_service,
    publish_id: int,
    stage: PublishStage,
) -> None:
    """Check whether an occupied online instance exists for the given publish+stage.

    Resolves the publish record's ext, finds the binding for *stage*, and
    inspects the device binding's ``status``. If the status is ACTIVE or FAILED
    the slot is still occupied and a ``DuplicateOnlineInstanceError`` is raised.

    A RELEASED binding means the slot is free — restart is allowed.

    This function is read-only and side-effect-free (easy to unit test).

    At-least-once safety: the guard re-reads the current binding status on
    each call. If a FAILED binding transitions to RELEASED between the
    ``restart_bot`` guard check and the ``execute_restart`` guard check
    (e.g. during a durable task redelivery), the guard correctly passes
    because the slot is genuinely free. No duplicate is created.

    Args:
        publish_service: The publish service used to look up records.
        publish_id: The publish record ID.
        stage: The publish stage to check.

    Raises:
        DuplicateOnlineInstanceError: When the binding status is ACTIVE or FAILED,
            meaning an online instance still occupies the slot.
    """
    record = publish_service.get_publish_by_id(publish_id)
    if record is None:
        return

    ext = record.ext if isinstance(record.ext, dict) else (record.ext or {})
    binding_id = (ext.get("binding") or {}).get(stage.value)
    if not binding_id:
        return

    binding = publish_service.get_device_binding_by_id(binding_id)
    if binding is None:
        return

    if binding.status in _OCCUPIED_STATUSES:
        raise DuplicateOnlineInstanceError(
            f"An online instance already exists for publish {publish_id} "
            f"at stage {stage.value} (binding_id={binding_id}, "
            f"status={binding.status}). Please clean up the existing "
            f"instance before restarting."
        )