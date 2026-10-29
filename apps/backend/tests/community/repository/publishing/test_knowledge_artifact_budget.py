"""Combined artifact/snapshot budget and lock-free upload admission."""

from contextlib import contextmanager

import pytest

from agentclaw.community.core.service_bot.repository.config_artifact_offload import ConfigArtifactOffloader
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from tests.community.core.service_bot.repository.test_config_artifact_offload import _FakeOSS
from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
    Binding, publication as publication_fixture,
)
from tests.community.repository.publishing.test_knowledge_short_transaction import finish

publication = publication_fixture


def configure(publication, *, multibyte=False):
    db, repo, record = publication
    ids = [f'{i:03d}' + ('😀' if multibyte else 'a') * 125 for i in range(100)]
    with db.orm_session() as session:
        session.query(Binding).delete()
        session.add_all([Binding(bot_pk=1, space_id=9, env='dev', kb_id=kb,
            enabled=True, online_enabled=False, revision=1, updated_by='owner') for kb in ids])
    storage = _FakeOSS()
    repo._offload = ConfigArtifactOffloader(storage)
    return db, repo, record, ids, storage


@pytest.mark.parametrize('existing_artifact', [False, True])
@pytest.mark.parametrize('multibyte', [False, True])
def test_subthreshold_artifact_and_max_selection_publish_together(
    publication, existing_artifact, multibyte,
):
    db, repo, record, ids, storage = configure(publication, multibyte=multibyte)
    artifact = {'data': 'x' * 60000}
    if existing_artifact:
        repo.update_status_with_ext(record.id, PublishStatus.DRAFT,
            {'config_artifact': artifact}, PublishStatus.DRAFT)
    building = repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
    assert building.ext['knowledge_selection_snapshot']['enabled_kb_ids'] == ids
    built = repo.compare_and_set_built_with_ext(publish_id=record.id,
        source_status=PublishStatus.BUILDING, target_status=PublishStatus.BUILT,
        expected_ext=building.ext, ext={**building.ext, 'config_artifact': artifact},
        center_skill_uuids=(), env='dev')
    online = repo.update_status_with_ext(record.id, PublishStatus.ONLINE_PUB,
        {**built.ext, 'publish': {'online': 77}}, PublishStatus.BUILT)
    completed = finish(repo, online)
    assert completed.ext['knowledge_selection_result']['status'] == 'applied'
    assert completed.ext['config_artifact'] == artifact
    with db.orm_session() as session:
        assert {row.kb_id for row in session.query(Binding).filter_by(online_enabled=True)} == set(ids)
    assert storage.put_calls == 1


def test_build_upload_runs_outside_locks_and_failure_does_not_freeze(publication):
    db, repo, record, _ids, storage = configure(publication)
    repo.update_status_with_ext(record.id, PublishStatus.DRAFT,
        {'config_artifact': {'data': 'x' * 60000}}, PublishStatus.DRAFT)
    active = [False]
    original = db.transactional_orm_session

    @contextmanager
    def tracked():
        with original() as session:
            active[0] = True
            try:
                yield session
            finally:
                active[0] = False

    db.transactional_orm_session = tracked

    def failing_put(*_args):
        assert not active[0], 'OSS PUT under publication locks'
        raise RuntimeError('storage unavailable')

    storage.put_object = failing_put
    with pytest.raises(RuntimeError, match='storage unavailable'):
        repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
    unchanged = repo.get_by_id(record.id)
    assert unchanged.status == PublishStatus.DRAFT
    assert 'knowledge_selection_snapshot' not in unchanged.ext


@pytest.mark.parametrize('race', ['status', 'ext', 'selection', 'delete'])
def test_build_rechecks_state_and_selection_after_artifact_upload(publication, race):
    db, repo, record, ids, storage = configure(publication)
    repo.update_status_with_ext(record.id, PublishStatus.DRAFT,
        {'config_artifact': {'data': 'x' * 60000}}, PublishStatus.DRAFT)
    put = storage.put_object
    raced = [False]

    def racing_put(key, body):
        if not raced[0]:
            raced[0] = True
            if race == 'status':
                repo.update_status(record.id, PublishStatus.FAILED, PublishStatus.DRAFT)
            elif race == 'ext':
                old = repo.get_by_id(record.id)
                repo.compare_and_set_ext(publish_id=record.id, expected_ext=old.ext,
                    ext={**old.ext, 'concurrent': 'keep'})
            elif race == 'selection':
                with db.orm_session() as session:
                    session.query(Binding).filter_by(kb_id=ids[0]).update({'enabled': False})
            else:
                repo.delete(record.id)
        return put(key, body)

    storage.put_object = racing_put
    result = repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
    if race == 'status':
        assert result is None
        assert repo.get_by_id(record.id).status == PublishStatus.FAILED
    elif race == 'delete':
        assert result is None
        assert not storage.store
    else:
        assert result.status == PublishStatus.BUILDING
        if race == 'ext':
            assert result.ext['concurrent'] == 'keep'
        else:
            assert result.ext['knowledge_selection_snapshot']['enabled_kb_ids'] == ids[1:]


@pytest.mark.parametrize('method', ['status_ext', 'cas'])
def test_other_build_entrances_freeze_selection_with_native_ext_cas(publication, method):
    _db, repo, record = publication
    incoming = {**record.ext, 'retry': True}
    if method == 'status_ext':
        result = repo.update_status_with_ext(record.id, PublishStatus.BUILDING,
            incoming, PublishStatus.DRAFT)
    else:
        result = repo.compare_and_set_status_with_ext(publish_id=record.id,
            source_status=PublishStatus.DRAFT, target_status=PublishStatus.BUILDING,
            expected_ext=record.ext, ext=incoming)
    assert result.ext['knowledge_selection_snapshot']['enabled_kb_ids'] == ['B']
    assert result.ext['retry'] is True


def test_stale_build_ext_cas_does_not_freeze_or_upload(publication):
    _db, repo, record, _ids, storage = configure(publication)
    current = repo.compare_and_set_ext(publish_id=record.id,
        expected_ext=record.ext, ext={**record.ext, 'concurrent': True})
    assert repo.compare_and_set_status_with_ext(publish_id=record.id,
        source_status=PublishStatus.DRAFT, target_status=PublishStatus.BUILDING,
        expected_ext=record.ext, ext={'config_artifact': {'data': 'x' * 60000}}) is None
    assert repo.get_by_id(record.id).ext == current.ext
    assert storage.put_calls == 0


def test_legacy_ext_writer_budgets_preserved_snapshot_before_staging(publication):
    _db, repo, record, ids, storage = configure(publication)
    building = repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
    # Legacy callers omit protected metadata; the repository must include it in
    # the pre-lock budget rather than write a marker whose object was never PUT.
    result = repo.update_status_with_ext(record.id, PublishStatus.BUILT,
        {'config_artifact': {'data': 'x' * 60000}}, PublishStatus.BUILDING)
    assert result.ext['config_artifact'] == {'data': 'x' * 60000}
    assert result.ext['knowledge_selection_snapshot'] == building.ext['knowledge_selection_snapshot']
    assert result.ext['knowledge_selection_snapshot']['enabled_kb_ids'] == ids
    assert storage.put_calls == 1


def test_completion_result_crossing_text_budget_stages_artifact_first(publication):
    import json

    db, repo, record, _ids, storage = configure(publication)
    building = repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
    ext = {**building.ext, 'publish': {'online': 77}, 'config_artifact': {'data': ''}}
    empty_size = len(json.dumps(ext, ensure_ascii=False).encode())
    ext['config_artifact']['data'] = 'x' * (65500 - empty_size)
    online = repo.update_status_with_ext(record.id, PublishStatus.ONLINE_PUB,
        ext, PublishStatus.BUILDING)
    assert storage.put_calls == 0
    completed = finish(repo, online)
    assert completed.status == PublishStatus.SUCCESS
    assert completed.ext['config_artifact'] == ext['config_artifact']
    assert completed.ext['knowledge_selection_result']['status'] == 'applied'
    assert storage.put_calls == 1


def test_incoming_build_ext_does_not_overwrite_metadata_changed_during_put(publication):
    _db, repo, record, _ids, storage = configure(publication)
    incoming = {**record.ext, 'config_artifact': {'data': 'x' * 60000}}
    put = storage.put_object

    def racing_put(key, body):
        old = repo.get_by_id(record.id)
        repo.compare_and_set_ext(publish_id=record.id, expected_ext=old.ext,
            ext={**old.ext, 'concurrent': 'keep'})
        return put(key, body)

    storage.put_object = racing_put
    assert repo.update_status_with_ext(record.id, PublishStatus.BUILDING,
        incoming, PublishStatus.DRAFT) is None
    unchanged = repo.get_by_id(record.id)
    assert unchanged.status == PublishStatus.DRAFT
    assert unchanged.ext['concurrent'] == 'keep'


def test_redraft_smaller_selection_keeps_current_artifact_ext_cas_working(publication):
    db, repo, record, ids, storage = configure(publication)
    repo.update_status_with_ext(record.id, PublishStatus.DRAFT,
        {'config_artifact': {'data': 'x' * 60000}}, PublishStatus.DRAFT)
    first = repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
    assert storage.put_calls == 1
    repo.update_status(record.id, PublishStatus.DRAFT, PublishStatus.BUILDING)
    with db.orm_session() as session:
        session.query(Binding).filter_by(bot_pk=1).update({'enabled': False})
        session.query(Binding).filter_by(kb_id=ids[0]).update({'enabled': True})
    second = repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
    assert second.ext['knowledge_selection_snapshot']['enabled_kb_ids'] == ids[:1]
    assert second.ext['knowledge_selection_snapshot']['generation'] != first.ext['knowledge_selection_snapshot']['generation']
    updated = repo.compare_and_set_ext(publish_id=record.id, expected_ext=second.ext,
        ext={**second.ext, 'progress': 'building'})
    assert updated is not None
    assert updated.ext['config_artifact'] == {'data': 'x' * 60000}


def test_protected_metadata_race_cannot_commit_an_unstaged_marker(publication, monkeypatch):
    from agentclaw.community.kernel.knowledge import KnowledgeError

    _db, repo, record, ids, storage = configure(publication)
    original_stage = repo._stage_artifact

    def stage_then_race(*args, **kwargs):
        staged = original_stage(*args, **kwargs)
        # A real, independent admission adds protected metadata after preflight.
        repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
        return staged

    monkeypatch.setattr(repo, '_stage_artifact', stage_then_race)
    with pytest.raises(KnowledgeError, match='KNOWLEDGE_ARTIFACT_STAGE_CHANGED'):
        repo.update_status_with_ext(record.id, PublishStatus.FAILED,
            {'config_artifact': {'data': 'x' * 60000}})
    current = repo.get_by_id(record.id)
    assert current.status == PublishStatus.BUILDING
    assert current.ext['knowledge_selection_snapshot']['enabled_kb_ids'] == ids
    assert 'config_artifact' not in current.ext
    assert not storage.store
