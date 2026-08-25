"""``baas.deploy_runtime`` selects the deploy composer, and nothing else does.

Rule 14: which container a deployment runs is a config decision made once in
the composition root. The tests that matter here are the two edges — the
default keeps every existing deployment on the managed image, and an
unrecognized value stops the boot rather than quietly picking one.

That second one is the load-bearing case. A typo'd ``deploy_runtime`` that fell
back to ``managed`` would give an ACK deployment the managed image's boot chain
and NAS mounts: the pods would start, report healthy, and run bots that cannot
work. A ``ValueError`` at wiring time is orders of magnitude cheaper.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.service_bot.services.deploy.ack_composer import (
    AckDeployConfigComposer,
)
from agentclaw.community.core.service_bot.services.deploy.managed_composer import (
    ManagedDeployConfigComposer,
)
from agentclaw.community.di import config as cfg
from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.config_module import ConfigModule
from agentclaw.community.di.modules.service_bot_module import ServiceBotModule


def _composer(deploy_runtime: str):
    return ServiceBotModule().deploy_config_composer(
        baas=cfg.BaasConfig(deploy_runtime=deploy_runtime),
        bot_repo=MagicMock(),
        sandbox_registry=MagicMock(),
    )


def _baas_config(monkeypatch, user_config: dict) -> cfg.BaasConfig:
    monkeypatch.setattr(config_module, "_user_config", lambda: dict(user_config))
    return ConfigModule().baas()


class TestSelection:
    def test_managed_is_the_default(self):
        assert cfg.BaasConfig().deploy_runtime == "managed"
        assert isinstance(_composer("managed"), ManagedDeployConfigComposer)

    def test_ack_selects_the_ack_composer(self):
        assert isinstance(_composer("ack"), AckDeployConfigComposer)

    @pytest.mark.parametrize("value", ["", "Managed", "k8s", "arca"])
    def test_an_unknown_runtime_fails_the_boot(self, value):
        with pytest.raises(ValueError, match="unknown baas.deploy_runtime"):
            _composer(value)

    def test_the_error_names_the_values_that_would_have_worked(self):
        with pytest.raises(ValueError) as exc:
            _composer("aliyun")

        message = str(exc.value)
        assert "'managed'" in message
        assert "'ack'" in message


class TestConfigBlock:
    def test_absent_block_keeps_the_managed_image(self, monkeypatch):
        assert _baas_config(monkeypatch, {}).deploy_runtime == "managed"

    def test_absent_key_keeps_the_managed_image(self, monkeypatch):
        config = _baas_config(monkeypatch, {"baas": {"tenant": "community"}})

        assert config.deploy_runtime == "managed"

    def test_the_yaml_value_wins(self, monkeypatch):
        config = _baas_config(monkeypatch, {"baas": {"deploy_runtime": "ack"}})

        assert config.deploy_runtime == "ack"

    def test_surrounding_whitespace_is_not_a_different_runtime(self, monkeypatch):
        """A trailing space in a yaml overlay would otherwise fail the boot with
        an error that looks identical to the value that works."""
        config = _baas_config(monkeypatch, {"baas": {"deploy_runtime": "  ack "}})

        assert config.deploy_runtime == "ack"
