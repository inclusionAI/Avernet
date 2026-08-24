"""DI bindings for the opt-in local Chat file-share adapter."""

from __future__ import annotations

from injector import Module, provider, singleton

from engine.community.config import load_chat_file_share_settings
from engine.community.core.chat_file_share.service import ChatFileShareService
from engine.community.plugin_api.workspace_root import workspace_root_strict
from engine.community.plugins.session_file_export import BaasSessionFileClient


class ChatFileShareModule(Module):
    """Compose a Chat-only use case from the existing Session File client."""

    @singleton
    @provider
    def service(self) -> ChatFileShareService:
        settings = load_chat_file_share_settings()
        workspace_root = workspace_root_strict()
        if settings is None or workspace_root is None:
            raise RuntimeError(
                "local file sharing requires Engine profile and workspace"
            )
        client = BaasSessionFileClient(
            baas_base_url=settings.baas_base_url,
            control_headers={},
            allowed_share_hosts=settings.allowed_share_hosts,
        )
        return ChatFileShareService(
            workspace_root=workspace_root,
            tenant=settings.tenant,
            client=client,
        )
