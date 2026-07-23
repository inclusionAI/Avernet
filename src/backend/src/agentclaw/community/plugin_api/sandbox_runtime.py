"""SandboxRuntimeClient — the vendor sandbox-runtime I/O seam.

The device orchestration (``core/devices/services``) decides *what* to do (which
template, tenant_idx (0 main / 1 alt / 2 aicoding), mounts, the start sequence) in
vendor-free code; the actual runtime calls — create / destroy / info / exec /
outbound-rule / proxy-connection
— go through this Protocol. The prod impl (``plugins/prod`` ``ArcaSandboxClient``)
holds the ARCA SDK; community has no such runtime (it raises); test is a no-op.

All inputs/outputs are neutral: core models (``NasMappingInfo``) and kernel DTOs
(``OutBoundOperationRule`` / ``ResourceSpecification`` / ``CommandResult`` /
``SandboxInfo`` / ``ProxyConnection``). The impl converts to/from the SDK's own
value objects at its boundary.

Implementations:
- ``plugins.prod.sandbox_client.ArcaSandboxClient`` (ARCA SDK)
- ``plugins.community.sandbox_client.CommunitySandboxClient`` (raises — no runtime)
- ``plugins.local.sandbox_client.NoopSandboxClient`` (test double)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from agentclaw.community.plugin_api.base import Plugin

if TYPE_CHECKING:
    from agentclaw.community.kernel.device_dto import (
        CommandResult,
        OutBoundOperationRule,
        ProxyConnection,
        ProxyRequest,
        SandboxInfo,
    )

# Default in-sandbox service ports reached through the proxy.
BOLT_PORT = 20003
RELAY_PORT = 18900


class SandboxRuntimeUnavailableError(Exception):
    """Raised by impls for runtimes that are not available in this deployment."""


class SandboxRuntimeClient(Plugin, Protocol):
    """Create and operate bot sandboxes on a container runtime."""

    def create_sandbox(
        self,
        *,
        template_id: str,
        ttl_minutes: int,
        envs: dict[str, str],
        outbound_rule: "OutBoundOperationRule",
        tenant_idx: int,
        mounts: "list[Any] | None" = None,
        oss_mount_id: str | None = None,
        nas_storage_id: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> "SandboxInfo":
        """Create a sandbox and return its neutral info.

        ``mounts`` items are ``NasMappingInfo``-shaped (``.nas_remote`` /
        ``.nas_local`` / ``.nas_permission``); typed loosely here so this Protocol
        stays free of any ``core`` import (the prod impl pins the precise type).

        Exactly one storage form is given: ``mounts`` (+ ``oss_mount_id``) for the
        OSS-mount flow, or ``nas_storage_id`` for the NAS flow. ``overrides`` is
        the neutral template-override dict (``image`` / ``command`` /
        ``resource_spec`` as a kernel ``ResourceSpecification``); the impl converts
        ``resource_spec`` to the SDK type at its boundary.
        """
        ...

    def destroy_sandbox(self, *, sandbox_id: str, tenant_idx: int) -> bool:
        """Destroy ``sandbox_id`` (raw id, no ``@alt`` suffix). Idempotent."""
        ...

    def get_sandbox_info(self, *, sandbox_id: str, tenant_idx: int) -> "SandboxInfo":
        """Return current status/ttl for ``sandbox_id``."""
        ...

    def exec_command(
        self, *, sandbox_id: str, cmd: str, tenant_idx: int, timeout_ms: int = 10_000
    ) -> "CommandResult":
        """Run ``cmd`` inside ``sandbox_id`` and return the neutral result."""
        ...

    def update_outbound_rule(
        self,
        *,
        sandbox_id: str,
        rule: "OutBoundOperationRule",
        tenant_idx: int,
        mode: str = "replace",
    ) -> bool:
        """Update the live outbound-header rule on ``sandbox_id``.

        ``mode`` is ``replace`` by default. Provider-aware runtime overlays may
        request ``append`` so a Caller rule does not erase existing managed
        headers.
        """
        ...

    def build_proxy_connection(
        self, *, sandbox_id: str, ttl_seconds: int
    ) -> "ProxyConnection":
        """Build the proxy target + signed proxy-pass token for ``sandbox_id``
        (the ``@alt``-suffixed id, used verbatim in the target)."""
        ...

    def proxy_base_url(self) -> str:
        """The proxy gateway base URL (no routing/path) for this runtime/env."""
        ...

    def proxy_target(self, sandbox_id: str, *, port: int = BOLT_PORT) -> str:
        """The routing target for ``sandbox_id`` (``@alt``-suffixed) on ``port`` —
        used to build a ``{base}/proxypass/{target}{path}`` URL by hand when the
        caller already holds a signed token (e.g. ``get_device_connection_v2``)."""
        ...

    def build_proxy_request(
        self, *, sandbox_id: str, api_path: str, port: int = BOLT_PORT
    ) -> "ProxyRequest":
        """Build a proxied request (full URL + signed proxy-pass headers) to the
        in-sandbox service on ``port`` at ``api_path`` for ``sandbox_id`` (the
        ``@alt``-suffixed id). ``port`` selects the in-sandbox service
        (``BOLT_PORT`` for the engine adapter, ``RELAY_PORT`` for the relay)."""
        ...

    # -- file I/O --------------------------------------------------------------
    # ``path`` is already mapped by the caller (no ``path_mapper`` here). Return
    # types are neutral; the impl talks to the runtime's file API at its boundary.

    async def read_file(self, *, sandbox_id: str, path: str) -> bytes | None:
        """Read ``path`` in ``sandbox_id``; ``None`` if missing / unreadable."""
        ...

    async def write_file(self, *, sandbox_id: str, path: str, content: bytes) -> None:
        """Write ``content`` to ``path`` in ``sandbox_id`` (creating/replacing)."""
        ...

    async def list_dir(
        self, *, sandbox_id: str, path: str, recursive: bool = False
    ) -> "list[dict[str, Any]] | None":
        """List ``path`` in ``sandbox_id`` (entry dicts); ``None`` if unreadable."""
        ...

    async def delete_file(self, *, sandbox_id: str, path: str) -> bool:
        """Delete the single file/dir at ``path`` in ``sandbox_id``."""
        ...

    async def delete_tree(self, *, sandbox_id: str, path: str) -> bool:
        """Recursively delete the directory ``path`` in ``sandbox_id``."""
        ...

    async def exists(self, *, sandbox_id: str, path: str) -> bool:
        """Whether ``path`` exists in ``sandbox_id``."""
        ...
