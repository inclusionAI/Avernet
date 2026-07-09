"""Community ``SandboxRuntimeClient`` — no ARCA sandbox runtime.

The open-source build has no ARCA platform; its container runtime is owned by the
BaaS team and reached through the BaaS device provider, not this client. So every
method fails loudly rather than pretending to run a sandbox. A community
deployment never routes arca-provider device ops here (the device router selects
by ``binding.device_provider``), so this is a guard, not a live path. Not a
``MockSeam`` — a real (fail-closed) impl bound by ``CommunitySandboxRuntimeModule``.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.plugin_api.sandbox_runtime import (
    SandboxRuntimeClient,
    SandboxRuntimeUnavailableError,
)


class CommunitySandboxClient(SandboxRuntimeClient):
    """No ARCA runtime in the community build — every op raises."""

    _MSG = (
        "ARCA sandbox runtime is not available in the community build; "
        "use a BaaS-provided device runtime instead."
    )

    def create_sandbox(self, **kwargs: Any):
        raise SandboxRuntimeUnavailableError(self._MSG)

    def destroy_sandbox(self, **kwargs: Any) -> bool:
        raise SandboxRuntimeUnavailableError(self._MSG)

    def get_sandbox_info(self, **kwargs: Any):
        raise SandboxRuntimeUnavailableError(self._MSG)

    def exec_command(self, **kwargs: Any):
        raise SandboxRuntimeUnavailableError(self._MSG)

    def update_outbound_rule(self, **kwargs: Any) -> bool:
        raise SandboxRuntimeUnavailableError(self._MSG)

    def build_proxy_connection(self, **kwargs: Any):
        raise SandboxRuntimeUnavailableError(self._MSG)

    def proxy_base_url(self) -> str:
        raise SandboxRuntimeUnavailableError(self._MSG)

    def proxy_target(self, sandbox_id: str, **kwargs: Any) -> str:
        raise SandboxRuntimeUnavailableError(self._MSG)

    def build_proxy_request(self, **kwargs: Any):
        raise SandboxRuntimeUnavailableError(self._MSG)

    async def read_file(self, **kwargs: Any):
        raise SandboxRuntimeUnavailableError(self._MSG)

    async def write_file(self, **kwargs: Any) -> None:
        raise SandboxRuntimeUnavailableError(self._MSG)

    async def list_dir(self, **kwargs: Any):
        raise SandboxRuntimeUnavailableError(self._MSG)

    async def delete_file(self, **kwargs: Any) -> bool:
        raise SandboxRuntimeUnavailableError(self._MSG)

    async def delete_tree(self, **kwargs: Any) -> bool:
        raise SandboxRuntimeUnavailableError(self._MSG)

    async def exists(self, **kwargs: Any) -> bool:
        raise SandboxRuntimeUnavailableError(self._MSG)
