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
from agentclaw.community.kernel.deploy_runtime import DeployRuntime


def _composer(runtime: DeployRuntime):
    return ServiceBotModule().deploy_config_composer(
        deploy_runtime=cfg.DeployRuntimeConfig(runtime),
        bot_repo=MagicMock(),
        sandbox_registry=MagicMock(),
    )


def _deploy_runtime(monkeypatch, user_config: dict) -> cfg.DeployRuntimeConfig:
    monkeypatch.setattr(config_module, "_user_config", lambda: dict(user_config))
    return ConfigModule().deploy_runtime()


class TestSelection:
    def test_managed_is_the_default(self):
        assert cfg.DeployRuntimeConfig().runtime is DeployRuntime.MANAGED
        assert isinstance(
            _composer(DeployRuntime.MANAGED), ManagedDeployConfigComposer
        )

    def test_ack_selects_the_ack_composer(self):
        assert isinstance(_composer(DeployRuntime.ACK), AckDeployConfigComposer)

    @pytest.mark.parametrize("runtime", list(DeployRuntime))
    def test_every_runtime_has_a_composer(self, runtime):
        """The mapping is total over the enum: adding a member without wiring a
        composer fails here rather than at some deployment's first create."""
        assert _composer(runtime).name is runtime


class TestConfigBlock:
    def test_absent_block_keeps_the_managed_image(self, monkeypatch):
        assert _deploy_runtime(monkeypatch, {}).runtime is DeployRuntime.MANAGED

    def test_absent_key_keeps_the_managed_image(self, monkeypatch):
        config = _deploy_runtime(monkeypatch, {"baas": {"tenant": "community"}})

        assert config.runtime is DeployRuntime.MANAGED

    def test_the_yaml_value_wins(self, monkeypatch):
        config = _deploy_runtime(monkeypatch, {"baas": {"deploy_runtime": "ack"}})

        assert config.runtime is DeployRuntime.ACK

    def test_surrounding_whitespace_is_not_a_different_runtime(self, monkeypatch):
        """A trailing space in a yaml overlay would otherwise fail the boot with
        an error that looks identical to the value that works."""
        config = _deploy_runtime(
            monkeypatch, {"baas": {"deploy_runtime": "  ack "}}
        )

        assert config.runtime is DeployRuntime.ACK

    @pytest.mark.parametrize("value", ["", "Managed", "k8s", "arca"])
    def test_an_unknown_runtime_fails_at_config_load(self, monkeypatch, value):
        with pytest.raises(ValueError, match="unknown baas.deploy_runtime"):
            _deploy_runtime(monkeypatch, {"baas": {"deploy_runtime": value}})

    def test_the_error_names_the_values_that_would_have_worked(self, monkeypatch):
        with pytest.raises(ValueError) as exc:
            _deploy_runtime(monkeypatch, {"baas": {"deploy_runtime": "aliyun"}})

        message = str(exc.value)
        assert "'managed'" in message
        assert "'ack'" in message
