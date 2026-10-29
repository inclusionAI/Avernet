"""Regression contracts for preserving native publication when knowledge is unavailable."""

import pytest

from agentclaw.community.core.service_bot.repository.models import (
    BotPublishModel,
    PublishStatus,
)
from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
    Binding,
    publication as _publication,
)

publication = _publication


@pytest.mark.parametrize("configured", [False, True])
def test_first_new_release_can_roll_back_to_pre_feature_version(
    publication, configured
):
    db, repo, current = publication
    legacy = repo.insert(
        {
            "source_bot_pk": 1,
            "source_bot_id": "service",
            "publish_bot_id": "service.pub",
            "name": "Service",
            "owner_id": "owner",
            "permission_owner": "owner",
            "status": PublishStatus.UPGRADED,
            "version": 0,
            "env": "dev",
            "ext": {"old": True},
        }
    )
    with db.orm_session() as session:
        session.query(BotPublishModel).filter_by(id=current.id).update(
            {"last_pub_id": legacy.id}
        )
        if not configured:
            session.query(Binding).delete()
    built = repo.update_status(current.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
    assert built.ext["knowledge_selection_snapshot"] is not None
    online = repo.update_status_with_ext(current.id, PublishStatus.ONLINE_PUB,
        {**built.ext, "publish": {"online": 77}}, PublishStatus.BUILDING)
    published = repo.compare_and_set_status_with_ext(publish_id=current.id,
        source_status=PublishStatus.ONLINE_PUB, target_status=PublishStatus.SUCCESS,
        expected_ext=online.ext, ext=online.ext, observed_workflow_id=77)
    result = repo.rollback_flip(
        demoted_publish_id=published.id,
        demoted_ext=published.ext,
        demoted_from_status=PublishStatus.SUCCESS,
        demoted_to_status=PublishStatus.DRAFT,
        restored_publish_id=legacy.id,
        restored_ext={**legacy.ext, "rollback_restored_from": published.id},
        restored_from_status=PublishStatus.UPGRADED,
        restored_to_status=PublishStatus.SUCCESS,
    )
    assert result == (True, True)
    restored = repo.get_by_id(legacy.id)
    deploying = repo.update_status_with_ext(
        legacy.id,
        PublishStatus.ONLINE_PUB,
        {**restored.ext, "publish": {"online": 88}},
        source_status=PublishStatus.SUCCESS,
    )
    completed = repo.compare_and_set_status_with_ext(
        publish_id=legacy.id,
        source_status=PublishStatus.ONLINE_PUB,
        target_status=PublishStatus.SUCCESS,
        expected_ext=deploying.ext,
        ext=deploying.ext,
        observed_workflow_id=((deploying.ext or {}).get("publish") or {}).get("online", 77),
    )
    assert completed.status == PublishStatus.SUCCESS
    assert completed.ext["knowledge_selection_result"]["status"] == "unknown"
    with db.orm_session() as session:
        assert not session.query(Binding).filter(Binding.online_enabled.is_(True)).count()


def test_build_admission_rolls_back_when_knowledge_table_read_fails(publication):
    from sqlalchemy import event

    db, repo, current = publication
    engine = db.factory.kw["bind"]

    def fail_knowledge_read(_conn, _cursor, statement, _params, _context, _many):
        if (
            statement.lstrip().upper().startswith("SELECT")
            and "ac_bot_knowledge_binding" in statement
        ):
            raise RuntimeError("injected knowledge read failure")

    event.listen(engine, "before_cursor_execute", fail_knowledge_read)
    try:
        with pytest.raises(RuntimeError, match="injected knowledge read failure"):
            repo.update_status(current.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
        assert repo.get_by_id(current.id).status == PublishStatus.DRAFT
    finally:
        event.remove(engine, "before_cursor_execute", fail_knowledge_read)


def test_online_success_does_not_reupload_unchanged_oversized_artifact(publication):
    from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
        _start_online,
    )

    _db, repo, current = publication
    objects = {}
    storage = repo._offload._oss
    storage.put_object.side_effect = lambda key, body: (
        objects.setdefault(key, body) is not None
    )
    storage.get_object.side_effect = lambda key: objects.get(key)
    updated = repo.update_status_with_ext(
        current.id,
        PublishStatus.DRAFT,
        {**current.ext, "config_artifact": {"data": "x" * 62000}},
        source_status=PublishStatus.DRAFT,
    )
    online = _start_online(repo, updated)
    storage.put_object.reset_mock()
    completed = repo.compare_and_set_status_with_ext(
        publish_id=current.id,
        source_status=PublishStatus.ONLINE_PUB,
        target_status=PublishStatus.SUCCESS,
        expected_ext=online.ext,
        ext={**online.ext, "done": True},
        observed_workflow_id=((online.ext or {}).get("publish") or {}).get("online", 77),
    )
    assert completed.status == PublishStatus.SUCCESS
    storage.put_object.assert_not_called()


def test_online_success_still_uploads_changed_oversized_artifact(publication):
    from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
        _start_online,
    )

    _db, repo, current = publication
    objects = {}
    storage = repo._offload._oss
    storage.put_object.side_effect = lambda key, body: (
        objects.setdefault(key, body) is not None
    )
    storage.get_object.side_effect = lambda key: objects.get(key)
    updated = repo.update_status_with_ext(
        current.id,
        PublishStatus.DRAFT,
        {**current.ext, "config_artifact": {"data": "x" * 62000}},
        source_status=PublishStatus.DRAFT,
    )
    online = _start_online(repo, updated)
    storage.put_object.reset_mock()
    completed = repo.compare_and_set_status_with_ext(
        publish_id=current.id,
        source_status=PublishStatus.ONLINE_PUB,
        target_status=PublishStatus.SUCCESS,
        expected_ext=online.ext,
        ext={**online.ext, "config_artifact": {"data": "y" * 62000}},
        observed_workflow_id=((online.ext or {}).get("publish") or {}).get("online", 77),
    )
    assert completed.status == PublishStatus.SUCCESS
    assert storage.put_object.call_count == 1


def test_online_changed_artifact_upload_failure_does_not_commit(publication):
    from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
        _start_online,
    )

    db, repo, current = publication
    objects = {}
    storage = repo._offload._oss
    storage.put_object.side_effect = lambda key, body: (
        objects.setdefault(key, body) is not None
    )
    storage.get_object.side_effect = lambda key: objects.get(key)
    updated = repo.update_status_with_ext(
        current.id,
        PublishStatus.DRAFT,
        {**current.ext, "config_artifact": {"data": "x" * 62000}},
        source_status=PublishStatus.DRAFT,
    )
    online = _start_online(repo, updated)
    storage.put_object.side_effect = lambda _key, _body: False
    with pytest.raises(RuntimeError, match="offload failed"):
        repo.compare_and_set_status_with_ext(
            publish_id=current.id,
            source_status=PublishStatus.ONLINE_PUB,
            target_status=PublishStatus.SUCCESS,
            expected_ext=online.ext,
            ext={**online.ext, "config_artifact": {"data": "y" * 62000}},
            observed_workflow_id=((online.ext or {}).get("publish") or {}).get("online", 77),
        )
    assert repo.get_by_id(current.id).status == PublishStatus.ONLINE_PUB
    with db.orm_session() as session:
        assert (
            not session.query(Binding).filter(Binding.online_enabled.is_(True)).count()
        )
