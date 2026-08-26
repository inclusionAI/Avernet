"""RealEvalBindingResolver 单元测试。"""

from unittest.mock import MagicMock

from secbaas.community.plugins.eval_env.real._real_binding_resolver import (
    RealEvalBindingResolver,
)


def _make_binding_mock(binding_id=200099):
    binding = MagicMock()
    binding.id = binding_id
    return binding


class TestRealEvalBindingResolver:
    def test_resolve_eval_binding_returns_binding_id(self):
        binding_repo = MagicMock()
        binding_repo.find_eval_binding.return_value = _make_binding_mock(200099)
        system_config = MagicMock()

        resolver = RealEvalBindingResolver(
            binding_repo=binding_repo,
            system_config_service=system_config,
        )
        result = resolver.resolve_eval_binding(
            bot_id="bot-1",
            entity_id="entity-1",
            env="staging",
        )
        assert result == 200099
        binding_repo.find_eval_binding.assert_called_once_with(
            bot_id="bot-1", entity_id="entity-1", env="staging"
        )

    def test_resolve_eval_binding_returns_none_when_not_found(self):
        binding_repo = MagicMock()
        binding_repo.find_eval_binding.return_value = None
        system_config = MagicMock()

        resolver = RealEvalBindingResolver(
            binding_repo=binding_repo,
            system_config_service=system_config,
        )
        result = resolver.resolve_eval_binding(
            bot_id="bot-1",
            entity_id="entity-1",
            env="staging",
        )
        assert result is None

    def test_resolve_eval_binding_returns_none_on_exception(self):
        binding_repo = MagicMock()
        binding_repo.find_eval_binding.side_effect = Exception("DB error")
        system_config = MagicMock()

        resolver = RealEvalBindingResolver(
            binding_repo=binding_repo,
            system_config_service=system_config,
        )
        result = resolver.resolve_eval_binding(
            bot_id="bot-1",
            entity_id="entity-1",
            env="staging",
        )
        assert result is None

    def test_is_eval_env_enabled_returns_true(self):
        config_mock = MagicMock()
        config_mock.conf_value = "true"
        system_config = MagicMock()
        system_config.get_config.return_value = config_mock

        resolver = RealEvalBindingResolver(
            binding_repo=MagicMock(),
            system_config_service=system_config,
        )
        assert resolver.is_eval_env_enabled() is True

    def test_is_eval_env_enabled_returns_true_for_one(self):
        config_mock = MagicMock()
        config_mock.conf_value = "1"
        system_config = MagicMock()
        system_config.get_config.return_value = config_mock

        resolver = RealEvalBindingResolver(
            binding_repo=MagicMock(),
            system_config_service=system_config,
        )
        assert resolver.is_eval_env_enabled() is True

    def test_is_eval_env_enabled_returns_true_for_yes(self):
        config_mock = MagicMock()
        config_mock.conf_value = "yes"
        system_config = MagicMock()
        system_config.get_config.return_value = config_mock

        resolver = RealEvalBindingResolver(
            binding_repo=MagicMock(),
            system_config_service=system_config,
        )
        assert resolver.is_eval_env_enabled() is True

    def test_is_eval_env_enabled_returns_false_when_config_none(self):
        system_config = MagicMock()
        system_config.get_config.return_value = None

        resolver = RealEvalBindingResolver(
            binding_repo=MagicMock(),
            system_config_service=system_config,
        )
        assert resolver.is_eval_env_enabled() is False

    def test_is_eval_env_enabled_returns_false_when_service_none(self):
        resolver = RealEvalBindingResolver(
            binding_repo=MagicMock(),
            system_config_service=None,
        )
        assert resolver.is_eval_env_enabled() is False

    def test_is_eval_env_enabled_returns_false_on_exception(self):
        system_config = MagicMock()
        system_config.get_config.side_effect = Exception("error")

        resolver = RealEvalBindingResolver(
            binding_repo=MagicMock(),
            system_config_service=system_config,
        )
        assert resolver.is_eval_env_enabled() is False

    def test_is_eval_env_enabled_returns_false_for_other_value(self):
        config_mock = MagicMock()
        config_mock.conf_value = "false"
        system_config = MagicMock()
        system_config.get_config.return_value = config_mock

        resolver = RealEvalBindingResolver(
            binding_repo=MagicMock(),
            system_config_service=system_config,
        )
        assert resolver.is_eval_env_enabled() is False
