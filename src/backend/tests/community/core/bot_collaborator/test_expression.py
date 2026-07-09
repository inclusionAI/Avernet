"""Tests for interceptor expression resolver."""
import pytest
from agentclaw.community.core.bot_collaborator.interceptor.expression import ExpressionResolver


class TestExpressionResolver:
    """ExpressionResolver 测试用例。"""

    def setup_method(self):
        self.resolver = ExpressionResolver()

    def test_resolve_simple_path(self):
        """测试简单路径解析。"""
        # 模拟 Pydantic 模型
        class MockRequest:
            publish_id = "123"
            bot_id = "bot_001"

        route_kwargs = {"request": MockRequest()}

        result = self.resolver.resolve("$request.publish_id", route_kwargs)
        assert result == "123"

        result = self.resolver.resolve("$request.bot_id", route_kwargs)
        assert result == "bot_001"

    def test_resolve_nested_path(self):
        """测试嵌套路径解析。"""
        class MockBot:
            id = "bot_001"
            name = "test_bot"

        class MockRequest:
            bot = MockBot()

        route_kwargs = {"request": MockRequest()}

        result = self.resolver.resolve("$request.bot.id", route_kwargs)
        assert result == "bot_001"

        result = self.resolver.resolve("$request.bot.name", route_kwargs)
        assert result == "test_bot"

    def test_resolve_dict_path(self):
        """测试字典路径解析。"""
        route_kwargs = {
            "request": {
                "publish_id": "456",
                "owner_id": "user_001",
            }
        }

        result = self.resolver.resolve("$request.publish_id", route_kwargs)
        assert result == "456"

        result = self.resolver.resolve("$request.owner_id", route_kwargs)
        assert result == "user_001"

    def test_resolve_nested_dict_path(self):
        """测试嵌套字典路径解析。"""
        route_kwargs = {
            "request": {
                "bot": {
                    "id": "bot_002",
                    "owner": {"id": "user_002"},
                }
            }
        }

        result = self.resolver.resolve("$request.bot.id", route_kwargs)
        assert result == "bot_002"

        result = self.resolver.resolve("$request.bot.owner.id", route_kwargs)
        assert result == "user_002"

    def test_resolve_literal_value(self):
        """测试字面值（不以 $ 开头）直接返回。"""
        result = self.resolver.resolve("literal_value", {})
        assert result == "literal_value"

        result = self.resolver.resolve("bot_id", {})
        assert result == "bot_id"

    def test_resolve_non_string_returns_as_is(self):
        """测试非字符串类型直接返回。"""
        result = self.resolver.resolve(123, {})
        assert result == 123

        result = self.resolver.resolve(None, {})
        assert result is None

        result = self.resolver.resolve(["a", "b"], {})
        assert result == ["a", "b"]

    def test_resolve_missing_key_returns_none(self):
        """测试不存在的键返回 None。"""
        route_kwargs = {"other": "value"}

        result = self.resolver.resolve("$request.publish_id", route_kwargs)
        assert result is None

    def test_resolve_missing_nested_key_returns_none(self):
        """测试不存在的嵌套键返回 None。"""
        class MockRequest:
            publish_id = "123"

        route_kwargs = {"request": MockRequest()}

        # 不存在的嵌套属性
        result = self.resolver.resolve("$request.non_existent", route_kwargs)
        assert result is None

        result = self.resolver.resolve("$request.publish_id.non_existent", route_kwargs)
        assert result is None

    def test_resolve_empty_expression(self):
        """测试空表达式。"""
        result = self.resolver.resolve("$", {})
        assert result is None

    def test_resolve_integer_value(self):
        """测试整数值返回。"""
        route_kwargs = {
            "request": {
                "publish_id": 123,  # int 类型
            }
        }

        result = self.resolver.resolve("$request.publish_id", route_kwargs)
        assert result == 123
        assert isinstance(result, int)

    def test_resolve_mixed_object_and_dict(self):
        """测试混合对象和字典的路径解析。"""
        class MockBot:
            id = "bot_003"

        route_kwargs = {
            "request": MockBot(),  # 对象
            "body": {"owner_id": "user_003"},  # 字典
        }

        result = self.resolver.resolve("$request.id", route_kwargs)
        assert result == "bot_003"

        result = self.resolver.resolve("$body.owner_id", route_kwargs)
        assert result == "user_003"