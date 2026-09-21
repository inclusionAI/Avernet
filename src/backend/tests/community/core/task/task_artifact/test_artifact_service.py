"""ArtifactService 测试(真实 SQLite 仓储 + 就绪校验替身)—— 对齐语雀
《BCN 产物领域对象设计》§7(幂等/immutable+supersedes)、§9(File 就绪不变量)、
§4.4(血缘解析)。"""
import pytest

from agentclaw.community.core.repository.implementations.task.artifact_repository import (
    TaskArtifactRepository,
)
from agentclaw.community.core.task.domain.artifact import (
    ActorRef,
    ActorType,
    ArtifactLineage,
    ArtifactScope,
    TextContent,
)
from agentclaw.community.core.task.domain.errors import (
    ArtifactFileNotReadyError,
    ArtifactReferenceError,
)
from agentclaw.community.core.task.task_artifact import ArtifactService


class _FakeReadiness:
    def __init__(self, ready_ids: set[str]) -> None:
        self._ready = set(ready_ids)

    def is_ready(self, file_id: str) -> bool:
        return file_id in self._ready


@pytest.fixture
def service(engine, db):
    repo = TaskArtifactRepository(db)
    return ArtifactService(
        repository=repo, readiness=_FakeReadiness({"sr_ready"}), clock=lambda: 1780000000000
    )


def _scope(task_id="T-1", node_id="N-1", attempt=0) -> ArtifactScope:
    return ArtifactScope(
        task_id=task_id, node_id=node_id, session_id="s_1", run_id="1", attempt=attempt
    )


def _actor() -> ActorRef:
    return ActorRef(actor_type=ActorType.BOT, actor_id="B-1:U1")


def test_publish_text_is_deterministic_and_idempotent(service):
    a = service.publish_text(scope=_scope(), text="报告正文", created_by=_actor())
    b = service.publish_text(scope=_scope(), text="报告正文", created_by=_actor())
    assert a.artifact_id == b.artifact_id  # 同 scope+kind+内容 → 同 id,重放幂等
    assert service.get(a.artifact_id).content.text == "报告正文"
    # 内容/api 变化 → 自然派生新 id
    c = service.publish_text(
        scope=_scope(), text="报告正文 v2", created_by=_actor(), supersedes=a.artifact_id
    )
    assert c.artifact_id != a.artifact_id
    assert c.supersedes == a.artifact_id


def test_publish_file_requires_ready_session_resource(service):
    with pytest.raises(ArtifactFileNotReadyError):
        service.publish_file(
            scope=_scope(), file_id="sr_pending", file_name="a.pdf",
            media_type="application/pdf", size_bytes=10, created_by=_actor(),
        )
    ok = service.publish_file(
        scope=_scope(), file_id="sr_ready", file_name="a.pdf",
        media_type="application/pdf", size_bytes=10, created_by=_actor(),
    )
    assert ok.content.kind == "file"


def test_publish_file_fails_closed_without_readiness_checker(db):
    repo = TaskArtifactRepository(db)
    svc = ArtifactService(repository=repo, clock=lambda: 1)  # readiness=None
    with pytest.raises(ArtifactFileNotReadyError):
        svc.publish_file(
            scope=_scope(), file_id="sr_ready", file_name="a.pdf",
            media_type="application/pdf", size_bytes=10, created_by=_actor(),
        )


def test_publish_collection_validates_items_exist(service):
    src = service.publish_text(scope=_scope(node_id="src"), text="源", created_by=_actor())
    with pytest.raises(ArtifactReferenceError):
        service.publish_collection(
            scope=_scope(), items=["art_missing"], created_by=_actor()
        )
    coll = service.publish_collection(
        scope=_scope(), items=[src.artifact_id], created_by=_actor()
    )
    assert coll.content.items == (src.artifact_id,)


def test_publish_rejects_unknown_supersedes(service):
    with pytest.raises(ArtifactReferenceError):
        service.publish_text(
            scope=_scope(), text="正文", created_by=_actor(), supersedes="art_none"
        )


def test_supersede_creates_new_id_and_keeps_old_as_audit(service):
    v1 = service.publish_text(scope=_scope(), text="v1", created_by=_actor())
    v2 = service.supersede(v1.artifact_id, content=TextContent(text="v2"))
    assert v2.supersedes == v1.artifact_id
    assert v2.artifact_id != v1.artifact_id
    # 旧行保留(审计/历史回放),不可变
    still = service.get(v1.artifact_id)
    assert still.content.text == "v1"
    with pytest.raises(ArtifactReferenceError):
        service.supersede("art_missing", content=TextContent(text="x"))


def test_supersede_chain_lineage_resolution(service):
    v1 = service.publish_text(scope=_scope(), text="v1", created_by=_actor())
    v2 = service.supersede(v1.artifact_id, content=TextContent(text="v2"))
    v3 = service.supersede(v2.artifact_id, content=TextContent(text="v3"))
    derived_src = service.publish_text(
        scope=_scope(node_id="src"), text="源数据", created_by=_actor()
    )
    v4 = service.supersede(
        v3.artifact_id,
        content=TextContent(text="v4"),
        lineage=ArtifactLineage(derived_from=(derived_src.artifact_id,)),
    )
    resolved = service.resolve_lineage(v4.artifact_id)
    assert [a.content.text for a in resolved["chain"]] == ["v4", "v3", "v2", "v1"]
    assert [a.artifact_id for a in resolved["derived_from"]] == [derived_src.artifact_id]


def test_listing_scopes_and_latest(service):
    service.publish_text(scope=_scope(node_id="N-1", attempt=0), text="a", created_by=_actor())
    service.publish_text(scope=_scope(node_id="N-1", attempt=1), text="b", created_by=_actor())
    service.publish_text(scope=_scope(node_id="N-2"), text="c", created_by=_actor())
    assert len(service.list_by_task("T-1")) == 3
    assert len(service.list_by_node("T-1", "N-1")) == 2
    latest = service.latest_by_node("T-1", "N-1")
    assert latest is not None and latest.content.text == "b"
    by_attempt = service.list_by_node("T-1", "N-1", attempt=1)
    assert [a.content.text for a in by_attempt] == ["b"]


def test_scope_attempt_variance_keeps_retry_artifacts_distinct(service):
    """文档 §4.3:attempt 显式入 scope — 节点重试不覆盖上一 attempt 的产物。"""
    first = service.publish_text(scope=_scope(attempt=0), text="首轮产出", created_by=_actor())
    retried = service.publish_text(scope=_scope(attempt=1), text="首轮产出", created_by=_actor())
    assert first.artifact_id != retried.artifact_id
    assert first.scope.attempt == 0 and retried.scope.attempt == 1
    assert service.latest_by_node("T-1", "N-1").artifact_id == retried.artifact_id