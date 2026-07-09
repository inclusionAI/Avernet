"""openclaw HTTP router: neutral, deps via Injected, correct route shapes."""
from engine.community.api.routers.openclaw_http import router


def test_openclaw_http_router_has_three_endpoints():
    paths = {r.path for r in router.routes}
    assert paths == {"/api/openclaw/test-connection", "/api/openclaw/disconnect", "/api/openclaw/config"}


def test_openclaw_http_router_does_not_import_corp_transport():
    """Neutral: must not import engine.community.openclaw.client.* (corp transport)."""
    import engine.community.api.routers.openclaw_http as mod
    src = open(mod.__file__).read()
    assert "engine.community.openclaw.client" not in src
    assert "engine.community.openclaw.config" not in src
