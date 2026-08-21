"""_eval_binding_query 单元测试。"""

from unittest.mock import MagicMock

from secbaas.community.api.eval_env import EvalBindingResolverProtocol
from secbaas.community.core.service.eval_binding._eval_binding_query import (
    is_eval_env_enabled,
    resolve_eval_binding_id,
)


def _make_plugin(enabled=True, resolved_id=100099):
    """构造 mock EvalBindingResolverProtocol。"""
    plugin = MagicMock(spec=EvalBindingResolverProtocol)
    plugin.is_eval_env_enabled.return_value = enabled
    plugin.resolve_eval_binding.return_value = resolved_id
    return plugin


class TestResolveEvalBindingId:
    def test_delegates_to_plugin(self):
        plugin = _make_plugin(resolved_id=200099)
        result = resolve_eval_binding_id(
            plugin,
            bot_id="bot-1",
            entity_id="entity-1",
            env="staging",
        )
        assert result == 200099
        plugin.resolve_eval_binding.assert_called_once_with(
            bot_id="bot-1",
            entity_id="entity-1",
            env="staging",
        )

    def test_returns_none_when_plugin_returns_none(self):
        plugin = _make_plugin(resolved_id=None)
        result = resolve_eval_binding_id(
            plugin,
            bot_id="bot-1",
            entity_id="entity-1",
            env="staging",
        )
        assert result is None


class TestIsEvalEnvEnabled:
    def test_returns_true_when_enabled(self):
        plugin = _make_plugin(enabled=True)
        assert is_eval_env_enabled(plugin) is True
        plugin.is_eval_env_enabled.assert_called_once()

    def test_returns_false_when_disabled(self):
        plugin = _make_plugin(enabled=False)
        assert is_eval_env_enabled(plugin) is False
        plugin.is_eval_env_enabled.assert_called_once()
