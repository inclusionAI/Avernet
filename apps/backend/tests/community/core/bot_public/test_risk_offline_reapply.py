import json
from contextlib import contextmanager
from dataclasses import replace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_public_risk.repository import (
    BotPublicRiskRepository, RequestConflict, RiskCheck, RiskCheckModel,
)
from agentclaw.community.plugin_api.models import BotModel


class Database:
    def __init__(self):
        self.engine = create_engine("sqlite:///:memory:")
        BotModel.__table__.create(self.engine)
        RiskCheckModel.__table__.create(self.engine)
        self.sessions = sessionmaker(bind=self.engine)

    @contextmanager
    def transactional_orm_session(self):
        with self.sessions.begin() as session:
            yield session

    orm_session = transactional_orm_session


@pytest.fixture
def offline():
    db = Database()
    with db.transactional_orm_session() as session:
        session.add(BotModel(id=7, bot_id="bot", bot_name="bot", entity_id="owner",
                             entity_type="staff", creator_id="owner", owner_id="owner",
                             env="dev", public="0", is_delete=0,
                             ext=json.dumps({"public_approval": {"puid": "puid-a", "status": "PROCESSING"}})))
    repo = BotPublicRiskRepository(db)
    old = RiskCheck(risk_check_id="old", env="dev", bot_pk=7, owner_id="owner",
                    operator_id="owner", client_scope="legacy:owner", client_request_id="old-request",
                    public_scope="legacy", target_json="{}", request_hash="old-hash",
                    input_json="{}", input_hash="hash", approval_biz_id="biz-old")
    saved, _ = repo.create_or_replay(old)
    assert repo.update_current(env="dev", risk_check_id="old", row_version=saved.row_version,
                               changes={"phase": "NEEDS_ATTENTION", "approval_puid": "puid-a",
                                        "workflow_json": json.dumps({"error": "CALLBACK_DEVICE_OFFLINE_REAPPLY",
                                            "callback_claim": {"status": "unknown", "claim_id": "claim",
                                                               "event": {"puid": "puid-a", "decision": "AGREE"}}})})
    return repo, repo.get(env="dev", risk_check_id="old")


def test_reapply_is_conditional_and_retries_reuse_the_same_request(offline):
    repo, old = offline
    new = replace(old, risk_check_id="new", client_request_id="new-request", request_hash="new-hash",
                  phase="PREPARING", approval_puid=None, approval_biz_id="biz-new")
    result, created = repo.reapply_offline(new, expected_risk_id="old", expected_version=old.row_version)
    assert created and result.risk_check_id == "new"
    assert repo.get(env="dev", risk_check_id="old").phase == "CLOSED"
    replay, created = repo.reapply_offline(new, expected_risk_id="old", expected_version=old.row_version)
    assert not created and replay.risk_check_id == "new"
    with pytest.raises(RequestConflict):
        repo.reapply_offline(replace(new, risk_check_id="other", client_request_id="other-request"),
                             expected_risk_id="old", expected_version=old.row_version)
    assert repo.get_current(env="dev", bot_pk=7, public_scope="legacy").risk_check_id == "new"


def test_reapply_rejects_wrong_owner_stale_version_and_unknown_outcome(offline):
    repo, old = offline
    new = replace(old, risk_check_id="new", client_request_id="new-request", request_hash="new-hash")
    with pytest.raises(RequestConflict):
        repo.reapply_offline(replace(new, operator_id="admin"), expected_risk_id="old", expected_version=old.row_version)
    with pytest.raises(RequestConflict):
        repo.reapply_offline(new, expected_risk_id="old", expected_version=old.row_version - 1)
    assert repo.update_current(env="dev", risk_check_id="old", row_version=old.row_version,
                               changes={"workflow_json": json.dumps({"error": "CALLBACK_OUTCOME_UNKNOWN"})})
    with pytest.raises(RequestConflict):
        repo.reapply_offline(new, expected_risk_id="old", expected_version=old.row_version + 1)
    assert repo.get_current(env="dev", bot_pk=7, public_scope="legacy").risk_check_id == "old"
