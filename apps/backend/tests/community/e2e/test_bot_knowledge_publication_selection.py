"""Controlled HTTP/DI/DB flow, not live device or provider-deployment acceptance."""

from fastapi.testclient import TestClient

from agentclaw.community.api.bot_knowledge_service import (
    BotKnowledgePublicationSelectionProtocol,
)
from agentclaw.community.core.repository.protocols.publishing import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.kernel.knowledge import KnowledgeError
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotCommonConfig
from tests.community.endpoints.test_bot_knowledge_router import (
    _BOT_ID,
    _KB_ID,
    _OWNER_ID,
    _seed_publication,
)


def test_version_selection_does_not_follow_new_draft_or_read_side_effects(
    app_with_testing_modules,
    world,
):
    _seed_publication(world)
    observer = world.get(BotKnowledgePublicationSelectionProtocol)
    assert observer.record_build_selection(publication_id=101)
    repo = world.get(BotPublishRepositoryProtocol)
    for source, target in [
        ("building", "built"),
        ("built", "validate_pub"),
        ("validate_pub", "validating"),
    ]:
        assert repo.update_status(101, target, source)
    headers = {"x-user-id": _OWNER_ID}
    selection_url = f"/api/bot-knowledge/{_BOT_ID}/publications/101/selection"
    with TestClient(app_with_testing_modules) as client:
        staging = client.get(
            selection_url, params={"owner_id": _OWNER_ID}, headers=headers
        )
        assert staging.status_code == 200
        original = staging.json()
        assert original["kb_ids"] == [_KB_ID]
        for source, target in [("validating", "online_pub"), ("online_pub", "success")]:
            assert repo.update_status(101, target, source)
        assert (
            client.get(
                selection_url, params={"owner_id": _OWNER_ID}, headers=headers
            ).json()
            == original
        )
        with world.get(DatabasePlugin).orm_session() as session:
            previous = session.query(BotPublishModel).filter_by(id=101).one()
            session.add(
                BotPublishModel(
                    id=102,
                    source_bot_pk=previous.source_bot_pk,
                    source_bot_id=_BOT_ID,
                    publish_bot_id="knowledge-pub2",
                    name="Next version",
                    owner_id=_OWNER_ID,
                    permission_owner=_OWNER_ID,
                    env=previous.env,
                    version=2,
                    status="draft",
                    last_pub_id=101,
                )
            )
        # Normal existing PUT, not a hand-built current-selection service.
        changed = client.put(
            f"/api/bot-knowledge/{_BOT_ID}/{_KB_ID}",
            headers=headers,
            json={"owner_id": _OWNER_ID, "enabled": False, "expected_revision": 1},
        )
        assert changed.status_code == 200
        assert changed.json()["enabled"] is False
        assert (
            client.get(
                selection_url, params={"owner_id": _OWNER_ID}, headers=headers
            ).json()
            == original
        )
        current = client.get(
            f"/api/bot-knowledge/{_BOT_ID}",
            params={"owner_id": _OWNER_ID},
            headers=headers,
        ).json()
        assert current["runtime_selection"] == "current"
        assert current["items"][0]["enabled"] is False
        missing = client.get(
            f"/api/bot-knowledge/{_BOT_ID}/publications/102/selection",
            params={"owner_id": _OWNER_ID},
            headers=headers,
        ).json()
        assert missing["observation_status"] == "unrecorded"
        # Exercise the native DB rollback flip, without pretending to deploy devices.
        assert repo.update_status(101, "upgraded", "success")
        assert repo.update_status(102, "success", "draft")
        assert repo.rollback_flip(
            demoted_publish_id=102,
            demoted_ext={},
            demoted_from_status="success",
            demoted_to_status="draft",
            restored_publish_id=101,
            restored_ext={},
            restored_from_status="upgraded",
            restored_to_status="success",
        ) == (True, True)
        assert (
            client.get(
                selection_url, params={"owner_id": _OWNER_ID}, headers=headers
            ).json()
            == original
        )
        assert (
            client.get(
                f"/api/bot-knowledge/{_BOT_ID}",
                params={"owner_id": _OWNER_ID},
                headers=headers,
            ).json()["items"][0]["enabled"]
            is False
        )
    with world.get(DatabasePlugin).orm_session() as session:
        assert session.query(BotCommonConfig).count() == 1
        assert (
            session.query(BotPublishModel).filter_by(id=101).one().status == "success"
        )


def test_real_di_provider_failure_is_visible_to_new_http_endpoint(
    app_with_testing_modules,
    world,
    monkeypatch,
):
    _seed_publication(world)
    observer = world.get(BotKnowledgePublicationSelectionProtocol)

    def broken_provider(**kwargs):
        raise KnowledgeError("PROVIDER_MUTATION_WITNESS", 503)

    monkeypatch.setattr(observer, "get_publication_selection", broken_provider)
    with TestClient(app_with_testing_modules) as client:
        response = client.get(
            f"/api/bot-knowledge/{_BOT_ID}/publications/101/selection",
            params={"owner_id": _OWNER_ID},
            headers={"x-user-id": _OWNER_ID},
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "PROVIDER_MUTATION_WITNESS"


def test_selection_route_is_registered_once(app_with_testing_modules):
    from tests.community.framework.coverage_gate import _walk_apiroutes

    paths = [
        path
        for path, _ in _walk_apiroutes(app_with_testing_modules.routes)
        if path.endswith("/publications/{publication_id}/selection")
    ]
    assert paths == [
        "/api/bot-knowledge/{bot_id}/publications/{publication_id}/selection"
    ]
