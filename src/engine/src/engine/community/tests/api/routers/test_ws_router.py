"""Unified /ws router: all three engines, neutral (no corp transport imports)."""
from engine.community.api.routers.ws import router


def test_ws_router_has_openclaw_endpoint_only():
    # Only openclaw /ws is mounted unconditionally (both profiles use the generic
    # EngineWebSocketServer). claude_code /ws is a separate shared router
    # (ClaudeCodeWsServer port); aicoding /ws is mounted uniformly and guarded by active engine.
    paths = {r.path for r in router.routes}
    assert paths == {"/api/openclaw/ws"}


def test_ws_router_does_not_import_engine_transport():
    """Neutral: must not import corp engine.{openclaw,aicoding,claude_code}.* transport."""
    import engine.community.api.routers.ws as mod
    src = open(mod.__file__).read()
    assert "engine.community.openclaw.client" not in src
    assert "engine.corp.transport.aicoding.server" not in src
    assert "engine.corp.transport.claude_code.server" not in src
