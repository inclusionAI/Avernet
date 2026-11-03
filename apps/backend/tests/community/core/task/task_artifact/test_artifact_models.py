"""TaskArtifact 领域模型:构造校验、kind-tag 序列化、幂等 hash、文本归约(spec §3)。"""
from __future__ import annotations

import pytest

from agentclaw.community.core.task.domain.errors import TaskArtifactContentError
from agentclaw.community.core.task.task_context.task_artifact import (
    ArtifactKind,
    ArtifactScope,
    CodeContent,
    FileContent,
    StorageType,
    TaskArtifact,
    TextContent,
    UrlLinkContent,
    artifact_content_hash,
    artifact_text_value,
    content_from_json,
    content_to_json,
    new_artifact_id,
)


@pytest.fixture()
def scope() -> ArtifactScope:
    return ArtifactScope(session_id="sess-1", task_id="t-1", node_id="n-1", attempt=0)


def _artifact(content, *, scope: ArtifactScope, kind: ArtifactKind | None = None) -> TaskArtifact:
    return TaskArtifact(
        artifact_id=new_artifact_id(),
        artifact_name=None,
        kind=kind or ArtifactKind.TEXT,
        scope=scope,
        content=content,
        content_hash=artifact_content_hash(content),
        creator="bot-1",
        created_at=1730000000000,
    )


# ---------------------------------------------------------------- 构造校验


class TestFileContentValidation:
    def test_bot_workspace_requires_storage_id(self):
        with pytest.raises(TaskArtifactContentError):
            FileContent(
                storage_type=StorageType.BOT_WORKSPACE,
                storage_id="",
                file_type="text/markdown",
                file_name="report.md",
                file_id=None,
            )

    def test_bot_workspace_rejects_file_id(self):
        with pytest.raises(TaskArtifactContentError):
            FileContent(
                storage_type=StorageType.BOT_WORKSPACE,
                storage_id="reports/report.md",
                file_type="text/markdown",
                file_name="report.md",
                file_id="f-987",
            )

    def test_session_file_requires_file_id(self):
        with pytest.raises(TaskArtifactContentError):
            FileContent(
                storage_type=StorageType.SESSION_FILE,
                storage_id="sess-xxx",
                file_type="application/pdf",
                file_name="report.pdf",
                file_id=None,
            )

    def test_bot_workspace_rejects_absolute_path(self):
        with pytest.raises(TaskArtifactContentError):
            FileContent(
                storage_type=StorageType.BOT_WORKSPACE,
                storage_id="/home/admin/workspace/report.md",
                file_type="text/markdown",
                file_name="report.md",
                file_id=None,
            )

    def test_bot_workspace_rejects_parent_escape(self):
        with pytest.raises(TaskArtifactContentError):
            FileContent(
                storage_type=StorageType.BOT_WORKSPACE,
                storage_id="reports/../../etc/passwd",
                file_type="text/markdown",
                file_name="report.md",
                file_id=None,
            )

    def test_bot_workspace_rejects_overlong_path(self):
        with pytest.raises(TaskArtifactContentError):
            FileContent(
                storage_type=StorageType.BOT_WORKSPACE,
                storage_id="a" * 513,
                file_type="text/markdown",
                file_name="report.md",
                file_id=None,
            )

    def test_bot_workspace_relative_path_accepted(self):
        content = FileContent(
            storage_type=StorageType.BOT_WORKSPACE,
            storage_id="reports/analysis-report.md",
            file_type="text/markdown",
            file_name="analysis-report.md",
            file_id=None,
        )
        assert content.storage_id == "reports/analysis-report.md"


class TestObjectFileContentValidation:
    """OBJECT_FILE 分支:对象 key 寻址,结构校验 fail-loud(前缀域校验归服务层)。"""

    @staticmethod
    def _content(storage_id: str, file_id: str | None = None) -> FileContent:
        return FileContent(
            storage_type=StorageType.OBJECT_FILE,
            storage_id=storage_id,
            file_type="application/pdf",
            file_name="analysis-report.pdf",
            file_id=file_id,
        )

    def test_object_file_accepted_with_stable_key(self):
        content = self._content("task-artifacts/t-1/" + "9f86d081884c7d659a2feaa0c55ad015" * 2 + ".pdf")
        assert content.file_id is None

    def test_object_file_rejects_file_id(self):
        with pytest.raises(TaskArtifactContentError):
            self._content("task-artifacts/t-1/abc.pdf", file_id="f-1")

    def test_object_file_rejects_absolute_key(self):
        with pytest.raises(TaskArtifactContentError):
            self._content("/task-artifacts/t-1/abc.pdf")

    def test_object_file_rejects_tilde_key(self):
        with pytest.raises(TaskArtifactContentError):
            self._content("~/task-artifacts/t-1/abc.pdf")

    def test_object_file_rejects_backslash_key(self):
        with pytest.raises(TaskArtifactContentError):
            self._content("task-artifacts\\t-1\\abc.pdf")

    @pytest.mark.parametrize(
        "key",
        ["task-artifacts//t-1/abc.pdf", "task-artifacts/./t-1/abc.pdf", "task-artifacts/.."],
        ids=["empty-segment", "dot-segment", "parent-escape"],
    )
    def test_object_file_rejects_illegal_segments(self, key):
        with pytest.raises(TaskArtifactContentError):
            self._content(key)

    def test_object_file_rejects_control_characters(self):
        with pytest.raises(TaskArtifactContentError):
            self._content("task-artifacts/t-1/ab\x01c.pdf")

    def test_object_file_rejects_overlong_key(self):
        with pytest.raises(TaskArtifactContentError):
            self._content("a" * 513)

    def test_object_file_storage_type_coercion_from_json(self):
        content = content_from_json(
            '{"kind":"file","storage_type":"object_file","storage_id":"task-artifacts/t-1/abc.pdf",'
            '"file_type":"application/pdf","file_name":"abc.pdf","file_id":null}'
        )
        assert isinstance(content, FileContent)
        assert content.storage_type is StorageType.OBJECT_FILE

    def test_object_file_unknown_storage_type_fails_loud(self):
        with pytest.raises(TaskArtifactContentError):
            content_from_json(
                '{"kind":"file","storage_type":"bogus","storage_id":"x","file_type":"t",'
                '"file_name":"x.pdf","file_id":null}'
            )


class TestUrlLinkContentValidation:
    def test_rejects_non_http_url(self):
        with pytest.raises(TaskArtifactContentError):
            UrlLinkContent(url="ftp://example.com/doc", type="dima需求")

    @pytest.mark.parametrize("empty", ["", "   "])
    def test_rejects_missing_url(self, empty):
        with pytest.raises(TaskArtifactContentError):
            UrlLinkContent(url=empty, type="dima需求")

    def test_rejects_empty_type(self):
        with pytest.raises(TaskArtifactContentError):
            UrlLinkContent(url="https://yuque.example.com/doc", type="")

    def test_rejects_overlong_type(self):
        with pytest.raises(TaskArtifactContentError):
            UrlLinkContent(url="https://yuque.example.com/doc", type="a" * 65)

    def test_rejects_overlong_url(self):
        with pytest.raises(TaskArtifactContentError):
            UrlLinkContent(url="https://example.com/" + "a" * 2100, type="dima需求")

    def test_valid_url_link(self):
        content = UrlLinkContent(url="https://yuque.example.com/doc", type="语雀文档")
        assert content.type == "语雀文档"


class TestCodeContentValidation:
    def test_rejects_non_http_pr_link(self):
        with pytest.raises(TaskArtifactContentError):
            CodeContent(
                code_base_name="avernet",
                code_base_address="git@example.com:avernet.git",
                pr_link="ftp://example.com/pr/1",
                pr_desc="",
            )

    def test_rejects_empty_code_base_name(self):
        with pytest.raises(TaskArtifactContentError):
            CodeContent(
                code_base_name="",
                code_base_address="https://antcode.example.com/avernet",
                pr_link="https://antcode.example.com/pr/1",
                pr_desc="",
            )

    def test_rejects_overlong_pr_desc(self):
        with pytest.raises(TaskArtifactContentError):
            CodeContent(
                code_base_name="avernet",
                code_base_address="https://antcode.example.com/avernet",
                pr_link="https://antcode.example.com/pr/1",
                pr_desc="x" * (8 * 1024 + 1),
            )

    def test_empty_pr_desc_allowed(self):
        content = CodeContent(
            code_base_name="avernet",
            code_base_address="https://antcode.example.com/avernet",
            pr_link="https://antcode.example.com/pr/1",
            pr_desc="",
        )
        assert content.pr_desc == ""


# ---------------------------------------------------------------- 序列化


class TestContentSerialization:
    def _cases(scope):
        return [
            TextContent(text="# 报告\n正文"),
            FileContent(
                storage_type=StorageType.BOT_WORKSPACE,
                storage_id="reports/report.md",
                file_type="text/markdown",
                file_name="report.md",
                file_id=None,
            ),
            FileContent(
                storage_type=StorageType.SESSION_FILE,
                storage_id="sess-xxx",
                file_type="application/pdf",
                file_name="report.pdf",
                file_id="f-987",
            ),
            FileContent(
                storage_type=StorageType.OBJECT_FILE,
                storage_id="task-artifacts/t-1/9f86d081884c7d659a2feaa0c55ad015.pdf",
                file_type="application/pdf",
                file_name="report.pdf",
                file_id=None,
            ),
            UrlLinkContent(url="https://example.com/doc", type="dima需求"),
            CodeContent(
                code_base_name="avernet",
                code_base_address="https://antcode.example.com/avernet",
                pr_link="https://antcode.example.com/pr/1",
                pr_desc="fix: something",
            ),
        ]

    @pytest.mark.parametrize("case", _cases("ignored"), ids=["text", "file_ws", "file_sf", "file_of", "url", "code"])
    def test_roundtrip_equality(self, case):
        assert content_from_json(content_to_json(case)) == case

    def test_json_is_kind_tagged_canonical(self):
        payload = content_to_json(TextContent(text="hi"))
        assert payload == '{"kind":"text","text":"hi"}'

    def test_json_has_no_newlines(self):
        payload = content_to_json(
            UrlLinkContent(url="https://example.com/doc", type="dima需求")
        )
        assert "\n" not in payload

    def test_unknown_kind_tag_fails_loud(self):
        with pytest.raises(TaskArtifactContentError):
            content_from_json('{"kind":"bogus","x":1}')

    def test_malformed_json_fails_loud(self):
        with pytest.raises(TaskArtifactContentError):
            content_from_json("not json{")


class TestContentHash:
    def test_stable_for_same_content(self):
        a = TextContent(text="done")
        assert artifact_content_hash(a) == artifact_content_hash(TextContent(text="done"))

    def test_insensitive_to_field_order(self):
        direct = FileContent(
            storage_type=StorageType.BOT_WORKSPACE,
            storage_id="reports/report.md",
            file_type="text/markdown",
            file_name="report.md",
            file_id=None,
        )
        reordered = content_from_json(
            '{"file_id":null,"file_name":"report.md","kind":"file",'
            '"file_type":"text/markdown","storage_id":"reports/report.md",'
            '"storage_type":"bot_workspace"}'
        )
        assert reordered == direct
        assert artifact_content_hash(reordered) == artifact_content_hash(direct)


class TestArtifactTextValue:
    @pytest.mark.parametrize(
        ("output", "expected"),
        [
            ({"output": "done"}, "done"),
            ({"markdown": "# x"}, "# x"),
            ({"result": "分析完成"}, "分析完成"),
            ({"output": "a", "b": 1}, None),
            ({"skipped": True}, None),
            ({"output": ""}, None),
            ({}, None),
        ],
    )
    def test_reduction(self, output, expected):
        assert artifact_text_value(output) == expected


class TestTaskArtifact:
    def test_storage_for_file_branch(self, scope):
        artifact = _artifact(
            FileContent(
                storage_type=StorageType.SESSION_FILE,
                storage_id="sess-xxx",
                file_type="application/pdf",
                file_name="report.pdf",
                file_id="f-987",
            ),
            scope=scope,
            kind=ArtifactKind.FILE,
        )
        assert artifact.storage == "session_file"

    def test_storage_for_text_branch(self, scope):
        artifact = _artifact(TextContent(text="done"), scope=scope)
        assert artifact.storage == "inline"

    def test_frozen(self, scope):
        artifact = _artifact(TextContent(text="done"), scope=scope)
        with pytest.raises(Exception):
            artifact.creator = "other"


class TestNewArtifactId:
    def test_prefix_and_length(self):
        value = new_artifact_id()
        assert value.startswith("task-art-")
        tail = value[len("task-art-"):]
        assert len(tail) == 16
        int(tail, 16)  # 16 位 hex