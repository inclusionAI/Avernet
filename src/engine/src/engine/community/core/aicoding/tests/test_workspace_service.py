"""Unit tests for WorkspaceService.

覆盖两个层面：

1. 本 PR 新增/修改的两个静态方法
   - ``_resolve_workspace_base`` —— 读取 ``RELAY_DEFAULT_CWD`` 环境变量；未设置或纯
     空白时回落到 ``CONTAINER_WORKSPACE_BASE``。
   - ``ensure_workspace_exists`` —— 在 ``resolve_workspace`` 基础上做
     ``os.path.isdir`` 校验；不存在时抛 ``FileNotFoundError``，detail 含完整路径。

2. 4 个实例方法 + ``_ensure_within_workspace`` 保护
   - ``list_file_tree`` —— 调用 ``FileService.list_dir`` + ``build_file_tree``
   - ``preview_file`` —— 校验 path/大小/路径穿越，读出 utf-8 文本
   - ``list_git_diff`` —— ``find .git`` + ``git status --porcelain`` 全流程
   - ``get_file_diff`` —— ``git diff HEAD`` 主路径 + untracked fallback
   - ``_ensure_within_workspace`` —— 防 ``..`` / 绝对路径穿越
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import pytest

from engine.community.core.aicoding.workspace_service import (
    CONTAINER_WORKSPACE_BASE,
    PREVIEW_MAX_BYTES,
    FilePreviewTooLargeError,
    WorkspaceService,
    _resolve_workspace_base,
)
from engine.community.core.bash.models import BashExecResult
from engine.community.core.file.models import FileEntry, ListDirResult


# ── _resolve_workspace_base ──────────────────────────────────────────────────


def test_resolve_workspace_base_falls_back_when_env_unset(monkeypatch) -> None:
    """``RELAY_DEFAULT_CWD`` 未设置 → 返回硬编码常量。"""
    monkeypatch.delenv("RELAY_DEFAULT_CWD", raising=False)
    assert _resolve_workspace_base() == CONTAINER_WORKSPACE_BASE


def test_resolve_workspace_base_uses_env_when_set(monkeypatch) -> None:
    """env 有值 → 返回 env 值（覆盖硬编码）。"""
    monkeypatch.setenv("RELAY_DEFAULT_CWD", "/tmp/custom-workspace")
    assert _resolve_workspace_base() == "/tmp/custom-workspace"


def test_resolve_workspace_base_whitespace_env_falls_back(monkeypatch) -> None:
    """env 设为纯空白 → strip 后为空 → 回落到硬编码（防止配置错误把 base 设成空目录）。"""
    monkeypatch.setenv("RELAY_DEFAULT_CWD", "   ")
    assert _resolve_workspace_base() == CONTAINER_WORKSPACE_BASE


def test_resolve_workspace_base_reads_env_each_call(monkeypatch) -> None:
    """每次调用都重新读 env —— 不缓存，便于测试通过 monkeypatch 覆盖。"""
    monkeypatch.setenv("RELAY_DEFAULT_CWD", "/path/one")
    assert _resolve_workspace_base() == "/path/one"
    monkeypatch.setenv("RELAY_DEFAULT_CWD", "/path/two")
    assert _resolve_workspace_base() == "/path/two"


# ── WorkspaceService.resolve_workspace ───────────────────────────────────────


def test_resolve_workspace_concats_base_and_session_id(monkeypatch) -> None:
    """硬编码 base 下，路径形如 ``{base}/{session_id}``。"""
    monkeypatch.delenv("RELAY_DEFAULT_CWD", raising=False)
    sid = "user:u1:session:s1:agent:b1"
    assert WorkspaceService.resolve_workspace(sid) == f"{CONTAINER_WORKSPACE_BASE}/{sid}"


def test_resolve_workspace_passes_session_id_with_colons_verbatim(monkeypatch) -> None:
    """session_id 含 ``:`` 不应被 URL-encode 或转义，按字面拼接。"""
    monkeypatch.setenv("RELAY_DEFAULT_CWD", "/data/aix")
    sid = "user:abc:session:xyz"
    assert WorkspaceService.resolve_workspace(sid) == f"/data/aix/{sid}"


def test_resolve_workspace_follows_env_changes(monkeypatch) -> None:
    """env 改变后 resolve_workspace 同步生效（与 _resolve_workspace_base 一致）。"""
    sid = "sess-1"
    monkeypatch.setenv("RELAY_DEFAULT_CWD", "/a")
    assert WorkspaceService.resolve_workspace(sid) == "/a/sess-1"
    monkeypatch.setenv("RELAY_DEFAULT_CWD", "/b")
    assert WorkspaceService.resolve_workspace(sid) == "/b/sess-1"


# ── WorkspaceService.ensure_workspace_exists ─────────────────────────────────


def test_ensure_workspace_exists_returns_path_when_dir_exists(
    tmp_path: Path, monkeypatch
) -> None:
    """目录存在 → 返回校验通过的绝对路径（即 resolve_workspace 结果）。"""
    monkeypatch.setenv("RELAY_DEFAULT_CWD", str(tmp_path))
    sid = "sess-ok"
    (tmp_path / sid).mkdir()

    got = WorkspaceService.ensure_workspace_exists(sid)
    assert got == str(tmp_path / sid)


def test_ensure_workspace_exists_raises_when_dir_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """目录不存在 → 抛 ``FileNotFoundError``，detail 含完整路径供 router 转 404。"""
    monkeypatch.setenv("RELAY_DEFAULT_CWD", str(tmp_path))
    sid = "sess-missing"
    expected_path = str(tmp_path / sid)

    with pytest.raises(FileNotFoundError) as excinfo:
        WorkspaceService.ensure_workspace_exists(sid)

    assert expected_path in str(excinfo.value)
    assert "AICoding session workspace not found" in str(excinfo.value)


def test_ensure_workspace_exists_rejects_file_path(
    tmp_path: Path, monkeypatch
) -> None:
    """路径存在但是文件而非目录 → 也应抛 ``FileNotFoundError``。

    ``os.path.isdir`` 对文件返回 False，与"不存在"统一处理 —— router 仍转 404，
    符合"workspace 必须是目录"的约束。
    """
    monkeypatch.setenv("RELAY_DEFAULT_CWD", str(tmp_path))
    sid = "sess-is-file"
    (tmp_path / sid).write_text("not a directory")

    with pytest.raises(FileNotFoundError):
        WorkspaceService.ensure_workspace_exists(sid)


# ── _ensure_within_workspace（路径穿越保护） ───────────────────────────────


def test_ensure_within_workspace_accepts_equal_path() -> None:
    """``full_path == workspace`` 必须放行（list workspace 根本身）。"""
    WorkspaceService._ensure_within_workspace(
        "/ws/sess-1", "/ws/sess-1",
    )


def test_ensure_within_workspace_accepts_child_path() -> None:
    """合法子路径必须放行。"""
    WorkspaceService._ensure_within_workspace(
        "/ws/sess-1/project-fe/README.md", "/ws/sess-1",
    )


def test_ensure_within_workspace_rejects_parent_traversal() -> None:
    """``..`` 跳出 workspace → ``ValueError``。"""
    with pytest.raises(ValueError, match="path traversal detected"):
        WorkspaceService._ensure_within_workspace(
            "/ws/other-sess/secret", "/ws/sess-1",
        )


def test_ensure_within_workspace_rejects_prefix_sibling() -> None:
    """workspace=``/ws/sess-1`` 不应吞并 ``/ws/sess-12``（防止前缀误判）。"""
    with pytest.raises(ValueError, match="path traversal detected"):
        WorkspaceService._ensure_within_workspace(
            "/ws/sess-12/file.txt", "/ws/sess-1",
        )


# ── 共用：构造一个挂载 fake plugin 的 WorkspaceService ─────────────────────


class FakeFilePlugin:
    """最小可用的 FileService stub。

    - ``list_dir`` 返回预设的 ``ListDirResult``
    - ``read`` 返回预设 bytes，或按 path 命中映射
    其它方法不实现，因为 WorkspaceService 不会调用。
    """

    def __init__(
        self,
        list_result: Optional[ListDirResult] = None,
        read_map: Optional[dict[str, bytes]] = None,
        read_raise: Optional[BaseException] = None,
    ) -> None:
        self.list_result = list_result or ListDirResult(
            dir_path="", recursive=True, files=[],
        )
        self.read_map = read_map or {}
        self.read_raise = read_raise
        self.calls: list[tuple[str, dict]] = []

    async def list_dir(
        self,
        dir_path: str,
        recursive: bool = False,
        exclude_dirs: set[str] | None = None,
        auth=None,  # noqa: ANN001 — match Protocol signature
    ) -> ListDirResult:
        self.calls.append(("list_dir", {"dir_path": dir_path, "recursive": recursive, "exclude_dirs": exclude_dirs}))
        return self.list_result

    async def read(self, file_path: str, auth=None) -> bytes:  # noqa: ANN001
        self.calls.append(("read", {"file_path": file_path}))
        if self.read_raise:
            raise self.read_raise
        if file_path in self.read_map:
            return self.read_map[file_path]
        return b""


class FakeBashPlugin:
    """复用 ``test_runstatus_service`` 的同款选择器。

    匹配规则：cmd substring + 可选 cwd。``raise_for`` 优先于响应表。
    """

    def __init__(self) -> None:
        self.responses: list[tuple[str, Optional[str], BashExecResult]] = []
        self.raise_for: list[tuple[str, Optional[str], BaseException]] = []
        self.calls: list[tuple[str, str, int]] = []

    def add(
        self, cmd_match: str, cwd: Optional[str], result: BashExecResult,
    ) -> None:
        self.responses.append((cmd_match, cwd, result))

    def add_raise(
        self, cmd_match: str, cwd: Optional[str], exc: BaseException,
    ) -> None:
        self.raise_for.append((cmd_match, cwd, exc))

    async def exec(
        self,
        cmd: str,
        cwd: str,
        timeout: int = 30,
        auth=None,  # noqa: ANN001
    ) -> BashExecResult:
        self.calls.append((cmd, cwd, timeout))
        for cmd_match, want_cwd, exc in self.raise_for:
            if cmd_match in cmd and (want_cwd is None or want_cwd == cwd):
                raise exc
        for cmd_match, want_cwd, res in self.responses:
            if cmd_match in cmd and want_cwd == cwd:
                return res
        for cmd_match, want_cwd, res in self.responses:
            if cmd_match in cmd and want_cwd is None:
                return res
        return BashExecResult(stdout="", stderr="", exit_code=0)


def _make_service(
    file_plugin: Optional[FakeFilePlugin] = None,
    bash_plugin: Optional[FakeBashPlugin] = None,
) -> WorkspaceService:
    return WorkspaceService(
        file_plugin=file_plugin or FakeFilePlugin(),
        bash_plugin=bash_plugin or FakeBashPlugin(),
    )


@pytest.fixture
def workspace_dir(tmp_path: Path, monkeypatch) -> Path:
    """建立真实 workspace 目录并把 base 指过去；返回 session workspace 路径。"""
    monkeypatch.setenv("RELAY_DEFAULT_CWD", str(tmp_path))
    sid = "sess-1"
    ws = tmp_path / sid
    ws.mkdir()
    return ws


SESSION_ID = "sess-1"


# ── list_file_tree ─────────────────────────────────────────────────────────


async def test_list_file_tree_returns_filtered_sorted_tree(workspace_dir: Path) -> None:
    """调用 FileService.list_dir(recursive=True, exclude_dirs=...) → build_file_tree → 排序。

    过滤由 list_dir 的 exclude_dirs 参数完成（mock 模拟已过滤后的结果）；
    目录优先 + 字母序排列。
    """
    file_plugin = FakeFilePlugin(
        list_result=ListDirResult(
            dir_path=str(workspace_dir),
            recursive=True,
            files=[
                FileEntry(
                    name="project-fe",
                    path=str(workspace_dir / "project-fe"),
                    relative_path="project-fe",
                    is_dir=True,
                    size=0,
                ),
                FileEntry(
                    name="README.md",
                    path=str(workspace_dir / "project-fe/README.md"),
                    relative_path="project-fe/README.md",
                    is_dir=False,
                    size=42,
                ),
                FileEntry(
                    name="src",
                    path=str(workspace_dir / "project-fe/src"),
                    relative_path="project-fe/src",
                    is_dir=True,
                    size=0,
                ),
                FileEntry(
                    name="index.ts",
                    path=str(workspace_dir / "project-fe/src/index.ts"),
                    relative_path="project-fe/src/index.ts",
                    is_dir=False,
                    size=10,
                ),
            ],
        )
    )
    service = _make_service(file_plugin=file_plugin)
    tree = await service.list_file_tree(SESSION_ID)

    # 顶层只剩 project-fe
    assert len(tree) == 1
    project = tree[0]
    assert project.name == "project-fe"
    assert project.is_dir is True
    # 目录优先 → src 在前，README.md 在后
    assert [c.name for c in project.children] == ["src", "README.md"]
    # FileService.list_dir 必须以 workspace 绝对路径 + recursive=True + exclude_dirs 调用
    assert file_plugin.calls[0] == (
        "list_dir",
        {"dir_path": str(workspace_dir), "recursive": True, "exclude_dirs": {".git", "node_modules"}},
    )


async def test_list_file_tree_raises_when_workspace_missing(
    tmp_path: Path, monkeypatch,
) -> None:
    """workspace 不存在 → 透传 ``FileNotFoundError``（由 ensure_workspace_exists 抛出）。"""
    monkeypatch.setenv("RELAY_DEFAULT_CWD", str(tmp_path))
    service = _make_service()
    with pytest.raises(FileNotFoundError):
        await service.list_file_tree("no-such-sess")


# ── preview_file ───────────────────────────────────────────────────────────


async def test_preview_file_returns_decoded_content(workspace_dir: Path) -> None:
    """成功路径：读出 utf-8 文本，size 与 bytes 长度一致。"""
    target = str(workspace_dir / "a.txt")
    file_plugin = FakeFilePlugin(read_map={target: "hello 你好".encode("utf-8")})
    service = _make_service(file_plugin=file_plugin)

    out = await service.preview_file(SESSION_ID, "a.txt")
    assert out.content == "hello 你好"
    assert out.size == len("hello 你好".encode("utf-8"))


async def test_preview_file_normalizes_path_and_strips_whitespace(
    workspace_dir: Path,
) -> None:
    """path 含前后空白应 strip；``./`` 应被 normpath 吃掉。"""
    target = str(workspace_dir / "a.txt")
    file_plugin = FakeFilePlugin(read_map={target: b"x"})
    service = _make_service(file_plugin=file_plugin)

    await service.preview_file(SESSION_ID, "  ./a.txt  ")
    read_calls = [c for c in file_plugin.calls if c[0] == "read"]
    assert read_calls[0][1]["file_path"] == target


@pytest.mark.parametrize("bad_path", ["", "   ", "\t"])
async def test_preview_file_rejects_blank_path(
    workspace_dir: Path, bad_path: str,
) -> None:
    """空 / 仅空白 path → ``ValueError``。"""
    service = _make_service()
    with pytest.raises(ValueError, match="path is required"):
        await service.preview_file(SESSION_ID, bad_path)


async def test_preview_file_rejects_path_traversal(workspace_dir: Path) -> None:
    """``..`` 跳出 workspace → ``ValueError``。"""
    service = _make_service()
    with pytest.raises(ValueError, match="path traversal detected"):
        await service.preview_file(SESSION_ID, "../../etc/passwd")


async def test_preview_file_raises_too_large(
    workspace_dir: Path, monkeypatch,
) -> None:
    """读出 size 超 ``PREVIEW_MAX_BYTES`` → 抛 ``FilePreviewTooLargeError``。"""
    # 把 cap 压到 8 字节，避免真造一个 10MiB buffer
    monkeypatch.setattr(
        "engine.community.core.aicoding.workspace_service.PREVIEW_MAX_BYTES", 8,
    )
    target = str(workspace_dir / "big.bin")
    file_plugin = FakeFilePlugin(read_map={target: b"0123456789"})
    service = _make_service(file_plugin=file_plugin)

    with pytest.raises(FilePreviewTooLargeError) as excinfo:
        await service.preview_file(SESSION_ID, "big.bin")
    assert "file too large for preview" in str(excinfo.value)


def test_preview_max_bytes_default_is_10_mib() -> None:
    """守住默认上限不被改小。"""
    assert PREVIEW_MAX_BYTES == 10 * 1024 * 1024


# ── list_git_diff ──────────────────────────────────────────────────────────


async def test_list_git_diff_aggregates_per_project(workspace_dir: Path) -> None:
    """find 出两个 project → 分别跑 git status → 合并成 per-project tree。"""
    bash = FakeBashPlugin()
    bash.add(
        "find",
        str(workspace_dir),
        BashExecResult(
            stdout=(
                f"{workspace_dir}/project-fe/.git\n"
                f"{workspace_dir}/project-be/.git\n"
            ),
            stderr="",
            exit_code=0,
        ),
    )
    bash.add(
        "git -c core.quotePath=false status",
        f"{workspace_dir}/project-fe",
        BashExecResult(
            # 注意：第一行是" M README.md"，有前导空格；不能 strip 整个 output
            stdout=" M README.md\n?? new.txt\n",
            stderr="",
            exit_code=0,
        ),
    )
    bash.add(
        "git -c core.quotePath=false status",
        f"{workspace_dir}/project-be",
        BashExecResult(stdout="A  added.py\n", stderr="", exit_code=0),
    )
    service = _make_service(bash_plugin=bash)

    result = await service.list_git_diff(SESSION_ID)
    assert result.session_id == SESSION_ID
    assert {p.project for p in result.diff_head} == {"project-fe", "project-be"}
    fe = next(p for p in result.diff_head if p.project == "project-fe")
    names = {child.name for child in (fe.tree.children or [])}
    assert names == {"README.md", "new.txt"}


async def test_list_git_diff_returns_empty_when_find_fails(
    workspace_dir: Path,
) -> None:
    """find 退出非 0 → 直接返回空 diff_head（带 warning）。"""
    bash = FakeBashPlugin()
    bash.add(
        "find",
        str(workspace_dir),
        BashExecResult(stdout="", stderr="permission denied", exit_code=1),
    )
    service = _make_service(bash_plugin=bash)
    result = await service.list_git_diff(SESSION_ID)
    assert result.diff_head == []


async def test_list_git_diff_skips_project_with_failing_status(
    workspace_dir: Path,
) -> None:
    """单个 project 的 git status 失败时跳过它，不影响其它 project。"""
    bash = FakeBashPlugin()
    bash.add(
        "find",
        str(workspace_dir),
        BashExecResult(
            stdout=(
                f"{workspace_dir}/project-fe/.git\n"
                f"{workspace_dir}/project-be/.git\n"
            ),
            stderr="",
            exit_code=0,
        ),
    )
    bash.add(
        "git -c core.quotePath=false status",
        f"{workspace_dir}/project-fe",
        BashExecResult(stdout="", stderr="not a git repo", exit_code=128),
    )
    bash.add(
        "git -c core.quotePath=false status",
        f"{workspace_dir}/project-be",
        BashExecResult(stdout="M  ok.py\n", stderr="", exit_code=0),
    )
    service = _make_service(bash_plugin=bash)
    result = await service.list_git_diff(SESSION_ID)
    assert [p.project for p in result.diff_head] == ["project-be"]


async def test_list_git_diff_skips_project_with_no_changes(
    workspace_dir: Path,
) -> None:
    """git status 空输出 / 仅空白 → 跳过该 project。"""
    bash = FakeBashPlugin()
    bash.add(
        "find",
        str(workspace_dir),
        BashExecResult(
            stdout=f"{workspace_dir}/project-fe/.git\n",
            stderr="",
            exit_code=0,
        ),
    )
    bash.add(
        "git -c core.quotePath=false status",
        f"{workspace_dir}/project-fe",
        BashExecResult(stdout="   \n", stderr="", exit_code=0),
    )
    service = _make_service(bash_plugin=bash)
    result = await service.list_git_diff(SESSION_ID)
    assert result.diff_head == []


async def test_list_git_diff_skips_unparseable_status(workspace_dir: Path) -> None:
    """非空但 ``parse_porcelain_status`` 解出零条记录（全 ``!!``） → 跳过。"""
    bash = FakeBashPlugin()
    bash.add(
        "find",
        str(workspace_dir),
        BashExecResult(
            stdout=f"{workspace_dir}/project-fe/.git\n",
            stderr="",
            exit_code=0,
        ),
    )
    bash.add(
        "git -c core.quotePath=false status",
        f"{workspace_dir}/project-fe",
        BashExecResult(stdout="!! ignored.txt\n", stderr="", exit_code=0),
    )
    service = _make_service(bash_plugin=bash)
    result = await service.list_git_diff(SESSION_ID)
    assert result.diff_head == []


async def test_list_git_diff_find_cmd_accepts_worktrees_and_prunes_repos(
    workspace_dir: Path,
) -> None:
    """find 命令必须 (1) 兼容 ``.git`` 文件指针，(2) 跳过 ``.repos/``。

    relay 在 ``<workspace>/<sid>/<repo>/`` 下用 ``git worktree`` 摊开 session
    视图，``.git`` 是文件不是目录；同时共享裸仓库放在 ``<workspace>/.repos/<repo>``
    下，``-maxdepth 2`` 会扫到那里的真 ``.git`` 目录，必须 prune 掉避免同 repo
    被报两次。
    """
    bash = FakeBashPlugin()
    captured: list[str] = []

    original_exec = bash.exec

    async def capture_exec(cmd: str, cwd: str, timeout: int = 30, auth=None):  # noqa: ANN001, ANN202
        captured.append(cmd)
        return await original_exec(cmd, cwd, timeout, auth)

    bash.exec = capture_exec  # type: ignore[assignment]
    bash.add(
        "find",
        str(workspace_dir),
        BashExecResult(stdout="", stderr="", exit_code=0),
    )

    service = _make_service(bash_plugin=bash)
    await service.list_git_diff(SESSION_ID)

    find_cmds = [c for c in captured if c.startswith("find ")]
    assert len(find_cmds) == 1, find_cmds
    cmd = find_cmds[0]
    # 接受 .git 文件指针（worktree）+ 目录（普通 clone）
    assert "-type d -o -type f" in cmd
    # 跳过共享仓库目录避免重复
    assert "-name .repos -prune" in cmd


# ── _parse_projects ────────────────────────────────────────────────────────


def test_parse_projects_extracts_name_and_path() -> None:
    """find 输出按行解析；每行去掉 ``/.git`` 后取 basename 当 project name。"""
    out = WorkspaceService._parse_projects(
        "/ws/s1/project-fe/.git\n/ws/s1/project-be/.git\n",
    )
    assert out == [
        ("project-fe", "/ws/s1/project-fe"),
        ("project-be", "/ws/s1/project-be"),
    ]


def test_parse_projects_skips_blank_and_root_only_lines() -> None:
    """空行 / 解析出空 basename 都应跳过，不让坏数据进入下游 git status。

    注意：``find_output.strip()`` 会吃掉首尾的空行，所以必须把空行**夹**在
    两条合法记录之间，才能命中 ``if not git_dir: continue`` 这一分支。
    """
    out = WorkspaceService._parse_projects(
        # 中间夹一条仅空格的行 + 一条 /.git（dirname=``/``、basename=``""``）
        "/ws/s1/proj-a/.git\n   \n/.git\n/ws/s1/proj-b/.git\n",
    )
    assert out == [
        ("proj-a", "/ws/s1/proj-a"),
        ("proj-b", "/ws/s1/proj-b"),
    ]


# ── get_file_diff ──────────────────────────────────────────────────────────


async def test_get_file_diff_returns_diff_output(workspace_dir: Path) -> None:
    """git diff HEAD 主路径 → 直接返回 stdout。"""
    project_path = workspace_dir / "project-fe"
    (project_path / ".git").mkdir(parents=True)

    bash = FakeBashPlugin()
    bash.add(
        "git diff HEAD",
        str(project_path),
        BashExecResult(
            stdout="--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-old\n+new\n",
            stderr="",
            exit_code=0,
        ),
    )
    service = _make_service(bash_plugin=bash)
    result = await service.get_file_diff(
        session_id=SESSION_ID, project="project-fe", file_path="README.md",
    )
    assert result.project == "project-fe"
    assert result.path == "README.md"
    assert "+new" in result.diff


async def test_get_file_diff_supports_renamed_old_path(
    workspace_dir: Path,
) -> None:
    """传 ``old_path`` → 拼成 ``git diff HEAD -- <old> <new>``。"""
    project_path = workspace_dir / "project-fe"
    (project_path / ".git").mkdir(parents=True)

    bash = FakeBashPlugin()
    bash.add(
        "git diff HEAD",
        str(project_path),
        BashExecResult(stdout="renamed-diff\n", stderr="", exit_code=0),
    )
    service = _make_service(bash_plugin=bash)
    result = await service.get_file_diff(
        session_id=SESSION_ID,
        project="project-fe",
        file_path="new.md",
        old_path="old.md",
    )
    assert result.diff == "renamed-diff\n"
    executed = [c[0] for c in bash.calls]
    assert any('"old.md" "new.md"' in cmd for cmd in executed)


async def test_get_file_diff_falls_back_to_no_index_for_untracked(
    workspace_dir: Path,
) -> None:
    """git diff HEAD 空输出 + 无 old_path → 跑 ``git diff --no-index`` 兜底。"""
    project_path = workspace_dir / "project-fe"
    (project_path / ".git").mkdir(parents=True)

    bash = FakeBashPlugin()
    bash.add(
        "git diff HEAD",
        str(project_path),
        BashExecResult(stdout="   \n", stderr="", exit_code=0),
    )
    bash.add(
        "git diff --no-index",
        str(project_path),
        BashExecResult(
            stdout="--- /dev/null\n+++ b/new.txt\n+hello\n",
            stderr="",
            exit_code=1,  # exit_code=1 表示"有 diff"，不是错误
        ),
    )
    service = _make_service(bash_plugin=bash)
    result = await service.get_file_diff(
        session_id=SESSION_ID, project="project-fe", file_path="new.txt",
    )
    assert "+hello" in result.diff


@pytest.mark.parametrize("bad_project", ["", ".", "..", "a/b"])
async def test_get_file_diff_rejects_invalid_project(
    workspace_dir: Path, bad_project: str,
) -> None:
    """project 不能含 ``/`` / 不能是 ``.``/``..``/空 —— 校验在 ensure_workspace_exists 之前。"""
    service = _make_service()
    with pytest.raises(ValueError, match="invalid project"):
        await service.get_file_diff(
            session_id=SESSION_ID, project=bad_project, file_path="x",
        )


async def test_get_file_diff_404_when_project_dir_missing(
    workspace_dir: Path,
) -> None:
    """workspace 在但 project 子目录不存在 → ``FileNotFoundError``。"""
    service = _make_service()
    with pytest.raises(FileNotFoundError, match="Project not found"):
        await service.get_file_diff(
            session_id=SESSION_ID, project="no-such-project", file_path="x",
        )


async def test_get_file_diff_400_when_not_a_git_repo(
    workspace_dir: Path,
) -> None:
    """project 存在但没有 ``.git`` → ``ValueError`` (router 转 400)。"""
    (workspace_dir / "plain").mkdir()
    service = _make_service()
    with pytest.raises(ValueError, match="Not a git repository"):
        await service.get_file_diff(
            session_id=SESSION_ID, project="plain", file_path="x",
        )


async def test_get_file_diff_accepts_worktree_pointer_file(
    workspace_dir: Path,
) -> None:
    """``.git`` 是 worktree pointer 文件（非目录）时也应识别为 git 仓库。

    relay 在 ``<workspace>/<sid>/<repo>`` 下用 ``git worktree add`` 摊开
    session 视图，产物的 ``.git`` 是首行 ``gitdir:`` 的文本文件而不是目录。
    历史实现按 ``os.path.isdir(".git")`` 校验，会把它误判成"不是 git 仓库"
    并抛 400。
    """
    project_path = workspace_dir / "project-fe"
    project_path.mkdir(parents=True)
    (project_path / ".git").write_text(
        "gitdir: /workspace/.repos/project-fe/.git/worktrees/sess-1\n",
    )

    bash = FakeBashPlugin()
    bash.add(
        "git diff HEAD",
        str(project_path),
        BashExecResult(stdout="worktree-diff\n", stderr="", exit_code=0),
    )
    service = _make_service(bash_plugin=bash)
    result = await service.get_file_diff(
        session_id=SESSION_ID, project="project-fe", file_path="README.md",
    )
    assert result.diff == "worktree-diff\n"


# ── validate_cwd / _validate_cwd_prefix（cwd 直传校验）─────────────────────


def test_validate_cwd_accepts_existing_dir_under_allowed_root(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AICODING_CWD_ALLOW_ROOTS", str(tmp_path))
    target = tmp_path / "session-1"
    target.mkdir()
    got = WorkspaceService.validate_cwd(str(target))
    assert got == os.path.normpath(str(target))


def test_validate_cwd_normalizes_path(tmp_path: Path, monkeypatch) -> None:
    """返回规范化绝对路径（吃掉 trailing slash）。"""
    monkeypatch.setenv("AICODING_CWD_ALLOW_ROOTS", str(tmp_path))
    target = tmp_path / "s"
    target.mkdir()
    got = WorkspaceService.validate_cwd(f"{target}/")
    assert got == str(target)


def test_validate_cwd_rejects_file_path(tmp_path: Path, monkeypatch) -> None:
    """cwd 指向文件 → NotADirectoryError（router 转 400）。"""
    monkeypatch.setenv("AICODING_CWD_ALLOW_ROOTS", str(tmp_path))
    f = tmp_path / "a.txt"
    f.write_text("x")
    with pytest.raises(NotADirectoryError):
        WorkspaceService.validate_cwd(str(f))


def test_validate_cwd_rejects_missing_path(tmp_path: Path, monkeypatch) -> None:
    """cwd 不存在 → FileNotFoundError（router 转 404）。"""
    monkeypatch.setenv("AICODING_CWD_ALLOW_ROOTS", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        WorkspaceService.validate_cwd(str(tmp_path / "no-such"))


def test_validate_cwd_rejects_outside_allowed_root(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AICODING_CWD_ALLOW_ROOTS", str(tmp_path))
    with pytest.raises(ValueError, match="cwd not allowed"):
        WorkspaceService.validate_cwd("/etc")


def test_validate_cwd_rejects_relative_path(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AICODING_CWD_ALLOW_ROOTS", str(tmp_path))
    with pytest.raises(ValueError, match="cwd must be absolute"):
        WorkspaceService.validate_cwd("relative/path")


def test_validate_cwd_rejects_blank(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AICODING_CWD_ALLOW_ROOTS", str(tmp_path))
    with pytest.raises(ValueError, match="cwd is required"):
        WorkspaceService.validate_cwd("   ")


def test_validate_cwd_rejects_traversal(tmp_path: Path, monkeypatch) -> None:
    """``..`` 穿越出允许根 → normpath 后越界 → ValueError。"""
    monkeypatch.setenv("AICODING_CWD_ALLOW_ROOTS", str(tmp_path))
    with pytest.raises(ValueError, match="cwd not allowed"):
        WorkspaceService.validate_cwd(f"{tmp_path}/../etc")


def test_validate_cwd_accepts_root_itself(tmp_path: Path, monkeypatch) -> None:
    """cwd 等于允许根本身应放行（不必是其子目录）。"""
    monkeypatch.setenv("AICODING_CWD_ALLOW_ROOTS", str(tmp_path))
    got = WorkspaceService.validate_cwd(str(tmp_path))
    assert got == str(tmp_path)


def test_validate_cwd_default_root_only(tmp_path: Path, monkeypatch) -> None:
    """未设 AICODING_CWD_ALLOW_ROOTS 时只允许 CONTAINER_WORKSPACE_BASE。"""
    monkeypatch.delenv("AICODING_CWD_ALLOW_ROOTS", raising=False)
    with pytest.raises(ValueError, match="cwd not allowed"):
        WorkspaceService.validate_cwd(str(tmp_path))


def test_validate_cwd_prefix_skips_existence(
    tmp_path: Path, monkeypatch
) -> None:
    """_validate_cwd_prefix 只校验格式 + 前缀，不校验存在性（供 worktree-status）。"""
    monkeypatch.setenv("AICODING_CWD_ALLOW_ROOTS", str(tmp_path))
    got = WorkspaceService._validate_cwd_prefix(str(tmp_path / "no-such"))
    assert got == os.path.normpath(str(tmp_path / "no-such"))


def test_allowed_cwd_roots_reads_env(tmp_path: Path, monkeypatch) -> None:
    """env 扩展允许根（Rule 14 配置驱动）；重复 / trailing slash 规范化。"""
    from engine.community.core.aicoding.workspace_service import _allowed_cwd_roots

    monkeypatch.setenv(
        "AICODING_CWD_ALLOW_ROOTS", f"{tmp_path}, /workspace/extra,"
    )
    roots = _allowed_cwd_roots()
    assert roots[0] == CONTAINER_WORKSPACE_BASE
    assert str(tmp_path) in roots
    assert "/workspace/extra" in roots


# ── resolve_workspace / ensure_workspace_exists 的 cwd 优先 ─────────────────


def test_resolve_workspace_cwd_direct_takes_precedence(monkeypatch) -> None:
    """cwd 直传优先于 base/{session_id} 拼接。"""
    monkeypatch.delenv("RELAY_DEFAULT_CWD", raising=False)
    monkeypatch.delenv("AICODING_CWD_ALLOW_ROOTS", raising=False)
    custom = f"{CONTAINER_WORKSPACE_BASE}/custom-session"
    got = WorkspaceService.resolve_workspace("sid", cwd=custom)
    assert got == custom


def test_resolve_workspace_cwd_none_falls_back_to_base(monkeypatch) -> None:
    monkeypatch.delenv("RELAY_DEFAULT_CWD", raising=False)
    got = WorkspaceService.resolve_workspace("sid", cwd=None)
    assert got == f"{CONTAINER_WORKSPACE_BASE}/sid"


def test_resolve_workspace_cwd_invalid_raises(monkeypatch) -> None:
    """resolve_workspace 的 cwd 直传仍走前缀校验（越界 → ValueError）。"""
    monkeypatch.delenv("AICODING_CWD_ALLOW_ROOTS", raising=False)
    with pytest.raises(ValueError, match="cwd not allowed"):
        WorkspaceService.resolve_workspace("sid", cwd="/etc")


def test_ensure_workspace_exists_cwd_direct_validates_existence(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AICODING_CWD_ALLOW_ROOTS", str(tmp_path))
    target = tmp_path / "s"
    target.mkdir()
    got = WorkspaceService.ensure_workspace_exists("ignored-sid", cwd=str(target))
    assert got == str(target)


def test_ensure_workspace_exists_cwd_file_raises_not_a_dir(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AICODING_CWD_ALLOW_ROOTS", str(tmp_path))
    f = tmp_path / "a.txt"
    f.write_text("x")
    with pytest.raises(NotADirectoryError):
        WorkspaceService.ensure_workspace_exists("sid", cwd=str(f))


def test_ensure_workspace_exists_cwd_none_falls_back(
    tmp_path: Path, monkeypatch
) -> None:
    """cwd=None → 旧逻辑：resolve_workspace(session_id) + isdir 检查。"""
    monkeypatch.setenv("RELAY_DEFAULT_CWD", str(tmp_path))
    sid = "sess-1"
    (tmp_path / sid).mkdir()
    got = WorkspaceService.ensure_workspace_exists(sid, cwd=None)
    assert got == str(tmp_path / sid)


# ── 业务方法 cwd 直传透传 ─────────────────────────────────────────────────


async def test_list_file_tree_forwards_cwd_as_workspace_root(
    tmp_path: Path, monkeypatch
) -> None:
    """cwd 直传时以 cwd 为 workspace 根列文件树（跳过 base/{sid} 拼接）。"""
    custom = tmp_path / "custom-cwd"
    custom.mkdir()
    monkeypatch.setenv("AICODING_CWD_ALLOW_ROOTS", str(tmp_path))
    file_plugin = FakeFilePlugin(
        list_result=ListDirResult(
            dir_path=str(custom), recursive=True, files=[],
        )
    )
    service = _make_service(file_plugin=file_plugin)
    await service.list_file_tree("sid", cwd=str(custom))
    assert file_plugin.calls[0][1]["dir_path"] == str(custom)


async def test_preview_file_forwards_cwd_as_workspace_root(
    tmp_path: Path, monkeypatch
) -> None:
    """cwd 直传时 preview 的 workspace 根切换到 cwd，path 相对 cwd 解析。"""
    custom = tmp_path / "custom-cwd"
    custom.mkdir()
    monkeypatch.setenv("AICODING_CWD_ALLOW_ROOTS", str(tmp_path))
    target = str(custom / "a.txt")
    file_plugin = FakeFilePlugin(read_map={target: b"hi"})
    service = _make_service(file_plugin=file_plugin)
    out = await service.preview_file("sid", "a.txt", cwd=str(custom))
    assert out.content == "hi"
    assert file_plugin.calls[0][1]["file_path"] == target


# ── cwd 白名单收紧：根路径 / 不得被掏成空串（gemini code review PR#132 HIGH）──


def test_strip_trailing_sep_preserves_root_and_never_empties() -> None:
    """``rstrip("/")`` 会把根 ``/`` 与 POSIX 双斜杠 ``//``（normpath 不归一）掏成
    空串，使前缀比较 ``"" + "/" == "/"`` 让任何绝对路径过关。helper 必须把全斜杠
    输入归一到 ``/``，永不返回空串。"""
    from engine.community.core.aicoding.workspace_service import _strip_trailing_sep

    assert _strip_trailing_sep("/") == "/"
    assert _strip_trailing_sep("//") == "/"
    assert _strip_trailing_sep("///") == "/"
    # 普通路径只去末尾斜杠
    assert _strip_trailing_sep("/home/admin/") == "/home/admin"
    assert _strip_trailing_sep("/home/admin") == "/home/admin"
    assert _strip_trailing_sep("/foo//") == "/foo"


def test_allowed_cwd_roots_drops_root_slash(monkeypatch, caplog) -> None:
    """配 ``=/`` 时根路径被显式丢弃，extras 不含 ``/`` 也不含 ``""``，并打 warning。"""
    import logging
    from engine.community.core.aicoding.workspace_service import _allowed_cwd_roots

    monkeypatch.setenv("AICODING_CWD_ALLOW_ROOTS", "/")
    with caplog.at_level(logging.WARNING, logger="aicoding-workspace"):
        roots = _allowed_cwd_roots()
    assert roots == (CONTAINER_WORKSPACE_BASE,)
    assert "" not in roots and "/" not in roots
    assert any("ignoring root path" in r.message for r in caplog.records)


def test_allowed_cwd_roots_drops_path_normalizing_to_root(monkeypatch) -> None:
    """normpath 后等于根 ``/`` 的伪装根（``/foo/..``、``//``、``/.``、``/..``）
    同样应被丢弃，不能以 ``""`` 形式混入 extras 导致白名单失效。"""
    from engine.community.core.aicoding.workspace_service import _allowed_cwd_roots

    for bad in ("/foo/..", "//", "/.", "/.."):
        monkeypatch.setenv("AICODING_CWD_ALLOW_ROOTS", bad)
        roots = _allowed_cwd_roots()
        assert roots == (CONTAINER_WORKSPACE_BASE,), bad
        assert "" not in roots and "/" not in roots


def test_validate_cwd_prefix_rejects_double_slash_root_configured(
    monkeypatch,
) -> None:
    """配 ``=//`` 后系统路径仍被拒——对抗验证发现的链式回归核心：若
    ``_strip_trailing_sep('//')`` 返回 ``""``，前缀比较会让任何绝对路径过关。
    helper 归一 ``//`` 为 ``/`` 后被 ``_allowed_cwd_roots`` 丢弃，白名单未失效。"""
    monkeypatch.setenv("AICODING_CWD_ALLOW_ROOTS", "//")
    for bad in ("/etc", "/root", "/"):
        with pytest.raises(ValueError, match="cwd not allowed"):
            WorkspaceService._validate_cwd_prefix(bad)


def test_validate_cwd_prefix_rejects_system_paths_when_root_configured(
    monkeypatch,
) -> None:
    """配 ``=/``（根路径被丢弃）后系统路径 /etc、/etc/passwd、/root、/var/log 仍被拒，
    白名单不会因运维把允许根配成根 而失效。"""
    monkeypatch.setenv("AICODING_CWD_ALLOW_ROOTS", "/")
    for bad in ("/etc", "/etc/passwd", "/root", "/var/log"):
        with pytest.raises(ValueError, match="cwd not allowed"):
            WorkspaceService._validate_cwd_prefix(bad)


def test_validate_cwd_prefix_rejects_root_slash_as_cwd(monkeypatch) -> None:
    """cwd 直传 ``/`` 本身被拒（``_strip_trailing_sep`` 保留 ``/``，不与任何非根
    允许根相等或前缀匹配）；含存在性的 :meth:`validate_cwd` 也在前缀校验阶段被拒。"""
    monkeypatch.delenv("AICODING_CWD_ALLOW_ROOTS", raising=False)
    with pytest.raises(ValueError, match="cwd not allowed"):
        WorkspaceService._validate_cwd_prefix("/")
    with pytest.raises(ValueError, match="cwd not allowed"):
        WorkspaceService.validate_cwd("/")
