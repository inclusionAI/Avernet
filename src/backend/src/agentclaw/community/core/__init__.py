"""Backend core — business logic implementation layer.

Target per re-architecture proposal §5.1/5.2.

This package depends on ``agentclaw.community.api`` (contracts) and
``agentclaw.community.plugin_api`` (capability interfaces) and is the home for the
HTTP/WebSocket routers, service implementations, and dependency wiring.

Migration status: skeleton only. The authoritative code currently lives in
``agentclaw.services``, ``agentclaw.servers.web.routes``, and
``agentclaw.servers.web.dependencies``. See ``core/README.md``.
"""
