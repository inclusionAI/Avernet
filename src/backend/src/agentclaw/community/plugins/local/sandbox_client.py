"""Local ``SandboxRuntimeClient`` — no-op double for offline/test.

Returns benign neutral values so ``ArcaDeviceService`` orchestration can be
exercised without a real ARCA platform. Tests that assert on specific runtime
behavior inject their own mock; this is the default zero-config double.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.kernel.device_dto import (
    CommandResult,
    ProxyConnection,
    ProxyRequest,
    SandboxInfo,
)
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugin_api.sandbox_runtime import SandboxRuntimeClient
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(mode=Mode.LOCAL, flavor=Flavor.NOOP, rationale="no sandbox runtime offline")
class NoopSandboxClient(MockSeam, SandboxRuntimeClient):
    """Test/offline double: sandbox ops return benign neutral values."""

    def create_sandbox(self, **kwargs: Any) -> SandboxInfo:
        return SandboxInfo(sandbox_id="local-sandbox", status="RUNNING", ttl_in_minutes=60)

    def destroy_sandbox(self, **kwargs: Any) -> bool:
        return True

    def get_sandbox_info(self, *, sandbox_id: str, tenant_idx: int) -> SandboxInfo:
        return SandboxInfo(sandbox_id=sandbox_id, status="RUNNING", ttl_in_minutes=60)

    def exec_command(self, **kwargs: Any) -> CommandResult:
        return CommandResult(stdout="", stderr="", exit_code=0, status="completed")

    def update_outbound_rule(self, **kwargs: Any) -> bool:
        return True

    def build_proxy_connection(self, *, sandbox_id: str, ttl_seconds: int) -> ProxyConnection:
        return ProxyConnection(target=f"LOCAL_{sandbox_id}", token="")

    def proxy_base_url(self) -> str:
        return "http://noop-sandbox"

    def proxy_target(self, sandbox_id: str, *, port: int = 20003) -> str:
        return f"LOCAL_{sandbox_id}:{port}"

    def build_proxy_request(self, *, sandbox_id: str, api_path: str, port: int = 20003) -> ProxyRequest:
        return ProxyRequest(url=f"http://noop-sandbox:{port}{api_path}", headers={})

    async def read_file(self, *, sandbox_id: str, path: str) -> bytes | None:
        return b""

    async def write_file(self, *, sandbox_id: str, path: str, content: bytes) -> None:
        return None

    async def list_dir(self, *, sandbox_id: str, path: str, recursive: bool = False) -> list | None:
        return []

    async def delete_file(self, *, sandbox_id: str, path: str) -> bool:
        return True

    async def delete_tree(self, *, sandbox_id: str, path: str) -> bool:
        return True

    async def exists(self, *, sandbox_id: str, path: str) -> bool:
        return False
