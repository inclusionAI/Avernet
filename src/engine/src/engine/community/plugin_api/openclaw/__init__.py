"""OpenClaw native port — the engine-owned ACL boundary.

`OpenClawPlugin` is the single aggregate Protocol the OpenClaw engine owns:
its native operation surface, expressed in native shapes (dicts + kernel frames,
never core DTOs). The concrete impl lives in `plugins/community/openclaw/`; the
`core/adapters/openclaw/` adapters translate the core `*Service` protocols
to/from this port. This package imports only `engine.community.kernel` (+ stdlib/typing).

See `specs/2026-05-31-engine-arch-f2-openclaw-acl/port-design-notes.md`.
"""
from engine.community.plugin_api.openclaw.gateway_service import OpenClawGatewayService
from engine.community.plugin_api.openclaw.models import EngineToken
from engine.community.plugin_api.openclaw.plugin import OpenClawPlugin

__all__ = ["EngineToken", "OpenClawGatewayService", "OpenClawPlugin"]
