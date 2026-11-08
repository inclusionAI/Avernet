"""The local catalog fixture is explicit, scoped and production-inert."""

import json

import pytest
from pydantic import ValidationError

from agentclaw.community.di.modules.bot_knowledge_module import BotKnowledgeModule
from agentclaw.community.di.profile import DeployProfile
from agentclaw.community.kernel.knowledge import BotKnowledgeScope, KnowledgeError


@pytest.fixture
def catalog_file(tmp_path, monkeypatch):
    path = tmp_path / "catalog.json"
    monkeypatch.setenv("SINGLEBOX_ACCEPTANCE_KNOWLEDGE_FIXTURE_FILE", str(path))
    return path


def test_local_fixture_is_exactly_scope_and_space_bound(catalog_file):
    catalog_file.write_text(
        json.dumps(
            [dict(bot_id="bot", owner_id="owner", env="dev", space_id=1, kb_ids=["kb"])]
        )
    )
    catalog = BotKnowledgeModule(DeployProfile.SINGLEBOX).catalog()
    scope = BotKnowledgeScope("bot", "owner", "dev")
    assert catalog.list_authorized(scope=scope, space_id=1) == ("kb",)
    assert catalog.list_authorized(scope=scope, space_id=2) == ()
    assert (
        catalog.list_authorized(
            scope=BotKnowledgeScope("bot", "other", "dev"), space_id=1
        )
        == ()
    )
    assert (
        catalog.list_authorized(
            scope=BotKnowledgeScope("bot", "owner", "prod"), space_id=1
        )
        == ()
    )
    assert len(catalog.calls_to("list_authorized")) == 4


@pytest.mark.parametrize("profile", [DeployProfile.COMMUNITY, DeployProfile.TEST])
def test_fixture_does_not_change_other_profile_catalogs(catalog_file, profile):
    # File deliberately absent: neither production/community nor unit-test
    # profiles may read a leftover acceptance fixture environment variable.
    catalog = BotKnowledgeModule(profile).catalog()
    with pytest.raises(KnowledgeError, match="KNOWLEDGE_CATALOG_UNAVAILABLE"):
        catalog.list_authorized(
            scope=BotKnowledgeScope("bot", "owner", "dev"), space_id=1
        )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [{"bot_id": "bot"}],
        [
            dict(
                bot_id="bot",
                owner_id="owner",
                env="dev",
                space_id=1,
                kb_ids=["kb"],
                unexpected=True,
            )
        ],
    ],
)
def test_invalid_fixture_fails_at_composition(catalog_file, payload):
    catalog_file.write_text(json.dumps(payload))
    with pytest.raises((ValueError, ValidationError)):
        BotKnowledgeModule(DeployProfile.SINGLEBOX).catalog()


def test_unconfigured_singlebox_catalog_still_fails_closed(monkeypatch):
    monkeypatch.delenv("SINGLEBOX_ACCEPTANCE_KNOWLEDGE_FIXTURE_FILE", raising=False)
    with pytest.raises(KnowledgeError, match="KNOWLEDGE_CATALOG_UNAVAILABLE"):
        BotKnowledgeModule(DeployProfile.SINGLEBOX).catalog().list_authorized(
            scope=BotKnowledgeScope("bot", "owner", "dev"), space_id=1
        )
