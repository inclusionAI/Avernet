"""When is a bot actually ready to use?

``status == "ACTIVE"`` is necessary but not sufficient. An **application** bot
(``applicationCoding`` on the ``aicoding`` / ``claude_code`` engines) is only
usable once its ``.repos/`` checkout has finished cloning, which the runtime
reports as ``ext.start_status == "SUCCEEDED"``. Reporting such a bot as ready
while the clone is still pending — or has failed — lets a caller start using an
incomplete workspace.

The policy lives here, rather than in one router, because both the internal
``/api/bots`` surface and the public ``/openapi/v1/bots`` surface answer the same
question and must answer it identically.
"""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.bot_management.utils import (
    is_baas_publish_failure_message,
)

# Engines an ``applicationCoding`` bot can be created with. The create path routes
# ``claude_code`` + ``applicationCoding`` to the ``aicoding`` engine, but the bot
# row keeps whatever the caller passed — so both values have to be recognized.
_APPLICATION_ENGINES = ("aicoding", "claude_code")
_APPLICATION_TEMPLATE = "applicationCoding"


def has_stale_baas_publish_failure(bot: dict[str, Any]) -> bool:
    """True when a live baas bot still carries a superseded publish-failure marker.

    The bot is ACTIVE with an ACTIVE baas binding, yet ``ext`` retains a
    ``start_status == "FAILED"`` from an earlier publish. That marker is stale, so
    it must not hold the bot back from being reported ready.
    """
    binding = bot.get("device_binding") or {}
    ext = bot.get("ext") or {}
    is_active_baas_binding = (
        bot.get("status") == "ACTIVE"
        and binding.get("status") == "ACTIVE"
        and binding.get("device_provider") == "baas"
    )
    return (
        is_active_baas_binding
        and ext.get("start_status") == "FAILED"
        and is_baas_publish_failure_message(ext.get("start_message"))
    )


def is_bot_ready(bot: dict[str, Any]) -> bool:
    """Whether ``bot`` (a ``get_bot`` record) is ready to serve requests.

    Expects the detailed record — the one carrying ``device_binding`` and ``ext``
    — not a bare ``to_dict()`` row.
    """
    if bot.get("status") != "ACTIVE":
        return False

    ext = bot.get("ext") or {}
    needs_repos = (
        bot.get("template_type") == _APPLICATION_TEMPLATE
        and bot.get("active_engine") in _APPLICATION_ENGINES
    )
    # Non-application bots ignore start_status entirely (preserves prior behavior);
    # application bots must have finished cloning.
    return (
        not needs_repos
        or ext.get("start_status") == "SUCCEEDED"
        or has_stale_baas_publish_failure(bot)
    )
