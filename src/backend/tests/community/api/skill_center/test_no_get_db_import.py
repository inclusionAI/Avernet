"""Verify api/skill_center/ routes no longer import get_db from compat."""
import inspect


class TestNoGetDbImport:
    """Route files should not import get_db from compat."""

    def test_skills_no_get_db(self):
        from agentclaw.community.adapters.http.skill_center import skills as mod
        source = inspect.getsource(mod)
        assert "get_db" not in source
        assert "Depends(get_db)" not in source

    def test_skillsets_no_get_db(self):
        from agentclaw.community.adapters.http.skill_center import skillsets as mod
        source = inspect.getsource(mod)
        assert "get_db" not in source
        assert "Depends(get_db)" not in source
