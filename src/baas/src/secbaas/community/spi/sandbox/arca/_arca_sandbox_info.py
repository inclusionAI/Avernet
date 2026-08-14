"""Unified Arca sandbox info model.

Single, community-owned model for ``ArcaSandbox.get_info()`` return values,
derived from the Arca SDK ``SandboxInfo`` surface. All Arca sandbox
implementations (enterprise SDK-backed, community stub, community
local-process, community local-docker) return this model so the SPI has one
source of truth for the sandbox info shape.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from secbaas.community.api.device_manage import (
        OutBoundOperationRule,
        ResourceSpecification,
        Storage,
    )


class ArcaSandboxInfo:
    """Unified Arca sandbox information container.

    ``status`` may be an enum-like value exposing ``.value`` or a plain string;
    consumers normalize via
    ``str(status.value) if hasattr(status, "value") else str(status)``.

    Field ownership / optionality:

    ``sandbox_id`` (always set; owned by all backends)
        ``str``. Arca sandbox ID.

    ``status`` (always set; owned by all backends)
        ``Any`` enum-like (``.value``) or plain ``str``. Current sandbox status.

    ``template_id`` (optional; owned by all backends)
        ``str | None``. Platform template ID; ``None`` when the backend does not
        report a template.

    ``resources`` (optional; owned by all backends)
        ``ResourceSpecification | None``. CPU/memory/disk; ``None`` when unset.

    ``ttl_in_minutes`` (optional; owned by all backends)
        ``float | int | None``. Remaining/set TTL in minutes; ``None`` when unset.

    ``ttl_timestamp`` (optional; owned by all backends)
        ``int | None``. Expiry timestamp (ms); ``None`` when unavailable.

    ``envs`` (optional; owned by all backends)
        ``dict[str, Any] | None``. Environment variables; ``None`` when unset.

    ``snapshot_id`` (optional; owned by all backends)
        ``str | None``. Snapshot ID; ``None`` when unset.

    ``metadata`` (optional; owned by all backends)
        ``dict[str, Any] | None``. Arbitrary metadata; ``None`` when unset.

    ``outbound_operation_rule`` (optional; owned by all backends)
        ``OutBoundOperationRule | None``. Outbound rule; ``None`` when unset.

    ``storage`` (optional; owned by all backends)
        ``Storage | None``. NAS storage binding; ``None`` when unset.

    ``is_ready`` (optional; owned by local-process + local-docker)
        ``bool``. Ready flag; ``False`` on backends that do not model it.

    ``container_id`` (optional; owned by local-docker)
        ``str | None``. Docker container ID; ``None`` elsewhere.

    ``bot_id`` (optional; owned by local-process)
        ``str``. Bot ID; ``""`` on backends that do not model it.

    ``adapter_port`` (optional; owned by local-process)
        ``int``. Adapter port; ``0`` on backends that do not model it.

    ``adapter_pid`` (optional; owned by local-process)
        ``int | None``. Adapter PID; ``None`` when no adapter process.

    ``engine_port`` (optional; owned by local-process)
        ``int``. Engine port; ``0`` on backends that do not model it.

    ``engine_pid`` (optional; owned by local-process)
        ``int | None``. Engine PID; ``None`` when no engine process.

    ``config_dir`` (optional; owned by local-process)
        ``str``. Config directory; ``""`` on backends that do not model it.

    ``workspace_dir`` (optional; owned by local-process)
        ``str``. Workspace directory; ``""`` on backends that do not model it.

    NOTE: Core logic that consumes an optional field MUST treat it as
    ``None`` (or its unset default) — only the owning backend guarantees a
    value. Do not assume presence without checking.
    """

    def __init__(
        self,
        sandbox_id: str,
        status: Any = None,
        template_id: str | None = None,
        resources: ResourceSpecification | None = None,
        ttl_in_minutes: float | int | None = None,
        ttl_timestamp: int | None = None,
        envs: dict[str, Any] | None = None,
        snapshot_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        outbound_operation_rule: OutBoundOperationRule | None = None,
        storage: Storage | None = None,
        is_ready: bool = False,
        container_id: str | None = None,
        bot_id: str = "",
        adapter_port: int = 0,
        adapter_pid: int | None = None,
        engine_port: int = 0,
        engine_pid: int | None = None,
        config_dir: str = "",
        workspace_dir: str = "",
        **extra: Any,
    ) -> None:
        self.sandbox_id = sandbox_id
        self.status = status
        self.template_id = template_id
        self.resources = resources
        self.ttl_in_minutes = ttl_in_minutes
        self.ttl_timestamp = ttl_timestamp
        self.envs = envs
        self.snapshot_id = snapshot_id
        self.metadata = metadata
        self.outbound_operation_rule = outbound_operation_rule
        self.storage = storage
        self.is_ready = is_ready
        self.container_id = container_id
        self.bot_id = bot_id
        self.adapter_port = adapter_port
        self.adapter_pid = adapter_pid
        self.engine_port = engine_port
        self.engine_pid = engine_pid
        self.config_dir = config_dir
        self.workspace_dir = workspace_dir
        # Unknown/forward-compatible extras remain available as dynamic attributes.
        for name, value in extra.items():
            setattr(self, name, value)
