"""Service API Protocol for the end-to-end publish workflow."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.kernel.device_dto import ResourceSpecification


@runtime_checkable
class PublishFlowServiceProtocol(Protocol):
    """Service API for orchestrating publish/restart flows + progress sync."""

    def process(self, *args: Any, **kwargs: Any) -> Any: ...

    def restart_bot(self, *args: Any, **kwargs: Any) -> Any: ...

    def scale_bot(self, *args: Any, **kwargs: Any) -> Any: ...

    def retry(self, *args: Any, **kwargs: Any) -> Any: ...

    def describe_publish(self, *args: Any, **kwargs: Any) -> Any: ...

    def sync_scale_progress(self, *args: Any, **kwargs: Any) -> Any: ...

    def sync_restart_progress(self, *args: Any, **kwargs: Any) -> Any: ...

    async def execute_rollback(self, *args: Any, **kwargs: Any) -> Any: ...

    async def execute_restore_draft(self, *args: Any, **kwargs: Any) -> Any: ...

    async def eval_publish(
        self,
        publish_id: int,
        operator: str,
        biz_id: str = "",
        default_tag: str = "",
        template_uuid: str = "",
        ttl_seconds: int | None = None,
        migration_path: str | None = None,
        config_artifact: str | None = None,
        docker_image: str | None = None,
        resource_spec: ResourceSpecification | None = None,
    ) -> dict[str, Any]:
        """Submit an eval deployment without advancing the main publication.

        Callers validate resource addressing and authorize the actor separately.
        Submission success is not readiness.
        resource_spec replaces template resource settings for this deployment only;
        None preserves existing defaults. Disk units/support are provider-defined.
        """
        ...

    def get_baas_publish_progress(
        self, *, baas_publish_id: int, include_devices: bool = True,
    ) -> dict[str, Any]:
        """Read BaaS workflow progress; callers authorize workflow access separately."""
        ...

    def eval_teardown(
        self,
        bot_uuid: str,
        *,
        operator: str = "system",
        publish_id: int = 0,
        default_tag: str = "",
    ) -> dict[str, Any]:
        """Enqueue durable eval teardown; acceptance does not imply completion."""
        ...
