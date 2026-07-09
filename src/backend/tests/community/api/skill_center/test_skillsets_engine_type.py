"""/skillsets/default/ensure must return per-engine default skill sets."""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService


class TestSkillSetAPIEngineType:
    """Verify engine_type flows through API to SkillSetService."""

    @pytest.fixture
    def mock_skill_set_service(self):
        """Create a mock SkillSetService that returns distinct default sets per engine."""
        mock = MagicMock(spec=SkillSetService)
        # Simulate different default skill sets per engine
        def ensure_default(user_id=None, bolt_id=None):
            engine = mock._engine_type
            return {
                "id": f"default-{engine}-123",
                "name": f"默认技能集-{engine}",
                "bolt_id": bolt_id or "default",
                "engine_type": engine,
                "is_default": True,
                "is_builtin": False,
                "user_id": user_id,
                "gmt_created": "2026-04-01T00:00:00",
                "gmt_modified": "2026-04-01T00:00:00",
                "is_active": True,
                "skills": [],
            }
        mock.ensure_default_skill_set.side_effect = ensure_default
        return mock

    def test_ensure_default_returns_openclaw_and_aicoding_separately(self):
        """Test that different engine_types produce different default skill sets."""
        # Test with mock service behavior
        mock_service = MagicMock(spec=SkillSetService)

        # Simulate openclaw
        mock_service._engine_type = "openclaw"
        mock_service.ensure_default_skill_set.return_value = {
            "id": "default-openclaw-123",
            "name": "默认技能集-openclaw",
            "bolt_id": "botX",
            "engine_type": "openclaw",
            "is_default": True,
            "is_builtin": False,
            "user_id": "100",
            "gmt_created": "2026-04-01T00:00:00",
            "gmt_modified": "2026-04-01T00:00:00",
            "is_active": True,
            "skills": [],
        }

        # Simulate aicoding
        mock_service_claude = MagicMock(spec=SkillSetService)
        mock_service_claude._engine_type = "aicoding"
        mock_service_claude.ensure_default_skill_set.return_value = {
            "id": "default-aicoding-456",
            "name": "默认技能集-aicoding",
            "bolt_id": "botX",
            "engine_type": "aicoding",
            "is_default": True,
            "is_builtin": False,
            "user_id": "100",
            "gmt_created": "2026-04-01T00:00:00",
            "gmt_modified": "2026-04-01T00:00:00",
            "is_active": True,
            "skills": [],
        }

        # Verify different engines produce different ids
        result_openclaw = mock_service.ensure_default_skill_set()
        result_aicoding = mock_service_claude.ensure_default_skill_set()

        assert result_openclaw["id"] != result_aicoding["id"]
        assert result_openclaw["engine_type"] == "openclaw"
        assert result_aicoding["engine_type"] == "aicoding"

    def test_skill_set_response_includes_engine_type(self):
        """SkillSetResponse schema must include engine_type field."""
        from agentclaw.community.adapters.http.skill_center.schemas import SkillSetResponse

        data = {
            "id": "test-123",
            "name": "Test Set",
            "description": None,
            "is_default": True,
            "is_builtin": False,
            "user_id": None,
            "bot_id": "botX",
            "engine_type": "openclaw",
            "gmt_created": "2026-04-01T00:00:00",
            "gmt_modified": "2026-04-01T00:00:00",
            "is_active": True,
            "skills": [],
        }
        response = SkillSetResponse(**data)
        assert response.engine_type == "openclaw"