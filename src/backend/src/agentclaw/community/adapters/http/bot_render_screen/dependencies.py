"""Bot Render Screen — FastAPI dependency entry-points.

The repository + service are bound by ``BotManagementModule``. This
module exposes the FastAPI-friendly callable that routes hook into
via ``Depends(get_render_screen_service)`` — tests can swap the
implementation through ``app.dependency_overrides``.

Lives under ``api/`` per Rule 7 (Core Independence): ``core/`` must
not import from ``fastapi_injector``.
"""
from __future__ import annotations

from fastapi_injector import Injected

from agentclaw.community.api.render_screen_service import RenderScreenServiceProtocol


def get_render_screen_service(
    service: RenderScreenServiceProtocol = Injected(RenderScreenServiceProtocol),
) -> RenderScreenServiceProtocol:
    """FastAPI Depends entry — returns the injected ``RenderScreenServiceProtocol``."""
    return service
