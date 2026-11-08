"""Real TEXT CAS regressions for the Bot repository's Unicode encoding."""

import json
import logging

import pytest

from agentclaw.community.plugin_api.models import BotModel
from tests.community.repository.bot.test_bot_repository_unified import (
    _data,
    db as db,
    repo as repo,
)
from tests.community.repository.publishing import test_bot_publish_unified as publishing_tests

pytestmark = pytest.mark.integration
publish_repo = publishing_tests.repo


def _raw_ext(db, bot_pk):
    with db.orm_session() as session:
        return session.query(BotModel.ext).filter(BotModel.id == bot_pk).scalar()


def test_cas_matches_unescaped_unicode_text_and_preserves_concurrent_changes(repo, db):
    snapshot = {"中文键": "中文值", "owner": "first"}
    bot = repo.insert(_data())
    with db.orm_session() as session:
        session.query(BotModel).filter(BotModel.id == bot["id"]).update(
            {BotModel.ext: json.dumps(snapshot, ensure_ascii=False)},
            synchronize_session=False,
        )
    expected = repo.get_by_id_and_owner("bot-1", "emp1")["ext"]
    replacement = {**expected, "sbot_use_default_image": True, "新增": "保留"}

    updated = repo.compare_and_set_ext(
        bot_id="bot-1", owner_id="emp1", expected_ext=expected, ext=replacement
    )

    assert updated is not None
    assert updated["ext"] == replacement
    assert _raw_ext(db, bot["id"]) == json.dumps(replacement, ensure_ascii=False)
    assert repo.compare_and_set_ext(
        bot_id="bot-1", owner_id="emp1", expected_ext=expected, ext={"stale": True}
    ) is None
    assert repo.get_by_id_and_owner("bot-1", "emp1")["ext"] == replacement


def test_cas_accepts_historically_escaped_unicode_text(repo, db):
    snapshot = {"中文键": "中文值"}
    bot = repo.insert(_data())
    historical_text = json.dumps(snapshot)
    with db.orm_session() as session:
        session.query(BotModel).filter(BotModel.id == bot["id"]).update(
            {BotModel.ext: historical_text}, synchronize_session=False
        )

    replacement = {**snapshot, "sbot_use_default_image": True}
    updated = repo.compare_and_set_ext(
        bot_id="bot-1", owner_id="emp1", expected_ext=snapshot, ext=replacement
    )

    assert updated is not None
    assert updated["ext"] == replacement
    assert _raw_ext(db, bot["id"]) == json.dumps(replacement, ensure_ascii=False)
    assert repo.compare_and_set_ext(
        bot_id="bot-1", owner_id="emp1", expected_ext=snapshot, ext={"stale": True}
    ) is None
    assert _raw_ext(db, bot["id"]) == json.dumps(replacement, ensure_ascii=False)


@pytest.mark.parametrize("snapshot", [{"ascii": "value"}, {"中文键": "中文值"}])
def test_insert_and_owner_update_keep_ext_compatible_with_cas(repo, db, snapshot):
    share_policy = {"中文": "保持原编码"}
    bot = repo.insert(_data(ext=snapshot, share_policy=share_policy))
    assert _raw_ext(db, bot["id"]) == json.dumps(snapshot)
    replacement = {**snapshot, "新增": "更新"}
    updated = repo.update_by_owner(
        "bot-1", "emp1", {"ext": replacement, "share_policy": share_policy}
    )
    assert updated["ext"] == replacement
    assert _raw_ext(db, bot["id"]) == json.dumps(replacement)
    with db.orm_session() as session:
        stored_share_policy = session.query(BotModel.share_policy).filter(
            BotModel.id == bot["id"]
        ).scalar()
    assert stored_share_policy == json.dumps(share_policy)
    assert repo.compare_and_set_ext(
        bot_id="bot-1", owner_id="emp1", expected_ext=replacement,
        ext={**replacement, "sbot_use_default_image": True},
    ) is not None


def test_null_snapshot_writes_unicode_and_rejects_non_null_current_ext(repo, db):
    bot = repo.insert(_data(ext=None))
    replacement = {"中文键": "中文值"}

    updated = repo.compare_and_set_ext(
        bot_id="bot-1", owner_id="emp1", expected_ext=None, ext=replacement
    )

    assert updated["ext"] == replacement
    assert _raw_ext(db, bot["id"]) == json.dumps(replacement, ensure_ascii=False)
    assert repo.compare_and_set_ext(
        bot_id="bot-1", owner_id="emp1", expected_ext=None, ext={"wrong": True}
    ) is None
    assert _raw_ext(db, bot["id"]) == json.dumps(replacement, ensure_ascii=False)


def test_cas_logs_only_business_identity_and_result(repo, caplog):
    secret = "not-a-real-token"
    snapshot = {"secret": secret}
    repo.insert(_data(ext=snapshot))

    with caplog.at_level(logging.INFO, logger="start"):
        updated = repo.compare_and_set_ext(
            bot_id="bot-1", owner_id="emp1", expected_ext=snapshot,
            ext={**snapshot, "sbot_use_default_image": True},
        )
        rejected = repo.compare_and_set_ext(
            bot_id="bot-1", owner_id="emp1", expected_ext=snapshot, ext={"stale": True}
        )

    assert updated is not None
    assert rejected is None
    assert "operation=compare_and_set_ext" in caplog.text
    assert "affected_rows=1" in caplog.text
    assert "affected_rows=0" in caplog.text
    assert "bot_id=bot-1" in caplog.text
    assert "expected_ext_is_null=False" in caplog.text
    assert "serialization=unicode" in caplog.text
    assert secret not in caplog.text
    assert json.dumps(snapshot) not in caplog.text


def test_draft_cas_already_preserves_unicode(publish_repo):
    snapshot = {"中文键": "中文值"}
    draft = publish_repo.insert(publishing_tests._data(ext=snapshot))

    updated = publish_repo.compare_and_set_ext(
        publish_id=draft.id, expected_ext=snapshot,
        ext={**snapshot, "sbot_use_default_image": True},
    )

    assert updated is not None
    assert updated.ext == {**snapshot, "sbot_use_default_image": True}
