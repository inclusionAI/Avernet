"""Defer NAS retirement during a trusted replacement to conflict recovery."""

from secbaas.community.api.device_manage import ErrorCode, PaasError
from secbaas.community.api.device_manage.creation_context import (
    get_device_creation_context,
)
from secbaas.community.spi.sandbox.arca import (
    ArcaSandboxError,
    ArcaSandboxNotFoundError,
)

from ._arca_paas_service import ArcaPaasService as LifecycleArcaPaasService


class ArcaPaasService(LifecycleArcaPaasService):
    """Keep explicit deletion unchanged; a NAS replacement retires in create.

    A successful destroy response inside a trusted replacement acknowledges the
    deferred phase. The create provider must validate ownership, operation and
    binding before retiring that instance. No storage allocation is deleted.
    """

    async def destroy_device(self, paas_device_id: str) -> bool:
        try:
            return await super().destroy_device(paas_device_id)
        except PaasError:
            raise
        except Exception as error:
            raise self._translate_error(
                error, ErrorCode.DEVICE_DESTROY_FAILED
            ) from error

    def _destroy_device_sync(self, paas_device_id: str) -> bool:
        context = get_device_creation_context()
        if (
            context is None
            or getattr(
                self._arca_sandbox_plugin, "supports_nas_replacement_recovery", False
            )
            is not True
        ):
            return super()._destroy_device_sync(paas_device_id)
        previous = context.previous_provider_id
        if (
            context.tenant != self._credentials.tenant_name
            or not previous
            or previous.rsplit("@", 1)[0] != paas_device_id
        ):
            raise ArcaSandboxError(
                "Replacement identity does not match retirement target"
            )
        try:
            sandbox = self._arca_sandbox_plugin.connect_sync_sandbox(paas_device_id)
            info = sandbox.get_info()
        except ArcaSandboxNotFoundError:
            return True
        storage = info.storage
        storage_type = (
            storage.get("type")
            if isinstance(storage, dict)
            else getattr(storage, "type", None)
        )
        if isinstance(storage_type, str) and storage_type.lower() == "nas":
            return True
        if storage is None or (
            isinstance(storage_type, str) and storage_type.lower() != "nas"
        ):
            return super()._destroy_device_sync(paas_device_id)
        raise ArcaSandboxError("Cannot determine replacement storage type")
