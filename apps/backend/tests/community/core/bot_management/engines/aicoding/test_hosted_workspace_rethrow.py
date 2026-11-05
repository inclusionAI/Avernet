"""A deliberate template-write rejection must not be swallowed by the hosted
workspace persistence fallback: ``ensure_hosted_workspace`` passes
``TemplateWriteInvalidInput`` through to its caller, and keeps the legacy
best-effort behavior for every unrelated failure (workspace id still
returned so callers can retry persistence).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from agentclaw.community.core.bot_management.engines.aicoding.hosted_workspace_mixin import (  # noqa: E501
    AicodingHostedWorkspaceMixin,
)
from agentclaw.community.plugin_api.bot_template_write_extension import (
    TemplateWriteInvalidInput,
)


class _Context:
    def __init__(self, bot_id="wb1"):
        self.bot_id = bot_id
        self.owner_id = "staff1"
        self.template_type = "applicationCoding"
        self.template_config = {"dima_space_id": None, "template_key": "app_t"}
        self.active_engine = "aicoding"


class _Host(AicodingHostedWorkspaceMixin):
    """Minimal carriage: only the mixin methods under test."""


def _fixture(template_error):
    host = _Host()
    ws_service = MagicMock()
    ws_service.create_workspace_for_bot.return_value = "ws-1"
    provider = MagicMock(return_value=ws_service)
    template_service = MagicMock()
    template_service.create_or_update_template.side_effect = template_error
    return _Context(), host, provider, template_service


def test_deliberate_hook_rejection_is_not_swallowed_by_persistence_fallback():
    ctx, host, provider, template_service = _fixture(
        TemplateWriteInvalidInput("invalid template snapshot")
    )
    with pytest.raises(TemplateWriteInvalidInput, match="invalid template snapshot"):
        host.ensure_hosted_workspace(
            ctx,
            bot_name="TestBot",
            template_config=ctx.template_config,
            template_service=template_service,
            workspace_hosting_provider=provider,
        )


def test_unrelated_persistence_failure_is_still_legacy_best_effort():
    ctx, host, provider, template_service = _fixture(RuntimeError("database down"))
    result = host.ensure_hosted_workspace(
        ctx,
        bot_name="TestBot",
        template_config=ctx.template_config,
        template_service=template_service,
        workspace_hosting_provider=provider,
    )
    assert result == "ws-1"
