"""Tests for quality task router helper functions.

Integration tests for _record_to_response and extract_from_task_id
using real injector with in-memory SQLite database.
"""
import pytest

from agentclaw.community.api.quality_service import QualityTaskServiceProtocol
from agentclaw.community.core.bot_collaborator.interceptor import InterceptorContext, PermissionParams
from tests.community.framework.world import World


class TestExtractFromTaskId:
    """Tests for extract_from_task_id permission extractor."""

    @pytest.fixture
    def ctx(self, world: World) -> InterceptorContext:
        """Create an interceptor context with real injector."""
        return InterceptorContext(injector=world.injector)

    @pytest.fixture
    def ctx_without_injector(self) -> InterceptorContext:
        """Create an interceptor context without injector."""
        return InterceptorContext(injector=None)

    @pytest.mark.asyncio
    async def test_empty_id_returns_empty_params(self, ctx: InterceptorContext):
        """Test that empty id returns empty PermissionParams."""
        from agentclaw.community.adapters.http.quality.router import extract_from_task_id

        result = await extract_from_task_id("", ctx)
        assert result == PermissionParams()

        result = await extract_from_task_id(None, ctx)
        assert result == PermissionParams()

    @pytest.mark.asyncio
    async def test_none_injector_returns_empty_params(
        self, ctx_without_injector: InterceptorContext
    ):
        """Test that None injector returns empty PermissionParams."""
        from agentclaw.community.adapters.http.quality.router import extract_from_task_id

        result = await extract_from_task_id("1", ctx_without_injector)
        assert result == PermissionParams()

    @pytest.mark.asyncio
    async def test_invalid_id_format_returns_empty_params(self, ctx: InterceptorContext):
        """Test that invalid id format (non-integer) returns empty PermissionParams."""
        from agentclaw.community.adapters.http.quality.router import extract_from_task_id

        result = await extract_from_task_id("not-an-int", ctx)
        assert result == PermissionParams()

    @pytest.mark.asyncio
    async def test_task_not_found_returns_empty_params(self, ctx: InterceptorContext):
        """Test that task not found returns empty PermissionParams."""
        from agentclaw.community.adapters.http.quality.router import extract_from_task_id

        # ID 999999 does not exist in the database
        result = await extract_from_task_id("999999", ctx)
        assert result == PermissionParams()

    @pytest.mark.asyncio
    async def test_task_found_returns_params_with_ids(self, ctx: InterceptorContext, world: World):
        """Test that found task returns PermissionParams with bot_id and owner_id."""
        from agentclaw.community.adapters.http.quality.router import extract_from_task_id
        from tests.community.factories.quality import make_quality_task

        # Create a real task in the database
        task = make_quality_task(
            world,
            task_type="eval",
            biz_type="service_bot_single",
            bot_id="bot-abc",
            owner_id="user-xyz",
        )

        result = await extract_from_task_id(str(task.id), ctx)

        assert result.bot_id == "bot-abc"
        assert result.owner_id == "user-xyz"

    @pytest.mark.asyncio
    async def test_task_with_none_bot_id_returns_none_values(
        self, ctx: InterceptorContext, world: World
    ):
        """Test that task with None bot_id returns PermissionParams with None bot_id."""
        from agentclaw.community.adapters.http.quality.router import extract_from_task_id
        from tests.community.factories.quality import make_quality_task

        # Create a task without bot_id
        task = make_quality_task(
            world,
            task_type="eval",
            biz_type="service_bot_single",
            bot_id=None,
            owner_id="user-xyz",
        )

        result = await extract_from_task_id(str(task.id), ctx)

        assert result.bot_id is None
        assert result.owner_id == "user-xyz"

    @pytest.mark.asyncio
    async def test_task_with_none_owner_id_returns_none_values(
        self, ctx: InterceptorContext, world: World
    ):
        """Test that task with None owner_id returns PermissionParams with None owner_id."""
        from agentclaw.community.adapters.http.quality.router import extract_from_task_id
        from tests.community.factories.quality import make_quality_task

        # Create a task without owner_id
        task = make_quality_task(
            world,
            task_type="eval",
            biz_type="service_bot_single",
            bot_id="bot-abc",
            owner_id=None,
        )

        result = await extract_from_task_id(str(task.id), ctx)

        assert result.bot_id == "bot-abc"
        assert result.owner_id is None

    @pytest.mark.asyncio
    async def test_task_with_both_none_ids_returns_none_values(
        self, ctx: InterceptorContext, world: World
    ):
        """Test that task with both None bot_id/owner_id returns PermissionParams with None values."""
        from agentclaw.community.adapters.http.quality.router import extract_from_task_id
        from tests.community.factories.quality import make_quality_task

        # Create a task without bot_id and owner_id
        task = make_quality_task(
            world,
            task_type="eval",
            biz_type="service_bot_single",
            bot_id=None,
            owner_id=None,
        )

        result = await extract_from_task_id(str(task.id), ctx)

        assert result.bot_id is None
        assert result.owner_id is None

    @pytest.mark.asyncio
    async def test_multiple_tasks_return_correct_ids(self, ctx: InterceptorContext, world: World):
        """Test that multiple tasks can be queried and return correct IDs."""
        from agentclaw.community.adapters.http.quality.router import extract_from_task_id
        from tests.community.factories.quality import make_quality_task

        # Create multiple tasks
        task1 = make_quality_task(
            world,
            bot_id="bot-1",
            owner_id="user-1",
        )
        task2 = make_quality_task(
            world,
            bot_id="bot-2",
            owner_id="user-2",
        )
        task3 = make_quality_task(
            world,
            bot_id="bot-3",
            owner_id="user-3",
        )

        # Query each task
        result1 = await extract_from_task_id(str(task1.id), ctx)
        result2 = await extract_from_task_id(str(task2.id), ctx)
        result3 = await extract_from_task_id(str(task3.id), ctx)

        assert result1.bot_id == "bot-1"
        assert result1.owner_id == "user-1"
        assert result2.bot_id == "bot-2"
        assert result2.owner_id == "user-2"
        assert result3.bot_id == "bot-3"
        assert result3.owner_id == "user-3"


class TestRecordToResponse:
    """Tests for _record_to_response helper function."""

    def test_record_to_response_converts_all_fields(self, world: World):
        """Test _record_to_response converts all fields correctly."""
        from agentclaw.community.adapters.http.quality.router import _record_to_response

        record = world.get(QualityTaskServiceProtocol).create_task(
            task_type="stress_test",
            biz_type="multi_bot",
            bot_id="bot-999",
            owner_id="user-888",
            ext={"key": "value"},
            operator_id="op-123",
        )

        response = _record_to_response(record)

        assert response.id == record.id
        assert response.uuid == record.uuid
        assert response.status == "init"
        assert response.task_type == "stress_test"
        assert response.biz_type == "multi_bot"
        assert response.bot_id == "bot-999"
        assert response.owner_id == "user-888"
        assert response.ext == {"key": "value"}
        assert response.operator_id == "op-123"

    def test_record_to_response_with_none_fields(self, world: World):
        """Test _record_to_response handles None optional fields."""
        from agentclaw.community.adapters.http.quality.router import _record_to_response

        record = world.get(QualityTaskServiceProtocol).create_task(
            task_type="eval",
            biz_type="service_bot_single",
            bot_id=None,
            owner_id=None,
            operator_id=None,
        )

        response = _record_to_response(record)

        assert response.id == record.id
        assert response.bot_id is None
        assert response.owner_id is None
        assert response.operator_id is None
        assert response.ext == {}  # default empty dict

    def test_record_to_response_with_empty_ext(self, world: World):
        """Test _record_to_response handles empty ext."""
        from agentclaw.community.adapters.http.quality.router import _record_to_response

        record = world.get(QualityTaskServiceProtocol).create_task(
            task_type="eval",
            biz_type="service_bot_single",
            ext={},
        )

        response = _record_to_response(record)

        assert response.ext == {}

    def test_record_to_response_with_nested_ext(self, world: World):
        """Test _record_to_response handles nested ext data."""
        from agentclaw.community.adapters.http.quality.router import _record_to_response

        record = world.get(QualityTaskServiceProtocol).create_task(
            task_type="eval",
            biz_type="service_bot_single",
            ext={"level1": {"level2": {"level3": "value"}}},
        )

        response = _record_to_response(record)

        assert response.ext == {"level1": {"level2": {"level3": "value"}}}

    def test_record_to_response_preserves_timestamps(self, world: World):
        """Test _record_to_response preserves timestamps correctly."""
        from agentclaw.community.adapters.http.quality.router import _record_to_response

        record = world.get(QualityTaskServiceProtocol).create_task(
            task_type="eval",
            biz_type="service_bot_single",
        )

        response = _record_to_response(record)

        # Timestamps should be preserved
        assert response.gmt_create is not None
        assert response.gmt_modified is not None

    def test_record_to_response_with_special_characters(self, world: World):
        """Test _record_to_response handles special characters in fields."""
        from agentclaw.community.adapters.http.quality.router import _record_to_response

        record = world.get(QualityTaskServiceProtocol).create_task(
            task_type="eval",
            biz_type="service_bot_single",
            bot_id="bot-with-dashes_and_underscores",
            owner_id="user@example.com",
            ext={"unicode": "你好世界", "special": "a\nb\tc"},
        )

        response = _record_to_response(record)

        assert response.bot_id == "bot-with-dashes_and_underscores"
        assert response.owner_id == "user@example.com"
        assert response.ext == {"unicode": "你好世界", "special": "a\nb\tc"}