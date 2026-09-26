"""NoopEvalBindingResolver 单元测试。"""

from secbaas.community.plugins.eval_env.stub._noop_binding_resolver import (
    NoopEvalBindingResolver,
)


class TestNoopEvalBindingResolver:
    def test_resolve_eval_binding_returns_none(self):
        resolver = NoopEvalBindingResolver()
        result = resolver.resolve_eval_binding(
            bot_id="bot-1",
            entity_id="entity-1",
            env="prod",
        )
        assert result is None

    def test_is_eval_env_enabled_returns_false(self):
        resolver = NoopEvalBindingResolver()
        assert resolver.is_eval_env_enabled() is False
