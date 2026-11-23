"""Real DI calls the selected Plugin after committing the native publication."""
from injector import InstanceProvider

from agentclaw.community.core.repository.protocols.publishing import BotPublishRepositoryProtocol
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugin_api.release_audit_models import ReleaseAuditModel
from agentclaw.community.plugin_api.service_bot_release_audit import ServiceBotReleaseAuditPlugin
from agentclaw.community.utils.env_utils import get_current_env


def test_repository_uses_selected_plugin_and_isolates_its_failure(world):
    selected = world.get(ServiceBotReleaseAuditPlugin)
    sessions = []
    fail = False

    class RecordingAudit:
        def create(self, session, snapshot):
            sessions.append(session)
            selected.create(session, snapshot)
            if fail:
                raise RuntimeError("audit insert failed")

        def complete(self, session, **kwargs):
            sessions.append(session)
            return selected.complete(session, **kwargs)

    world.injector.binder.bind(ServiceBotReleaseAuditPlugin, to=InstanceProvider(RecordingAudit()))
    repo = world.get(BotPublishRepositoryProtocol)
    db = world.get(DatabasePlugin)
    with db.orm_session() as session:
        bot = BotModel(bot_id="audit-contract", bot_name="Audit", entity_id="audit-owner", entity_type="staff",
                       creator_id="creator", owner_id="owner", env=get_current_env(),
                       bot_type="service", status="ACTIVE", active_engine="teclaw")
        session.add(bot)
        session.flush()
        bot_pk = bot.id
    publication = repo.insert(dict(source_bot_pk=bot_pk, source_bot_id="audit-contract",
                                   publish_bot_id="audit-contract", name="Audit", owner_id="audit-owner",
                                   permission_owner="owner", status="draft", version=1,
                                   audit_operator_id="editor"))
    submission = repo.update_status(publication.id, "building", "draft", audit_operator_id="submitter")
    repo.compare_and_set_status_with_ext(publish_id=publication.id, source_status="building",
                                        target_status="failed", expected_ext=submission.ext, ext=submission.ext)
    assert len(sessions) == 3
    with db.orm_session() as session:
        rows = session.query(ReleaseAuditModel).order_by(ReleaseAuditModel.id).all()
        assert len(rows) == 2
        assert rows[-1].operator_id == "submitter" and rows[-1].finish_status == "failed"
    fail = True
    retried = repo.update_status(publication.id, "building", "failed", audit_operator_id="retry")
    assert retried.status == "building"
    assert retried.ext == repo.get_by_id(publication.id).ext
    with db.orm_session() as session:
        assert session.query(ReleaseAuditModel).count() == 2
