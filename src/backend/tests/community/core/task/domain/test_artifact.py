"""任务产物(Artifact)领域对象测试 — 对齐语雀文档《BCN 产物领域对象设计》§4/§5。

覆盖:四分支 tagged union 往返(kind 判别)、非法 content 拒绝、不可变性、
scope/lineage/supersedes 默认值与畸形拒绝(artifact_from_dict 集中校验)。
"""
import pytest

from agentclaw.community.core.task.domain.artifact import (
    ActorRef,
    ActorType,
    Artifact,
    ArtifactContent,
    ArtifactKind,
    ArtifactLineage,
    ArtifactScope,
    CollectionContent,
    ContentDigest,
    DigestAlgorithm,
    FileContent,
    SchemaRef,
    StructuredContent,
    TextContent,
    artifact_from_dict,
    content_from_dict,
)
from agentclaw.community.core.task.domain.errors import (
    ArtifactContentError,
    ArtifactError,
)


def _scope() -> ArtifactScope:
    return ArtifactScope(
        task_id="task-1",
        node_id="generate_report",
        session_id="s_1",
        run_id="1",
        attempt=1,
    )


def _actor() -> ActorRef:
    return ActorRef(actor_type=ActorType.BOT, actor_id="reporter:U1")


def test_content_kinds_are_snake_case_and_exclusive():
    assert TextContent.kind == "text"
    assert StructuredContent.kind == "structured"
    assert FileContent.kind == "file"
    assert CollectionContent.kind == "collection"
    # 四分支互斥:kind 值两两不同
    kinds = {TextContent.kind, StructuredContent.kind, FileContent.kind, CollectionContent.kind}
    assert len(kinds) == 4


def test_text_content_to_dict_matches_doc_example():
    d = TextContent(text="# 分析报告\n...", media_type="text/markdown").to_dict()
    assert d == {"kind": "text", "text": "# 分析报告\n...", "media_type": "text/markdown"}


def test_structured_content_roundtrip_with_schema_ref():
    content = StructuredContent(
        value={"level": "medium", "score": 62},
        schema_ref=SchemaRef(schema_id="risk-assessment", schema_version="1.0"),
    )
    parsed = content_from_dict(content.to_dict())
    assert parsed == content  # frozen dataclass 值相等
    assert parsed.schema_ref is not None
    assert parsed.schema_ref.schema_id == "risk-assessment"


def test_file_content_omits_none_digest_and_roundtrips():
    content = FileContent(
        file_id="sr_abc",
        file_name="analysis-report.pdf",
        media_type="application/pdf",
        size_bytes=183420,
    )
    d = content.to_dict()
    assert "digest" not in d  # None digest 不进 wire shape
    assert content_from_dict(d) == content

    with_digest = FileContent(
        file_id="sr_abc",
        file_name="a.pdf",
        media_type="application/pdf",
        size_bytes=1,
        digest=ContentDigest(algorithm=DigestAlgorithm.SHA256, value="abcd..."),
    )
    dd = with_digest.to_dict()
    assert dd["digest"] == {"algorithm": "sha256", "value": "abcd..."}
    assert content_from_dict(dd) == with_digest


def test_collection_content_roundtrip():
    content = CollectionContent(items=("art_1", "art_2"))
    parsed = content_from_dict(content.to_dict())
    assert isinstance(parsed, CollectionContent)
    assert parsed.items == ("art_1", "art_2")


def test_content_from_dict_rejects_illegal_payloads():
    with pytest.raises(ArtifactContentError):
        content_from_dict("text")  # 非 dict
    with pytest.raises(ArtifactContentError):
        content_from_dict({"text": "hi"})  # 缺判别键 kind
    with pytest.raises(ArtifactContentError):
        content_from_dict({"kind": "blob", "data": "..."})  # 未知 kind
    with pytest.raises(ArtifactContentError):
        content_from_dict({"kind": "text", "text": 123})  # text 非 str
    with pytest.raises(ArtifactContentError):
        content_from_dict({"kind": "structured", "value": "raw"})  # 非 JSON 值
    with pytest.raises(ArtifactContentError):
        content_from_dict({"kind": "file", "file_id": "sr_1"})  # 缺必填分支字段
    with pytest.raises(ArtifactContentError):
        content_from_dict({  # size_bytes 非法
            "kind": "file", "file_id": "sr_1", "file_name": "a",
            "media_type": "application/pdf", "size_bytes": -1,
        })
    with pytest.raises(ArtifactContentError):
        content_from_dict({  # digest 算法未知
            "kind": "file", "file_id": "sr_1", "file_name": "a",
            "media_type": "application/pdf", "size_bytes": 1,
            "digest": {"algorithm": "md5", "value": "x"},
        })
    with pytest.raises(ArtifactContentError):
        content_from_dict({"kind": "collection", "items": [None]})  # items 含非 str


def test_content_variants_are_frozen():
    text = TextContent(text="t")
    with pytest.raises(Exception, match="cannot assign"):
        text.text = "changed"  # type: ignore[misc]


def test_artifact_to_dict_matches_doc_shape():
    artifact = Artifact(
        artifact_id="art_01K",
        artifact_kind=ArtifactKind.ANALYSIS_REPORT,
        scope=_scope(),
        content=TextContent(text="报告正文", media_type="text/markdown"),
        created_by=_actor(),
        created_at=1780000000000,
        lineage=ArtifactLineage(derived_from=("art_source_1",)),
    )
    d = artifact.to_dict()
    assert d["artifact_id"] == "art_01K"
    assert d["artifact_kind"] == "analysis_report"
    assert d["scope"] == {
        "task_id": "task-1", "session_id": "s_1",
        "run_id": "1", "node_id": "generate_report", "attempt": 1,
    }
    assert d["content"]["kind"] == "text"
    assert d["lineage"] == {"derived_from": ["art_source_1"], "source_message_ids": []}
    assert d["supersedes"] is None
    assert d["created_by"] == {"actor_type": "bot", "actor_id": "reporter:U1"}


def test_artifact_defaults_lineage_and_supersedes():
    artifact = Artifact(
        artifact_id="art_1",
        artifact_kind=ArtifactKind.SUMMARY,
        scope=ArtifactScope(task_id="task-1", node_id="n1"),
        content=TextContent(text="t"),
        created_by=ActorRef(actor_type=ActorType.SYSTEM, actor_id="engine"),
        created_at=1780000000000,
    )
    assert artifact.lineage == ArtifactLineage()
    assert artifact.lineage.derived_from == ()
    assert artifact.supersedes is None


def test_artifact_from_dict_roundtrips_all_branches():
    for content in (
        TextContent(text="t", media_type="text/markdown"),
        StructuredContent(value={"k": [1, 2]}),
        FileContent(file_id="sr_1", file_name="a.pdf", media_type="application/pdf", size_bytes=3),
        CollectionContent(items=("art_1", "art_2")),
    ):
        artifact = Artifact(
            artifact_id="art_x",
            artifact_kind=ArtifactKind.OTHER,
            scope=_scope(),
            content=content,
            created_by=_actor(),
            created_at=1780000000000,
            supersedes="art_prev",
        )
        assert artifact_from_dict(artifact.to_dict()) == artifact


def test_artifact_is_frozen():
    artifact = Artifact(
        artifact_id="art_1",
        artifact_kind=ArtifactKind.SUMMARY,
        scope=_scope(),
        content=TextContent(text="t"),
        created_by=_actor(),
        created_at=1780000000000,
    )
    with pytest.raises(Exception, match="cannot assign"):
        artifact.supersedes = "art_2"  # type: ignore[misc]


def test_artifact_from_dict_rejects_malformed_payloads():
    good = Artifact(
        artifact_id="art_1",
        artifact_kind=ArtifactKind.SUMMARY,
        scope=_scope(),
        content=TextContent(text="t"),
        created_by=_actor(),
        created_at=1780000000000,
    ).to_dict()

    with pytest.raises(ArtifactError):
        artifact_from_dict([good])  # 非 dict
    with pytest.raises(ArtifactError):
        artifact_from_dict({**good, "artifact_kind": "unknown_kind"})  # 未知业务类型
    with pytest.raises(ArtifactError):
        artifact_from_dict({**good, "scope": {"task_id": "", "node_id": "n"}})  # 空 task_id
    with pytest.raises(ArtifactError):
        artifact_from_dict({**good, "created_by": {"actor_type": "robot", "actor_id": "x"}})
    with pytest.raises(ArtifactError):
        artifact_from_dict({**good, "created_at": "not-a-number"})
    with pytest.raises(ArtifactError):
        artifact_from_dict({**good, "lineage": {"derived_from": [1]}})


def test_artifact_kind_is_closed_enum_with_escape_hatch():
    assert {e.value for e in ArtifactKind} == {
        "analysis_report", "summary", "source_code", "chart", "dataset", "other",
    }


def test_artifact_content_subclass_contract():
    # ArtifactContent 不可直接实例化(ABC);所有分支都继承 kind/to_dict 契约
    with pytest.raises(TypeError):
        ArtifactContent()  # type: ignore[abstract]
    assert issubclass(TextContent, ArtifactContent)
    assert issubclass(StructuredContent, ArtifactContent)
    assert issubclass(FileContent, ArtifactContent)
    assert issubclass(CollectionContent, ArtifactContent)