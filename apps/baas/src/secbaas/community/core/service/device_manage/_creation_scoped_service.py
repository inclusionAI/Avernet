"""Attach trusted creation identity around the existing lifecycle service."""

from contextlib import AbstractContextManager
from uuid import uuid4

from secbaas.community.api.device_manage import DeviceResponse, DeviceStatus
from secbaas.community.api.device_manage.creation_context import (
    DeviceCreationContext,
    device_creation_scope,
)
from secbaas.community.core.utils.env_utils import get_current_env

from ._device_service import DefaultDeviceService as LifecycleDeviceService


class CreationScopedDeviceService(LifecycleDeviceService):
    """Keep caller metadata separate from the server's device operation scope.

    The underlying service retains lifecycle, persistence and failure behavior.
    Ambiguous historical records never grant automatic resource recovery.
    """

    def _creation_scope(
        self,
        tenant: str,
        device_uuid: str,
        publish_id: int | None,
        *,
        starting: bool,
    ) -> AbstractContextManager[None]:
        env = get_current_env()
        records = self._repository.list_by_device_uuid(device_uuid, tenant, env)
        eligible = [
            record
            for record in records
            if not record.is_deleted
            and record.tenant == tenant
            and record.env == env
            and record.device_uuid == device_uuid
            and record.status != DeviceStatus.RELEASED.value
            and (not starting or record.status == DeviceStatus.PENDING.value)
        ]
        if len(eligible) != 1:
            return device_creation_scope(None)
        record = eligible[0]
        operation_id = (
            f"publish:{publish_id}:device:{record.id}"
            if publish_id is not None
            else f"device:{record.id}:{uuid4().hex}"
        )
        return device_creation_scope(
            DeviceCreationContext(
                tenant=tenant,
                env=env,
                device_uuid=device_uuid,
                operation_id=operation_id,
                previous_provider_id=record.provider_device_id,
            )
        )

    async def start_device(
        self,
        tenant: str,
        device_uuid: str,
        modifier: str = "system",
        publish_id: int | None = None,
    ) -> DeviceResponse:
        with self._creation_scope(tenant, device_uuid, publish_id, starting=True):
            return await super().start_device(tenant, device_uuid, modifier, publish_id)

    async def update_device(
        self,
        tenant: str,
        device_uuid: str,
        modifier: str = "system",
        publish_id: int | None = None,
    ) -> DeviceResponse:
        with self._creation_scope(tenant, device_uuid, publish_id, starting=False):
            return await super().update_device(
                tenant, device_uuid, modifier, publish_id
            )
