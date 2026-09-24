"""TaskArtifact 领域模型纯领域测试(PR1:kind-tagged union 红线 / roundtrip / 工厂)。

覆盖 spec(2026-09-23-task-artifact-manifest)不变量 I1-I4 的领域强制部分;
repository/service/HTTP 层见对应 PR 的测试文件。
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.task.domain.errors import TaskArtifactContentError
from agentclaw.community.core.task.task_context.task_artifact.models import (
    ArtifactContentKind,
    ArtifactKind,
    ArtifactLineage,
    ArtifactScope,
    FileArtifactContent,
    TaskArtifact,
    TextArtifactContent,
    artifact_from_dict,
    from_content_dict,
    new_artifact_id,
)


def _text_artifact(**kw) -> TaskArtifact:
    base = dict(
        artifact_id="art_test1",
        kind=ArtifactKind.NODE_RESULT,
        scope=ArtifactScope(task_id="t1", node_id="n1", attempt=2),
        content=TextArtifactContent(text="# 报告\n正文", media_type="text/markdown"),
        lineage=ArtifactLineage(derived_from=[], supersedes="art_prev"),
        created_by="bot-reporter",
        created_at=1790000000000,
    )
    base.update(kw)
    return TaskArtifact(**base)


# ---------------------------------------------------------------------------
# id 工厂(I1)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_new_artifact_id_shape_and_uniqueness():
    a, b = new_artifact_id(), new_artifact_id()
    assert a.startswith("art_") and len(a) == len("art_") + 32  # 对齐 sr_ 先例
    assert a != b


# ---------------------------------------------------------------------------
# to_dict / from_dict roundtrip(I1/I2/I4)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_text_artifact_roundtrip_preserves_all_fields():
    art = _text_artifact()
    rt = artifact_from_dict(art.to_dict())
    assert rt.artifact_id == "art_test1"
    assert rt.kind == ArtifactKind.NODE_RESULT
    assert rt.kind == "node_result"                  # StrEnum 直比 str
    assert rt.scope == ArtifactScope(task_id="t1", node_id="n1", attempt=2)
    assert isinstance(rt.content, TextArtifactContent)
    assert rt.content.text == "# 报告\n正文"
    assert rt.content.media_type == "text/markdown"
    assert rt.lineage.supersedes == "art_prev"
    assert rt.created_by == "bot-reporter"
    assert rt.created_at == 1790000000000


@pytest.mark.unit
def test_file_artifact_roundtrip_preserves_optional_integrity_fields():
    art = _text_artifact(
        kind=ArtifactKind.NODE_RESULT,
        content=FileArtifactContent(
            resource_id="sr_abc123", file_name="report.pdf",
            media_type="application/pdf", size_bytes=183420,
            sha256="sha256:" + "a" * 64,
        ),
    )
    rt = artifact_from_dict(art.to_dict())
    assert isinstance(rt.content, FileArtifactContent)
    assert rt.content.resource_id == "sr_abc123"
    assert rt.content.file_name == "report.pdf"
    assert rt.content.size_bytes == 183420
    assert rt.content.sha256 == "sha256:" + "a" * 64
    # 可空元数据缺省回程
    art_min = _text_artifact(content=FileArtifactContent(
        resource_id="sr_x", file_name=""))
    rt_min = artifact_from_dict(art_min.to_dict())
    assert rt_min.content.size_bytes is None
    assert rt_min.content.sha256 is None
    assert rt_min.content.media_type == "application/octet-stream"


@pytest.mark.lineage
@pytest.mark.unit
def test_lineage_defaults_are_empty_not_none():
    art = _text_artifact(lineage=ArtifactLineage())
    rt = artifact_from_dict(art.to_dict())
    assert rt.lineage.derived_from == []
    assert rt.lineage.supersedes is None


# ---------------------------------------------------------------------------
# from_content_dict 红线(I2:未知 kind 必须炸,脏值不得静默进领域)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_from_content_dict_rejects_unknown_kind():
    with pytest.raises(TaskArtifactContentError, match="unknown artifact content kind"):
        from_content_dict({"kind": "hologram", "text": "x"})
    with pytest.raises(TaskArtifactContentError, match="unknown artifact content kind"):
        from_content_dict({})           # 缺 kind 键
    with pytest.raises(TaskArtifactContentError, match="not a dict"):
        from_content_dict("not-a-dict")  # 非形态


@pytest.mark.unit
def test_from_content_dict_rejects_file_branch_missing_resource_id():
    with pytest.raises(TaskArtifactContentError, match="missing resource_id"):
        from_content_dict({"kind": "file", "file_name": "a.pdf"})
    with pytest.raises(TaskArtifactContentError, match="missing resource_id"):
        from_content_dict({"kind": "file", "resource_id": ""})


@pytest.mark.unit
def test_artifact_from_dict_rejects_unknown_artifact_kind():
    with pytest.raises(TaskArtifactContentError, match="unknown artifact kind"):
        artifact_from_dict({"kind": "mystery", "scope": {}, "content": {}})


# ---------------------------------------------------------------------------
# tagged-union 的持久化形态(I2:分派锚必须是 "kind" 键)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_content_dict_is_kind_tagged_and_exclusive():
    d = _text_artifact().content_to_dict()
    assert d["kind"] == ArtifactContentKind.TEXT.value == "text"
    assert set(d) == {"kind", "text", "media_type"}
    fd = _text_artifact(content=FileArtifactContent(
        resource_id="sr_1", file_name="a")).content_to_dict()
    assert fd["kind"] == "file"
    assert set(fd) == {"kind", "resource_id", "file_name",
                       "media_type", "size_bytes", "sha256"}
    # 分支字段零交集 → 互斥由形状保证,不存在双可空 payload 组合


# ---------------------------------------------------------------------------
# 双轴分离(I3)+ 版本分治(I4)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_kind_and_media_type_are_orthogonal_axes():
    assert ArtifactKind.NODE_RESULT == "node_result"          # 业务轴
    assert ArtifactContentKind.FILE == "file"                 # 内容技术轴
    # 同一业务用途可以有不同 media_type(构造自由),枚举不越界互染
    art_md = _text_artifact(content=TextArtifactContent(
        text="x", media_type="text/markdown"))
    art_txt = _text_artifact(content=TextArtifactContent(
        text="x", media_type="text/plain"))
    assert art_md.kind == art_txt.kind
    assert art_md.content.media_type != art_txt.content.media_type


@pytest.mark.unit
def test_scope_attempt_survives_roundtrip_and_defaults_zero():
    assert _text_artifact().scope.attempt == 2
    art_no_attempt = TaskArtifact(
        artifact_id="art_t2", kind=ArtifactKind.INTERNAL_CONTROL,
        scope=ArtifactScope(task_id="t1", node_id="n1"),
        content=TextArtifactContent(text="skipped"),
    )
    assert art_no_attempt.scope.attempt == 0
    rt = artifact_from_dict(art_no_attempt.to_dict())
    assert rt.scope.attempt == 0
    assert rt.created_at == 0 and rt.created_by is None  # 审计缺省合法域态


# ---------------------------------------------------------------------------
# kind 闭合治理(I1:新用途必须扩枚举)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_artifact_kind_is_closed_governed_enum():
    assert {m.value for m in ArtifactKind} == {
        "node_result", "graph_rollup", "internal_control"}
    assert {m.value for m in ArtifactContentKind} == {"text", "file"}
    with pytest.raises(ValueError):
        ArtifactKind("brand_new_kind")  # 脏值构造即拒