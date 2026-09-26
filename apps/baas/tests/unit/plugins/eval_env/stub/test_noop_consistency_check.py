"""NoopEvalConsistencyCheck 单元测试。"""

from secbaas.community.plugins.eval_env.stub._noop_consistency_check import (
    NoopEvalConsistencyCheck,
)


class TestNoopEvalConsistencyCheck:
    def test_check_default_tag_consistency_returns_true(self):
        checker = NoopEvalConsistencyCheck()
        result = checker.check_default_tag_consistency(
            binding_info=None,
            chat_metadata={"default_tag": "staging"},
        )
        assert result is True
