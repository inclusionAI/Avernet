"""TestingAicodingModule — local-mode override for aicoding services.

Binds a stub ``WorkspaceHostingService`` so endpoint tests exercising the
DIMA workspace creation path don't hit the real DIMA OpenAPI.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from injector import Binder, Module, singleton

from agentclaw.community.core.bot_management.services.aicoding.workspace_hosting_service import (
    WorkspaceHostingService,
)


class _StubWorkspaceHostingService:
    """Always-succeed stub for endpoint tests.

    ``create_workspace_for_bot`` synthesises a deterministic workspace ID
    from the bot_id so tests can assert on the returned value.
    """

    def create_workspace_for_bot(
        self,
        staff_id: str,
        bot_id: str,
        bot_name: str,
        template_config: Optional[Dict[str, Any]] = None,
        raise_on_failure: bool = False,
    ) -> Optional[str]:
        workspace_id = f"W_STUB_{bot_id}"
        if template_config is not None:
            template_config["dima_space_id"] = workspace_id
        return workspace_id


class TestingAicodingModule(Module):
    """Bind stub DIMA services so endpoint tests stay offline."""

    def configure(self, binder: Binder) -> None:
        binder.bind(WorkspaceHostingService, to=_StubWorkspaceHostingService, scope=singleton)  # type: ignore[arg-type]
