"""Engine-authoritative session-file operations, independent of upload records."""

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.runtime_binding.models import RuntimeBindingRequest
from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterStreamResponse


@runtime_checkable
class EngineSessionFilesProtocol(Protocol):
    """Call only after Bot and (when supplied) session-subject authorization.

    ``target.actor_user_id`` is the verified reader; ``session_user_id`` is an
    independently authorized friend-session subject, not a replacement actor.
    Without a subject, service Bots use the requested stage. With a subject,
    Caller engines use its independent instance when their strategy requires it.
    The legacy ``target.target`` selector is not used by file operations.
    """
    async def list_files(
        self, target: RuntimeBindingRequest, session_key: str,
        *, session_user_id: str | None = None,
        device_uuid: str | None = None,
    ) -> list[dict[str, Any]]: ...

    async def open_content(
        self, target: RuntimeBindingRequest, session_key: str,
        resource_id: str, disposition: str,
        *, session_user_id: str | None = None,
        device_uuid: str | None = None,
    ) -> DeviceAdapterStreamResponse: ...

    async def delete_file(
        self, target: RuntimeBindingRequest, session_key: str, resource_id: str,
        *, session_user_id: str | None = None,
        device_uuid: str | None = None,
    ) -> None: ...
