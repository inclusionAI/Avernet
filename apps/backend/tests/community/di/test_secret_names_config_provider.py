"""``ConfigModule.secret_names`` must carry every field through from yaml.

This provider is the only place the injected ``SecretNamesConfig`` is built, so
a field missing from its constructor call is silently pinned to the dataclass
default forever — the yaml key is read by nobody and every consumer sees "".

That is not a cosmetic drift. For a token name it is a security failure:
``SkillCenterInternalTokenBindings._resolved_internal_token`` treats an empty
name as "no secret configured" and returns the publicly-visible singlebox
fallback token, so an unwired field would authorize that known string in
**every** environment, production included.

The field-completeness test below is deliberately reflective over
``dataclasses.fields`` rather than a hand-written list: a new field added to
``SecretNamesConfig`` without a matching line in the provider fails here
immediately, which is exactly how the ``skill_center_internal_token`` gap got
in unnoticed.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from agentclaw.community.di import config as cfg
from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.config_module import ConfigModule

_NAMES = tuple(f.name for f in fields(cfg.SecretNamesConfig))


def _secret_names(monkeypatch, user_config: dict) -> cfg.SecretNamesConfig:
    monkeypatch.setattr(config_module, "_user_config", lambda: dict(user_config))
    return ConfigModule().secret_names()


@pytest.mark.unit
@pytest.mark.parametrize("name", _NAMES)
def test_every_field_is_read_from_the_yaml_block(monkeypatch, name: str):
    """A field the provider forgets stays at its default and the yaml is dead."""
    out = _secret_names(monkeypatch, {"secret_names": {name: "from-yaml"}})

    assert getattr(out, name) == "from-yaml", (
        f"ConfigModule.secret_names() does not read {name!r} from the "
        "secret_names block — add it to the constructor call, or the yaml key "
        "silently has no effect anywhere."
    )


@pytest.mark.unit
def test_an_absent_block_leaves_every_field_at_its_default(monkeypatch):
    out = _secret_names(monkeypatch, {})
    defaults = cfg.SecretNamesConfig()

    for name in _NAMES:
        assert getattr(out, name) == getattr(defaults, name)


@pytest.mark.unit
def test_the_skill_center_token_name_reaches_the_injected_config(monkeypatch):
    """The end the operator actually configures, pinned explicitly.

    An empty name here makes the internal endpoint accept the hardcoded
    singlebox fallback token, so this one is worth its own case rather than
    only living inside the parametrized sweep.
    """
    out = _secret_names(
        monkeypatch,
        {
            "secret_names": {
                "skill_center_internal_token": (
                    "other_manual_agentclaw_skill_center_internal_token"
                )
            }
        },
    )

    assert out.skill_center_internal_token == (
        "other_manual_agentclaw_skill_center_internal_token"
    )
    # Neighbouring names are untouched by the one key that was set.
    assert out.dormant_internal_token == ""
