"""Smoke tests — verify all skill center routers import and register routes."""


class TestRouterImports:
    """Ensure routers load without import errors."""

    def test_skills_router(self):
        from agentclaw.community.adapters.http.skill_center.skills import router
        assert len(router.routes) > 0

    def test_skillsets_router(self):
        from agentclaw.community.adapters.http.skill_center.skillsets import router
        assert len(router.routes) > 0

    def test_skill_auth_router(self):
        from agentclaw.community.adapters.http.skill_center.skill_auth import router
        assert len(router.routes) > 0

    def test_skill_scan_router(self):
        from agentclaw.community.adapters.http.skill_center.skill_scan import router
        assert len(router.routes) > 0

    def test_route_counts(self):
        from agentclaw.community.adapters.http.skill_center.skills import router as r1
        from agentclaw.community.adapters.http.skill_center.skillsets import router as r2
        from agentclaw.community.adapters.http.skill_center.skill_auth import router as r3
        from agentclaw.community.adapters.http.skill_center.skill_scan import router as r4

        # Sanity check — routers have expected number of routes
        assert len(r1.routes) >= 10, f"skills: expected >=10, got {len(r1.routes)}"
        assert len(r2.routes) >= 10, f"skillsets: expected >=10, got {len(r2.routes)}"
        assert len(r3.routes) >= 3, f"skill_auth: expected >=3, got {len(r3.routes)}"
        assert len(r4.routes) >= 2, f"skill_scan: expected >=2, got {len(r4.routes)}"
