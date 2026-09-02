# W7 — 命名源与 git 源 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** manifest 条目通过 `from` 引用命名源，git 仓库成为一等公民源——一次浅层单 ref fetch 把整份配置解析到同一个 commit，strict 模式在 ref 漂移时拒绝下发。

**Architecture:** 三层增量，不动既有契约——schema（W1）已全部就绪；`EntryFetcher` 加 `fetch_declared` 完成 `from` 解析与 URL/git 分派；新模块 `fetch/git_source.py`（subprocess git CLI）+ `apply/source_session.py`（per-apply 状态，挂 `ApplyContext`，同 `budget` 先例）。语法严格依据 spec：`docs/superpowers/specs/2026-09-02-bot-config-manifest-w7-git-sources-design.md`。

**Tech Stack:** Python 3.12、pytest、subprocess git（沿用 `skill_center/services/git_sync.py` 先例，零新依赖）。

**关键取证事实（写代码前不需要重查）：**

- `EntryFetcher` 是 DI **单例**（`di/modules/manifest_fetch_module.py:123`）——per-apply 状态一律走 `ctx.source_session`，绝不放 fetcher 实例上。
- `ApplyReport.sources: tuple[SourceResolution, ...]` 与 `outcomes.SourceResolution(name, ref, resolved_sha, auth)` 是 W5 预留给 W7 的空位；服务已有 `last_apply()`（`config_manifest_apply_service.py:500`）——strict 基线零迁移。
- `EntryFetcher.fetch(ctx, *, source_url, digest, auth, category, keep_last, entry_identity)` 是现有 URL 路；materialiser 用 `asyncio.to_thread` 调它。
- `FakeGuardedFetcher / FakeManifestContent / FakeCredentials / make_context / fetched_object` 全在 `apply/_fakes.py`。
- 测试命令在仓库根 `src/backend/` 下跑；全部测试路径相对 `src/backend/tests/community/core/bot_config_manifest/`。

**字符集注意：** 全部新文件用英文注释/docstring（仓库现状：`English comments throughout`，见 commit `3c4f2a415`）。

---

## File Structure

| 文件 | 动作 | 职责 |
| --- | --- | --- |
| `core/bot_config_manifest/fetch/limits.py` | 修改 | 加 4 个 git checkout 常量 |
| `core/bot_config_manifest/fetch/git_source.py` | **新建** | `GitSourceSpec` / `GitSourceClient` protocol / `SubprocessGitClient` / `GitCheckout` / `git_receipt_url` |
| `core/bot_config_manifest/apply/source_session.py` | **新建** | per-apply `SourceSession`：源声明、基线、checkout 缓存、resolution 记录 |
| `core/bot_config_manifest/apply/context.py` | 修改 | 加 `source_session` 字段 |
| `core/bot_config_manifest/apply/entry_fetch.py` | 修改 | 加 `fetch_declared` / `file_bytes` / `GitEntrySource`；`FetchedEntry` 加 `source_url` |
| `core/bot_config_manifest/apply/orchestrator.py` | 修改 | 报告填 `sources` |
| `core/bot_config_manifest/apply/materialisers/identity.py` | 修改 | 选择器重构 + git 单文件路 |
| `core/bot_config_manifest/apply/materialisers/skills.py` | 修改 | 选择器重构 + git 树路 `_git_package` |
| `core/bot_config_manifest/services/config_manifest_apply_service.py` | 修改 | session 构造/关闭、基线读取、`git_client_provider` |
| `di/modules/manifest_fetch_module.py` | 修改 | `GitSourceClient` provider + 延迟工厂 |
| `tests/.../fetch/test_git_source.py` | **新建** | 真实本地仓库后端 + argv 形状测试 |
| `tests/.../apply/test_source_session.py` | **新建** | 缓存/记录/基线/关闭 |
| `tests/.../apply/test_entry_fetch.py` | 修改 | `fetch_declared` 全套 |
| `tests/.../apply/test_identity_materialiser.py` | 修改 | git 路 |
| `tests/.../apply/test_skills_materialiser.py` | 修改 | git 路 |
| `tests/.../apply/test_apply_service_lifecycle.py` | 修改 | 构造参数 + 报告 sources 端到端 |
| `tests/.../apply/_fakes.py` | 修改 | 加 `FakeGitClient` |

（下文路径省略前缀 `<R> = src/backend/src/agentclaw/community/core/bot_config_manifest`，`<T> = src/backend/tests/community/core/bot_config_manifest`，`cwd` 一律 `src/backend/`。）

---

### Task 1: limits 的 git checkout 常量

**Files:**
- Modify: `<R>/fetch/limits.py`
- Test: `<T>/fetch/test_git_source.py`（新建，作为该模块测试文件的第一个用例）

- [ ] **Step 1: Write the failing test**

新建 `<T>/fetch/test_git_source.py`：

```python
"""Tests for git sources (``fetch/git_source.py``, W7, #1475).

Two kinds of tests live here, and the split is deliberate:

* **argv-shape tests** inject a fake runner and assert what the git CLI is
  asked to do — scheme guard, credential placement, no prompts. They exist
  because the https wire itself is not reachable from a test environment,
  and because "where the credential travels" is a property of the argv, not
  of the network.
* **behaviour tests** run real ``git`` subprocesses against bare repos
  created on disk by the fixtures — the same local-repo precedent as the
  schema suite's archive fixtures, and the only honest way to test that a
  shallow fetch, ref resolution and tree enumeration really work.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    ARCHIVE_MEMBER_LIMIT,
    FETCH_ENTRY_LIMITS,
    GIT_CHECKOUT_MEMBER_LIMIT,
    GIT_CHECKOUT_UNPACKED_LIMIT,
    GIT_FETCH_TIMEOUT_S,
    GIT_SINGLE_FILE_LIMIT,
)


def test_git_limits_align_with_the_archives_they_generalise():
    # A checkout is the same class of hazard as an unpacked archive: the
    # numbers align rather than invent a second dialect that drifts.
    assert GIT_CHECKOUT_UNPACKED_LIMIT == FETCH_ENTRY_LIMITS["resources_unpacked"]
    assert GIT_CHECKOUT_MEMBER_LIMIT == ARCHIVE_MEMBER_LIMIT
    assert GIT_SINGLE_FILE_LIMIT == FETCH_ENTRY_LIMITS["skills"]
    assert GIT_FETCH_TIMEOUT_S > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/community/core/bot_config_manifest/fetch/test_git_source.py -x -q`（cwd `src/backend/`）
Expected: FAIL — `ImportError: cannot import name 'GIT_CHECKOUT_UNPACKED_LIMIT'`

- [ ] **Step 3: Write minimal implementation**

在 `<R>/fetch/limits.py` 的 `MAX_REDIRECTS` 常量块之后（`SAFE_SCHEMES` 之前）追加：

```python
#: W7 — a git checkout's budget. The wire cap above counts bytes in flight;
#: a small pack can bloom into a giant tree on disk, so git content gets its
#: own post-unpack caps, aligned with the archive vocabulary rather than a
#: second dialect of numbers that drifts. Enforced by ``fetch/git_source.py``
#: while the checkout is read, refused before any byte reaches the entry.
GIT_CHECKOUT_UNPACKED_LIMIT = FETCH_ENTRY_LIMITS["resources_unpacked"]
GIT_CHECKOUT_MEMBER_LIMIT = ARCHIVE_MEMBER_LIMIT
GIT_SINGLE_FILE_LIMIT = FETCH_ENTRY_LIMITS["skills"]

#: A git fetch is one network operation with disk work after it, so it gets
#: more than a single HTTP hop's ``FETCH_TIMEOUT_S`` but stays inside the
#: apply-level ``APPLY_BUDGET_S`` ledger that is charged around it.
GIT_FETCH_TIMEOUT_S = 120.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/community/core/bot_config_manifest/fetch/test_git_source.py -x -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/bot_config_manifest/fetch/limits.py \
        tests/community/core/bot_config_manifest/fetch/test_git_source.py
git commit -m "feat(w7): git checkout limits, aligned with the archive numbers"
```

---

### Task 2: `GitSourceSpec` 与 `SubprocessGitClient`——fetch、解析、凭证注入、拒绝面

**Files:**
- Create: `<R>/fetch/git_source.py`
- Test: `<T>/fetch/test_git_source.py`（追加）

- [ ] **Step 1: Write the failing tests**

追加到 `<T>/fetch/test_git_source.py`：

```python
from agentclaw.community.core.bot_config_manifest.fetch.git_source import (
    GitCheckout,
    GitSourceClient,
    GitSourceSpec,
    SubprocessGitClient,
    _GitCommandError,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchFailedError,
    FetchRefusedError,
)

class _RecordingRunner:
    """Stands in for ``_real_run_git``: captures argv, answers scripted stdout."""

    def __init__(self, *, sha: str = "f" * 40, tree: str = "") -> None:
        self.argvs: list[list[str]] = []
        self.timeout: float | None = None
        self._sha = sha
        self._tree = tree

    def __call__(self, argv, *, cwd, timeout):
        self.argvs.append(list(argv))
        self.timeout = timeout
        if argv[1] == "rev-parse":
            return self._sha
        if argv[1] == "ls-tree":
            return self._tree
        return ""


def test_non_https_git_url_is_refused_before_any_subprocess():
    client = SubprocessGitClient(_run=_RecordingRunner())
    with pytest.raises(FetchRefusedError, match="https"):
        client.fetch(GitSourceSpec(url="http://git.corp/repo.git", ref="main"))


def test_credentials_travel_in_argv_config_never_in_the_remote_or_url():
    runner = _RecordingRunner(tree="100644 blob " + "a" * 40 + "\tpkg/skill.md\n")
    client = SubprocessGitClient(_run=runner)
    client.fetch(
        GitSourceSpec(url="https://git.corp/repo.git", ref="main"),
        headers={"Authorization": "Basic c2VjcmV0"},
    )
    fetch_argv = next(a for a in runner.argvs if a[1] == "fetch")
    assert any(
        part.startswith("http.extraHeader=Authorization: Basic")
        for part in fetch_argv
    ), fetch_argv
    # The credential rides the -c config, and nowhere else: not the remote,
    # not the fetch operands, not any other invocation.
    operands = fetch_argv[fetch_argv.index("fetch") + 1:]
    assert "Basic" not in " ".join(operands)
    remote_add = next(a for a in runner.argvs if a[1 : 3] == ["remote", "add"])
    assert "Basic" not in " ".join(remote_add)


def test_a_failed_git_command_is_a_fetch_failure_and_cleans_up(monkeypatch, tmp_path):
    class _Failing:
        def __init__(self):
            self.seen = 0

        def __call__(self, argv, *, cwd, timeout):
            self.seen += 1
            if argv[1] == "fetch":
                raise _GitCommandError("fetch", "remote: not found")
            return "" if argv[1] != "rev-parse" else "f" * 40

    leftovers_before = set(p.name for p in Path("/tmp").glob("manifest-git-*"))
    client = SubprocessGitClient(_run=_Failing())
    with pytest.raises(FetchFailedError, match="git fetch failed"):
        client.fetch(GitSourceSpec(url="https://git.corp/repo.git", ref="main"))
    leftovers_after = set(p.name for p in Path("/tmp").glob("manifest-git-*"))
    assert leftovers_after == leftovers_before, "a failed fetch must leave no temp dir"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/community/core/bot_config_manifest/fetch/test_git_source.py -x -q`
Expected: FAIL — `ImportError: cannot import name 'git_source'`

- [ ] **Step 3: Write minimal implementation**

新建 `<R>/fetch/git_source.py`（本 Task 只交付 spec/client/runner；`GitCheckout` 与树检查在 Task 3 补齐，先以占位最小实现让 fetch 返回已含 members 的 checkout）：

```python
"""Git sources — a shallow single-ref fetch through the git CLI (W7, #1475).

The contract is **git over HTTPS with header-injected credentials** — W3's
``header`` credential type — and it is deliberately host-agnostic (work-items
§W7: any HTTPS-reachable git service satisfies it). The implementation shells
out to ``git`` the way ``skill_center/services/git_sync.py`` already does,
rather than adding a pure-Python client dependency the corp dependency mirror
would have to carry too.

Three properties the shape of this module guarantees:

* **Refusal before the wire** — an https-only scheme check happens before any
  subprocess, so a refused scheme looks like W2's ``FetchRefusedError``, the
  class ``keep_last`` may not answer.
* **Read-only by construction** — ``fetch`` never evaluates server-controlled
  input: no hooks run on a fetch, no filters are requested, and the tree that
  comes back is enumerated *before* it is read, so a symlink or submodule
  member is refused before any platform code could follow it.
* **The credential stays in argv config** — ``-c http.extraHeader=...`` on the
  fetch invocation only. Never the URL (it would persist in receipts), never
  the environment (it would leak to child processes that read the whole env).

A failed or refused fetch removes its temporary directory before raising; a
successful one keeps it alive until the apply's `SourceSession` closes, and
the caller owns that lifetime.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
from types import MappingProxyType
from typing import Callable, Mapping, Optional, Protocol, Sequence, Tuple
from urllib.parse import urlparse

from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchFailedError,
    FetchRefusedError,
)
from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    GIT_CHECKOUT_MEMBER_LIMIT,
    GIT_FETCH_TIMEOUT_S,
)
from agentclaw.community.log import get_logger

logger = get_logger()

#: A full commit id, exactly as ``rev-parse FETCH_HEAD`` answers it.
_SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")

#: The index modes a checkout may contain: plain files only. ``120000`` is a
#: symlink (``payload -> /etc`` is exactly the W2 hazard), ``160000`` is a
#: gitlink/submodule — both refused before any byte is read.
_ALLOWED_MODES = frozenset({"100644", "100755"})


@dataclass(frozen=True)
class GitSourceSpec:
    """One declared git source, wherever it is written (inline or ``sources``).

    ``auth`` is a credential **name** (W3's identifier), never a value.
    """

    url: str
    ref: str = "HEAD"
    subpath: Optional[str] = None
    mode: str = "non_strict"
    auth: Optional[str] = None


def git_receipt_url(url: str, sha: str, subpath: Optional[str]) -> str:
    """The canonical pseudo-URL a git-sourced entry's W11 receipt is filed under.

    Keyed on the *resolved* SHA: a ref that moved is a different address, so
    ``keep_last`` cannot be fooled into reusing the old record for a new
    commit, and two entries reading different paths out of one commit do not
    collide.
    """
    return f"git+{url}@{sha}:{subpath or ''}"


class _GitCommandError(Exception):
    """One git invocation failed; the CLI's stderr text is kept for the message."""

    def __init__(self, step: str, stderr: str) -> None:
        self.step = step
        self.stderr = stderr
        super().__init__(f"{step}: {stderr}")


def _real_run_git(argv: Sequence[str], *, cwd: Path, timeout: float) -> str:
    """One git invocation: argv as a list (no shell), stdout back, or the
    CLI's own failure as ``_GitCommandError``.

    ``GIT_TERMINAL_PROMPT=0`` and the two ``GIT_CONFIG_*_NOSYSTEM``-era
    overrides keep the invocation hermetic: a git prompt would hang an apply
    thread to its timeout, and a user-authored ``insteadOf`` rewrite in
    ~/.gitconfig would silently redirect this fetch somewhere the document
    never named — a credential-leaking hole, not a convenience.
    """
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/true",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }
    try:
        completed = subprocess.run(  # noqa: S603 - argv is list-built in this module
            list(argv), cwd=cwd, capture_output=True, text=True,
            timeout=timeout, env=env, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise _GitCommandError(argv[1], "timed out") from exc
    if completed.returncode != 0:
        raise _GitCommandError(argv[1], completed.stderr.strip() or "failed")
    return completed.stdout


@dataclass
class GitCheckout:
    """One fetched commit's tree, on disk, already proven safe to read.

    ``members`` is the tree enumerated by ``git ls-tree`` at fetch time —
    every member a plain file — and the readers below walk **only** those
    paths, so nothing outside the enumeration is ever touched.
    """

    root: Path
    sha: str
    url: str
    ref: str
    members: Tuple[Tuple[str, str], ...] = ()  # (mode, path) — Task 3 fills this

    # ── the readers (Task 3) ────────────────────────────────────────────────


class GitSourceClient(Protocol):
    """The seam the apply pipeline speaks to; ``SubprocessGitClient`` is v1's
    only implementation, and tests duck-type it."""

    def fetch(
        self, spec: GitSourceSpec, *, headers: Mapping[str, str] = ...
    ) -> GitCheckout: ...


class SubprocessGitClient:
    """Shallow single-ref fetch through the git CLI.

    ``_run`` is the test seam (argv-shape tests script it; behaviour tests
    use the default, which really spawns git). ``allowed_schemes`` is the
    deployment's git-transport dialect — https in production, widened by the
    local-fixture tests to ``file`` so real bare repos on disk can serve a
    fetch; it admits *destinations*, never behaviors.
    """

    def __init__(
        self,
        _run: Callable[..., str] = _real_run_git,
        *,
        allowed_schemes: frozenset[str] = frozenset({"https"}),
    ) -> None:
        self._run = _run
        self._allowed_schemes = allowed_schemes

    def fetch(
        self, spec: GitSourceSpec, *, headers: Mapping[str, str] = MappingProxyType({})
    ) -> GitCheckout:
        if urlparse(spec.url).scheme not in self._allowed_schemes:
            # Before any subprocess: a refused scheme is configuration, the
            # class of failure keep_last must not mask (W5's ruling).
            raise FetchRefusedError(
                f"git source url must be https, got {spec.url!r}"
            )
        if not spec.ref:
            raise FetchRefusedError("'ref' must be a non-empty string")

        root = Path(mkdtemp(prefix="manifest-git-"))
        config: list[str] = []
        for key, value in headers.items():
            config += ["-c", f"http.extraHeader={key}: {value}"]
        try:
            self._git(["init", "--quiet"], cwd=root)
            self._git(["remote", "add", "origin", spec.url], cwd=root)
            self._git(
                [*config, "fetch", "--quiet", "--depth=1", "origin", spec.ref],
                cwd=root,
            )
            sha = self._git(["rev-parse", "FETCH_HEAD"], cwd=root).strip()
            if not _SHA_RE.match(sha):
                raise FetchFailedError(
                    f"git fetch of ref {spec.ref!r} produced no commit id"
                )
            tree_out = self._git(
                ["ls-tree", "-r", "FETCH_HEAD"], cwd=root
            )
            members = self._enumerate(tree_out)
            self._git(
                ["checkout", "--quiet", "--detach", "FETCH_HEAD"], cwd=root
            )
            logger.info(
                "[manifest.git] fetched host=%s ref=%s sha=%s files=%s",
                urlparse(spec.url).netloc, spec.ref, sha, len(members),
            )
            return GitCheckout(
                root=root, sha=sha, url=spec.url, ref=spec.ref, members=members
            )
        except FetchRefusedError:
            self._discard(root)
            raise
        except _GitCommandError as exc:
            self._discard(root)
            raise FetchFailedError(f"git {exc}") from exc
        except Exception:
            # Anything else (disk, os) still must not leak the temp dir.
            self._discard(root)
            raise

    def _git(self, argv: Sequence[str], *, cwd: Path) -> str:
        return self._run(["git", *argv], cwd=cwd, timeout=GIT_FETCH_TIMEOUT_S)

    def _enumerate(self, tree_out: str) -> Tuple[Tuple[str, str], ...]:
        """``ls-tree -r`` output → (mode, path) members, with the refusals."""
        members: list[tuple[str, str]] = []
        for line in tree_out.splitlines():
            meta, _, name = line.partition("\t")
            parts = meta.split()
            if len(parts) != 3 or not name:
                raise FetchFailedError("git tree listing was malformed")
            mode = parts[0]
            if mode not in _ALLOWED_MODES:
                raise FetchRefusedError(
                    f"git source contains a forbidden member {name!r}: "
                    "symlinks and submodules are refused"
                )
            members.append((mode, name))
        if not members:
            raise FetchRefusedError("the git ref resolved to an empty tree")
        if len(members) > GIT_CHECKOUT_MEMBER_LIMIT:
            raise FetchRefusedError(
                f"git tree exceeds the {GIT_CHECKOUT_MEMBER_LIMIT}-member cap"
            )
        return tuple(members)

    @staticmethod
    def _discard(root: Path) -> None:
        import shutil

        shutil.rmtree(root, ignore_errors=True)
```

注意：`GitCheckout` 本 task 先以带默认值的 `members` 字段落形——严格脚本在本
task 的 argv 测试里不读 members，下一 task 补读者。同原因本测试的
`client.fetch(...)`（credentials 测试）不发 `ls-tree` 解析失败——`_RecordingRunner`
scripted tree 给了一条合法行。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/community/core/bot_config_manifest/fetch/test_git_source.py -x -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/bot_config_manifest/fetch/git_source.py \
        tests/community/core/bot_config_manifest/fetch/test_git_source.py
git commit -m "feat(w7): subprocess git client with refusal-first scheme guard"
```

---

### Task 3: `GitCheckout` 读者——包含性、限额、subpath、清理

**Files:**
- Modify: `<R>/fetch/git_source.py`（补 `GitCheckout.files/read_file` 与 subpath 安全）
- Test: `<T>/fetch/test_git_source.py`（追加真实仓库行为测试）

- [ ] **Step 1: Write the failing tests**

追加（真实 git 子进程；`SubprocessGitClient` 的 `allowed_schemes` 测试放宽到 `file`）：

```python
def _git(argv: list[str], *, cwd: Path) -> str:
    return subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def _serve_repo(
    tmp_path: Path,
    *,
    allow_any_sha: bool = False,
    symlink_member: bool = False,
    gitlink_member: bool = False,
) -> Path:
    """A bare repo on disk serving one committed tree over file://."""
    work = tmp_path / "src"
    (work / "pkg").mkdir(parents=True)
    (work / "pkg" / "skill.md").write_text("# a skill\n")
    (work / "root.txt").write_text("root\n")
    _git(["git", "init", "--quiet", "-b", "main"], cwd=work)
    if symlink_member:
        (work / "pkg" / "escape").symlink_to("/etc")
    if gitlink_member:
        # A gitlink row without its submodule contents: exactly the shape a
        # submodule author leaves in the index.
        _git(
            ["git", "update-index", "--add", "--cacheinfo",
             "160000,1111111111111111111111111111111111111111,vendor/sub"],
            cwd=work,
        )
    _git(["git", "add", "."], cwd=work)
    _git(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--quiet", "-m", "one"],
        cwd=work,
    )
    bare = tmp_path / "bare.git"
    subprocess.run(
        ["git", "clone", "--quiet", "--bare", str(work), str(bare)],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    if allow_any_sha:
        _git(["git", "config", "uploadpack.allowAnySHA1InWant", "true"], cwd=bare)
    return bare


def _client() -> SubprocessGitClient:
    return SubprocessGitClient(allowed_schemes=frozenset({"https", "file"}))


@pytest.fixture
def serve(tmp_path):
    return _serve_repo(tmp_path)


def test_fetch_by_branch_and_read_the_tree(serve):
    bare = serve
    checkout = _client().fetch(
        GitSourceSpec(url=f"file://{bare}", ref="main", subpath="pkg")
    )
    assert checkout.sha
    assert checkout.files() == [("skill.md", b"# a skill\n")]


def test_read_file_requires_the_subpath_to_name_exactly_one_file(serve):
    checkout = _client().fetch(
        GitSourceSpec(url=f"file://{serve}", ref="main", subpath="pkg")
    )
    with pytest.raises(FetchRefusedError, match="directory"):
        checkout.read_file()
    checkout_file = _client().fetch(
        GitSourceSpec(url=f"file://{serve}", ref="main", subpath="root.txt")
    )
    assert checkout_file.read_file() == b"root\n"


def test_subpath_escape_is_refused(serve):
    checkout = _client().fetch(
        GitSourceSpec(url=f"file://{serve}", ref="main")
    )
    for bad in ("../root.txt", "/etc/passwd", "pkg/../../root.txt"):
        with pytest.raises(FetchRefusedError):
            checkout.files(bad)


def test_a_symlink_member_is_refused(tmp_path):
    bare = _serve_repo(tmp_path, symlink_member=True)
    with pytest.raises(FetchRefusedError, match="forbidden member"):
        _client().fetch(GitSourceSpec(url=f"file://{bare}", ref="main"))


def test_a_gitlink_member_is_refused(tmp_path):
    bare = _serve_repo(tmp_path, gitlink_member=True)
    with pytest.raises(FetchRefusedError, match="forbidden member"):
        _client().fetch(GitSourceSpec(url=f"file://{bare}", ref="main"))


def test_unknown_ref_is_a_fetch_failure(serve):
    with pytest.raises(FetchFailedError):
        _client().fetch(GitSourceSpec(url=f"file://{serve}", ref="no-such-branch"))


def test_a_sha_ref_fetches_when_the_server_allows_it(tmp_path):
    bare = _serve_repo(tmp_path, allow_any_sha=True)
    first = _client().fetch(GitSourceSpec(url=f"file://{bare}", ref="main"))
    again = _client().fetch(
        GitSourceSpec(url=f"file://{bare}", ref=first.sha)
    )
    assert again.sha == first.sha


def test_a_moved_tag_resolves_to_the_new_commit(tmp_path):
    bare = _serve_repo(tmp_path)
    first = _client().fetch(GitSourceSpec(url=f"file://{bare}", ref="main"))
    _git(["git", "tag", "release"], cwd=bare)
    moved = tmp_path / "move"
    moved.mkdir()
    (moved / "new.txt").write_text("new\n")
    # Push a second commit through a fresh clone, then move the tag.
    work2 = tmp_path / "src2"
    subprocess.run(
        ["git", "clone", "--quiet", str(bare), str(work2)],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    import shutil as _sh

    _sh.copy(moved / "new.txt", work2 / "new.txt")
    _git(["git", "add", "."], cwd=work2)
    _git(["git", "-c", "user.email=t@t", "-c", "user.name=t",
          "commit", "--quiet", "-m", "two"], cwd=work2)
    _git(["git", "push", "--quiet", "origin", "main"], cwd=work2)
    _git(["git", "tag", "-f", "release"], cwd=work2)
    _git(["git", "push", "--quiet", "-f", "origin", "release"], cwd=work2)
    second = _client().fetch(GitSourceSpec(url=f"file://{bare}", ref="release"))
    assert second.sha != first.sha
    assert any(path == "new.txt" for _, path in second.members)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/community/core/bot_config_manifest/fetch/test_git_source.py -q`
Expected: 新增用例 FAIL 于 `AttributeError: 'GitCheckout' object has no attribute 'files'`（`subpath_escape` 用例先行失败即算）

- [ ] **Step 3: Write minimal implementation**

在 `<R>/fetch/git_source.py` 的 `GitCheckout` 内替换 `# ── the readers (Task 3)` 注释：

```python
    # ── the readers ─────────────────────────────────────────────────────────
    #
    # Only enumerated members are read, only from this checkout's own root,
    # and only inside the limits: the enumeration proved every member a plain
    # file at fetch time, so a reader cannot be walked out of the temp dir by
    # anything the repository author left in the tree.

    def files(
        self, subpath: Optional[str] = None
    ) -> list[tuple[str, bytes]]:
        """Every file under ``subpath`` as (relative path, bytes).

        Raises ``FetchRefusedError`` — the pre-wire class — for an unsafe
        subpath, an empty selection, or a limit blown while reading.
        """
        relative_members = [
            (mode, name, _under_subpath(name, subpath))
            for mode, name in self.members
        ]
        selected = [(mode, rel, name) for mode, name, rel in relative_members if rel]
        if not selected:
            raise FetchRefusedError(
                f"the git tree contains nothing under subpath {subpath!r}"
            )
        out: list[tuple[str, bytes]] = []
        total = 0
        for mode, rel, name in selected:
            payload = self._read_member(name, mode)
            total += len(payload)
            if total > GIT_CHECKOUT_UNPACKED_LIMIT:
                from agentclaw.community.core.bot_config_manifest.fetch.limits import (
                    GIT_CHECKOUT_UNPACKED_LIMIT as _CAP,
                )

                raise FetchRefusedError(
                    f"git checkout exceeds the {_CAP}-byte unpacked cap"
                )
            out.append((rel, payload))
        return out

    def read_file(self, subpath: Optional[str] = None) -> bytes:
        """The single file a subpath names — the identity-category road."""
        _require_safe("subpath", subpath)
        if subpath is None:
            raise FetchRefusedError(
                "read_file: the source's subpath must name exactly one file"
            )
        hit = [(mode, name) for mode, name in self.members if name == subpath]
        if not hit:
            raise FetchRefusedError(
                f"the git tree has no file at subpath {subpath!r}"
            )
        mode, name = hit[0]
        payload = self._read_member(name, mode)
        if len(payload) > GIT_SINGLE_FILE_LIMIT:
            raise FetchRefusedError(
                "a single git tree file exceeds the "
                f"{GIT_SINGLE_FILE_LIMIT}-byte cap: {subpath!r}"
            )
        return payload

    def _read_member(self, name: str, mode: str) -> bytes:
        if not _is_plain_name(name):
            raise FetchRefusedError(f"refusing to read git member {name!r}")
        target = (self.root / name).resolve()
        if self.root.resolve() not in target.parents and target != self.root.resolve():
            if not str(target).startswith(str(self.root.resolve()) + os.sep):
                raise FetchRefusedError(
                    f"git member {name!r} resolves outside the checkout"
                )
        return target.read_bytes()
```

并在模块级（`_ALLOWED_MODES` 定义之后）加辅助函数：

```python
def _require_safe(what: str, value: Optional[str]) -> None:
    """A subpath is refused if absolute or escaping — the PUT-time rule
    re-asked at apply time, because a stored document may have drifted."""
    if value is None:
        return
    if value.startswith("/") or value.startswith("\\"):
        raise FetchRefusedError(f"{what} must be relative, got {value!r}")
    parts = value.replace("\\", "/").split("/")
    if ".." in parts:
        raise FetchRefusedError(f"{what} may not contain '..': {value!r}")


def _is_plain_name(name: str) -> bool:
    if name.startswith("/") or ".." in name.replace("\\", "/").split("/"):
        return False
    return not name.startswith("~")


def _under_subpath(member: str, subpath: Optional[str]) -> Optional[str]:
    """The member's path relative to ``subpath`` (segment-boundary matched),
    or ``None`` when outside it — the skills materialiser's own arithmetic,
    on members the enumeration already proved plain."""
    _require_safe("subpath", subpath)
    if subpath is None or subpath == "":
        return member
    prefix = subpath.replace("\\", "/").strip("/")
    parts = member.split("/")
    mine = [p for p in prefix.split("/") if p]
    if len(parts) <= len(mine):
        return None
    if parts[: len(mine)] != mine:
        return None
    return "/".join(parts[len(mine) :])
```

`GitCheckout.files` 里对 unpacked cap 的写法也统一为：

```python
        out: list[tuple[str, bytes]] = []
        total = 0
        for mode, rel, name in selected:
            payload = self._read_member(name, mode)
            total += len(payload)
            if total > GIT_CHECKOUT_UNPACKED_LIMIT:
                raise FetchRefusedError(
                    "git checkout exceeds the "
                    f"{GIT_CHECKOUT_UNPACKED_LIMIT}-byte unpacked cap"
                )
            out.append((rel, payload))
        return out
```

并把 `files` 的 selected 计算改为：

```python
        selected = [
            (mode, _under_subpath(name, subpath), name)
            for mode, name in self.members
        ]
        selected = [(m, r, n) for m, r, n in selected if r is not None]
```

（`_require_safe` 已由 `_under_subpath` 在逐成员时调用；成员遍历前若 `members` 为空已被 `_enumerate` 拒绝。）

`limits` 导入行更新为包含 `GIT_CHECKOUT_UNPACKED_LIMIT, GIT_SINGLE_FILE_LIMIT`（Task 2 的导入只带了两个常量，补全 `from ... import GIT_CHECKOUT_MEMBER_LIMIT, GIT_CHECKOUT_UNPACKED_LIMIT, GIT_FETCH_TIMEOUT_S, GIT_SINGLE_FILE_LIMIT`）。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/community/core/bot_config_manifest/fetch/test_git_source.py -q`
Expected: PASS（全部，含 Task 1–2 用例）

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/bot_config_manifest/fetch/git_source.py \
        tests/community/core/bot_config_manifest/fetch/test_git_source.py
git commit -m "feat(w7): guarded tree readers for the git checkout"
```

---

### Task 4: `SourceSession`——per-apply 缓存、resolution 记录、基线、关闭

> **CLAIMED BY B(协作会话)@2026-09-02 16:45** — B 正在交付本任务(文件:`apply/source_session.py`、`test_source_session.py`)。A 轨**请勿派发本任务**;`git status` 里这两个文件是 B 的 WIP,不碰、不提交。协调细节见文末「双 agent 协作约定」。

**Files:**
- Create: `<R>/apply/source_session.py`
- Test: `<T>/apply/test_source_session.py`（新建）

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the per-apply source session (``apply/source_session.py``, W7).

The session is where one apply's named-source state lives — the checkout
cache, the resolutions the report will carry, and the strict-mode baselines
read back from the last apply's report. It hangs on ``ApplyContext`` the same
way ``budget`` does: mutable by design inside a frozen context, because the
alternatives were per-fetcher state (the fetcher is a DI singleton — state
there leaks across applies) or re-derivation per entry (which would break
"one {git, ref} pulled once per apply").
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    SourceResolution,
)
from agentclaw.community.core.bot_config_manifest.apply.source_session import (
    SourceSession,
)
from agentclaw.community.core.bot_config_manifest.fetch.git_source import (
    FetchFailedError,
    GitSourceSpec,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchFailedError,
    FetchRefusedError,
)


class FakeGitClient:
    """The git seam, scripted per (url, ref); records what it was asked."""

    def __init__(
        self, *, result: object = None, error: Exception | None = None
    ) -> None:
        self.requests: list[GitSourceSpec] = []
        self.headers: list[dict] = []
        self._result = result
        self._error = error

    def fetch(self, spec, *, headers=None):
        self.requests.append(spec)
        self.headers.append(dict(headers or {}))
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


CHECKOUT = SimpleNamespace(sha="a" * 40, root=Path("/tmp/x"), url="u", ref="main")


def _spec(url: str = "https://git.corp/r.git", ref: str = "main") -> GitSourceSpec:
    return GitSourceSpec(url=url, ref=ref)


def test_one_url_ref_pair_is_fetched_once_and_the_sha_is_recorded():
    git = FakeGitClient(result=CHECKOUT)
    session = SourceSession(sources={}, baselines={}, git=git)
    first = session.checkout(_spec(), headers={}, display="src", auth_name="ci")
    second = session.checkout(_spec(), headers={}, display="src", auth_name="ci")
    # Same checkout object back, one underlying fetch for the pair.
    assert first is second
    assert len(git.requests) == 1
    assert session.resolution_records() == (
        SourceResolution(
            name="src", ref="main", resolved_sha="a" * 40, auth="ci"
        ),
    )


def test_distinct_refs_or_urls_fetch_distinctly():
    git = FakeGitClient(result=CHECKOUT)
    session = SourceSession(sources={}, baselines={}, git=git)
    session.checkout(_spec(), headers={}, display="src", auth_name=None)
    session.checkout(_spec(ref="dev"), headers={}, display="src", auth_name=None)
    session.checkout(
        _spec(url="https://git.corp/other.git"), headers={}, display="src2", auth_name=None
    )
    assert len(git.requests) == 3


def test_a_fetch_failure_is_raised_and_caches_nothing():
    git = FakeGitClient(error=FetchFailedError("git fetch failed"))
    session = SourceSession(sources={}, baselines={}, git=git)
    try:
        session.checkout(_spec(), headers={}, display="src", auth_name=None)
        raise AssertionError("expected FetchFailedError")
    except FetchFailedError:
        pass
    # The failure is not cached: the next entry's attempt re-asks the client
    # (and, in production, may still fall back per keep_last).
    session_again = SourceSession(sources={}, baselines={}, git=git)
    assert session_again.resolution_records() == ()


def test_close_is_idempotent_and_deregisters(monkeypatch):
    removed: list[Path] = []
    monkeypatch.setattr(
        "agentclaw.community.core.bot_config_manifest.apply.source_session._rmtree",
        lambda root, **kw: removed.append(Path(root)),
    )
    git = FakeGitClient(result=CHECKOUT)
    session = SourceSession(sources={}, baselines={}, git=git)
    session.checkout(_spec(), headers={}, display="src", auth_name=None)
    session.close()
    session.close()
    assert removed == [Path("/tmp/x")]


def test_baseline_reads_the_map_not_a_repository():
    session = SourceSession(
        sources={}, baselines={"src": "b" * 40}, git=FakeGitClient()
    )
    assert session.baseline("src") == "b" * 40
    assert session.baseline("unknown") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/community/core/bot_config_manifest/apply/test_source_session.py -x -q`
Expected: FAIL — `ModuleNotFoundError: No module named ...source_session`

- [ ] **Step 3: Write minimal implementation**

新建 `<R>/apply/source_session.py`：

```python
"""One apply's named-source state (W7, #1475).

The four things a single apply needs and nothing more: the document's
``sources`` declarations, the strict-mode baselines read back from the last
apply's report, a checkout cache keyed on the substituted ``(url, ref)``, and
the `SourceResolution` records the report will carry. It hangs on
``ApplyContext`` beside ``budget`` — mutable by design inside a frozen
context, the precedent that ruling cites — because the fetcher is a DI
singleton (state there would leak across applies) and a re-resolution per
entry would break "the same {git, ref} is pulled once per apply".
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    SourceResolution,
)
from agentclaw.community.core.bot_config_manifest.fetch.git_source import (
    GitCheckout,
    GitSourceClient,
    GitSourceSpec,
)

#: The seam ``close()`` goes through, so its test can observe the removals
#: without creating trees a real apply would have made through the client.
_rmtree = shutil.rmtree


@dataclass
class SourceSession:
    """Per-apply: what ``from`` may name, what last time resolved, what this
    apply fetched. Created by the apply service at ``start_apply``/``dry_run``
    and closed in every terminal path — including launch failure."""

    #: The stored document's top-level ``sources`` map, frozen at apply start.
    sources: Mapping[str, Mapping[str, Any]]
    #: Named source → the SHA the last apply recorded (``ApplyReport.sources``
    #: read back). A source absent here has no strict opinion yet.
    baselines: Mapping[str, str]
    #: The git transport; injected so tests script it and production gets the
    #: subprocess client via the DI provider.
    git: GitSourceClient

    _checkouts: dict[tuple[str, str], GitCheckout] = field(default_factory=dict)
    _resolutions: list[SourceResolution] = field(default_factory=list)
    _recorded: set[str] = field(default_factory=set)

    def checkout(
        self,
        spec: GitSourceSpec,
        *,
        headers: Mapping[str, str],
        display: str,
        auth_name: Optional[str],
    ) -> GitCheckout:
        """The checkout for one ``(url, ref)`` — fetching only the first time.

        ``display`` is the report's name for the source: the declared ``from``
        name for a named source, the repository URL for an inline one — the
        same key the baselines are read back by, so strict mode and the
        report agree on identity.
        """
        key = (spec.url, spec.ref)
        checkout = self._checkouts.get(key)
        if checkout is None:
            checkout = self.git.fetch(spec, headers=dict(headers))
            self._checkouts[key] = checkout
        if display not in self._recorded:
            self._recorded.add(display)
            self._resolutions.append(
                SourceResolution(
                    name=display,
                    ref=spec.ref,
                    resolved_sha=checkout.sha,
                    auth=auth_name,
                )
            )
        return checkout

    def resolution_records(self) -> tuple[SourceResolution, ...]:
        """What the report's ``sources`` section will carry."""
        return tuple(self._resolutions)

    def baseline(self, display: str) -> Optional[str]:
        """The SHA the last apply resolved this source to, or ``None``."""
        return self.baselines.get(display)

    def close(self) -> None:
        """Remove every checkout's tree. Idempotent — every terminal path of
        an apply calls it, including the launch-failure one."""
        for checkout in self._checkouts.values():
            _rmtree(checkout.root, ignore_errors=True)
        self._checkouts.clear()


__all__ = ["SourceSession"]
```

测试文件顶部两个 `FetchFailedError` 导入有重复（git_source 与 guarded_fetcher 同名）——直接删掉 `from ...git_source import FetchFailedError` 那个（它不导出）：Step 2 之前把测试导入块改成：

```python
from agentclaw.community.core.bot_config_manifest.fetch.git_source import (
    GitSourceSpec,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchFailedError,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/community/core/bot_config_manifest/apply/test_source_session.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/bot_config_manifest/apply/source_session.py \
        tests/community/core/bot_config_manifest/apply/test_source_session.py
git commit -m "feat(w7): per-apply source session with once-per-ref checkout cache"
```

---

### Task 5: `ApplyContext.source_session` + `EntryFetcher.fetch_declared` / `file_bytes`

**Files:**
- Modify: `<R>/apply/context.py`、`<R>/apply/entry_fetch.py`
- Modify: `<T>/apply/_fakes.py`（`make_context` 加 `source_session` 参数）
- Test: `<T>/apply/test_entry_fetch.py`（追加）

- [ ] **Step 1: Write the failing tests**

先在 `<T>/apply/_fakes.py` 的 `make_context` 签名加 `source_session=None` 并传入 `ApplyContext(..., source_session=source_session)`（`ApplyContext` 字段见 Step 3）。

追加到 `<T>/apply/test_entry_fetch.py`：

```python
from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
    GitEntrySource,
)
from agentclaw.community.core.bot_config_manifest.apply.source_session import (
    SourceSession,
)
from agentclaw.community.core.bot_config_manifest.fetch.git_source import (
    GitSourceSpec,
    git_receipt_url,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchFailedError,
)
from types import SimpleNamespace

GIT_URL = "https://git.corp/repo.git"
_FAKE_SHA = "a" * 40


class _ScriptedGit:
    def __init__(self, *, sha: str = _FAKE_SHA, error: Exception | None = None):
        self.specs: list[GitSourceSpec] = []
        self.headers: list[dict] = []
        self._sha = sha
        self._error = error

    def fetch(self, spec, *, headers=None):
        self.specs.append(spec)
        self.headers.append(dict(headers or {}))
        if self._error:
            raise self._error
        return SimpleNamespace(
            root=None, sha=self._sha, url=spec.url, ref=spec.ref,
            members=(("100644", spec.subpath or "pkg/skill.md"),),
            files=lambda subpath=None: [("skill.md", b"file-bytes")],
            read_file=lambda: b"file-bytes",
        )


def _session(git, *, sources=None, baselines=None):
    return SourceSession(
        sources=sources or {}, baselines=baselines or {}, git=git
    )


def test_fetch_declared_serves_a_url_from_a_named_source(rig):
    content, fetcher, credentials, pipeline = rig
    fetcher.responses["https://content.example/named.bin"] = fetched_object(
        BODY, url="https://content.example/named.bin"
    )
    ctx = make_context(
        source_session=_session(_ScriptedGit(), sources={
            "cdn": {"url": "https://content.example/named.bin", "auth": None},
        })
    )
    result = pipeline.fetch_declared(
        ctx, entry={"from": "cdn"}, category="identity"
    )
    # The same URL road as 'source', with the source's own auth folded in.
    assert result.content == BODY
    assert fetcher.requests[0].url == "https://content.example/named.bin"


def test_fetch_declared_gives_the_git_road_a_checkout(rig):
    _, _, credentials, pipeline = rig
    git = _ScriptedGit()
    ctx = make_context(
        source_session=_session(git, sources={
            "app": {"git": GIT_URL, "ref": "main", "subpath": "pkg"},
        })
    )
    decl = pipeline.fetch_declared(
        ctx, entry={"from": "app"}, category="skills", entry_identity="s1"
    )
    assert isinstance(decl, GitEntrySource)
    assert decl.checkout.sha == _FAKE_SHA
    assert decl.files() == [("skill.md", b"file-bytes")]
    # No named credential on this source: none was asked of W3, and the
    # session recorded the resolution the report will carry.
    assert credentials.binding_calls == []
    assert ctx.source_session.resolution_records()[0].resolved_sha == _FAKE_SHA


def test_fetch_declared_refuses_a_from_that_names_nothing(rig):
    _, _, _, pipeline = rig
    ctx = make_context(source_session=_session(_ScriptedGit()))
    with pytest.raises(EntryFetchError, match="not declared"):
        pipeline.fetch_declared(ctx, entry={"from": "ghost"}, category="skills")


def test_fetch_declared_missing_session_is_loud(rig):
    _, _, _, pipeline = rig
    ctx = make_context()
    with pytest.raises(EntryFetchError, match="no source session"):
        pipeline.fetch_declared(ctx, entry={"from": "x"}, category="skills")


def test_strict_refuses_when_the_ref_moved(rig):
    _, _, _, pipeline = rig
    git = _ScriptedGit()
    ctx = make_context(source_session=_session(git, baselines={"app": "b" * 40}))
    with pytest.raises(EntryFetchError, match="moved"):
        pipeline.fetch_declared(
            ctx,
            entry={"source": {"git": GIT_URL, "ref": "main", "mode": "strict"}},
            category="skills",
        )


def test_non_strict_records_the_move_in_the_note(rig):
    _, _, _, pipeline = rig
    git = _ScriptedGit()
    ctx = make_context(source_session=_session(git, baselines={"inline-src": "b" * 40}))
    decl = pipeline.fetch_declared(
        ctx,
        entry={"source": {"git": GIT_URL, "ref": "main"}},
        category="skills",
    )
    assert isinstance(decl, GitEntrySource)
    assert decl.moved_note() and "b" * 40 in decl.moved_note()
    assert "a" * 40 in decl.moved_note()


def test_strict_on_the_first_apply_has_no_opinion(rig):
    _, _, _, pipeline = rig
    git = _ScriptedGit()
    ctx = make_context(source_session=_session(git))  # no baselines
    decl = pipeline.fetch_declared(
        ctx,
        entry={"source": {"git": GIT_URL, "ref": "main", "mode": "strict"}},
        category="skills",
    )
    assert isinstance(decl, GitEntrySource)
    assert decl.moved_note() is None


def test_digest_on_a_git_source_is_refused(rig):
    _, _, _, pipeline = rig
    ctx = make_context(source_session=_session(_ScriptedGit()))
    with pytest.raises(EntryFetchError, match="digest"):
        pipeline.fetch_declared(
            ctx,
            entry={"source": {"git": GIT_URL, "ref": "main"},
                   "digest": "sha256:" + "0" * 64},
            category="skills",
        )


def test_git_keep_last_falls_back_to_the_baseline_receipt(rig):
    content, _, credentials, pipeline = rig
    old_sha = "b" * 40
    baseline_url = git_receipt_url(GIT_URL, old_sha, "pkg")
    content.store(
        fetched_object(b"stored-tree-zip", url=baseline_url,
                       content_type="application/zip"),
        scope=None, source_url=baseline_url,
    )
    git = _ScriptedGit(error=FetchFailedError("git fetch failed"))
    ctx = make_context(source_session=_session(
        git,
        sources={"app": {"git": GIT_URL, "ref": "main", "subpath": "pkg"}},
        baselines={"app": old_sha},
    ))
    result = pipeline.fetch_declared(
        ctx,
        entry={"from": "app", "on_fetch_failure": "keep_last"},
        category="skills",
        entry_identity="s1",
    )
    assert result.from_store is True
    assert result.content == b"stored-tree-zip"
    assert result.content_type == "application/zip"
    assert result.fallback_reason and "keep_last" in result.fallback_reason


def test_git_credentials_reach_the_transport_as_headers(rig):
    _, fetcher, credentials, pipeline = rig
    git = _ScriptedGit()
    ctx = make_context(source_session=_session(git, sources={
        "app": {"git": GIT_URL, "ref": "main", "auth": "ci-token"},
    }))
    pipeline.fetch_declared(ctx, entry={"from": "app"}, category="skills")
    assert credentials.binding_calls == ["ci-token"]
    assert git.headers == [{"X-Custom-Auth": "payload-of-ci-token"}]


def test_file_bytes_files_canonical_entry_bytes_with_the_store(rig):
    content, _, _, pipeline = rig
    ctx = make_context(apply_id="apply-1")
    digest = pipeline.file_bytes(
        ctx, content=b"canonical-zip",
        source_url=git_receipt_url(GIT_URL, _FAKE_SHA, "pkg"),
        category="skills", entry_identity="s1",
        content_type="application/zip",
    )
    assert digest == "sha256:" + hashlib.sha256(b"canonical-zip").hexdigest()
    call = content.store_calls[-1]
    assert call["source_url"] == f"git+{GIT_URL}@{_FAKE_SHA}:pkg"
    assert call["apply_id"] == "apply-1"
    assert call["entry_identity"] == "s1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/community/core/bot_config_manifest/apply/test_entry_fetch.py -q`
Expected: FAIL — `ImportError` on `GitEntrySource` 或 `TypeError: make_context() got an unexpected keyword 'source_session'`

- [ ] **Step 3: Write minimal implementation**

**`context.py`** —— imports 加：

```python
from agentclaw.community.core.bot_config_manifest.apply.source_session import (
    SourceSession,
)
```

dataclass 末尾（`budget` 字段后）加：

```python
    #: One apply's named-source state (W7): the document's ``sources``, the
    #: strict-mode baselines read back from the last apply's report, and the
    #: git checkout cache. Mutable by design inside the frozen context — the
    #: same ruling as ``budget``, and for the same reason: the alternative is
    #: state on a DI-singleton fetcher, which would leak across applies.
    #: ``None`` for callers that run no ``from``/git pipeline (tests,
    #: hand-driven use); ``fetch_declared`` refuses such entries loudly.
    source_session: Optional[SourceSession] = None
```

**`entry_fetch.py`** —— 模块 docstring 之后追加导入：`hashlib`、`from datetime import datetime, timezone`（如尚未有）、`Mapping`、以及：

```python
from agentclaw.community.core.bot_config_manifest.apply.source_session import (
    SourceSession,
)
from agentclaw.community.core.bot_config_manifest.fetch.git_source import (
    GitCheckout,
    GitSourceSpec,
    git_receipt_url,
)
```

`FetchedEntry` 加一个尾字段（默认值保证既有调用不破）：

```python
    #: The URL the bytes came by, when the caller needs it for shape
    #: inference (the skills materialiser's archive-kind detection). ``None``
    #: on the roads that never knew one.
    source_url: Optional[str] = None
```

同时把 `fetch()` 里三处 `FetchedEntry(...)` 构造各补 `source_url=target`。

模块级（`FetchedEntry` 之后）加：

```python
@dataclass(frozen=True)
class GitEntrySource:
    """A fresh git checkout for one entry to consume — files, not bytes.

    The URL road hands back bytes because there was exactly one blob on the
    wire; the git road hands back a proven tree and lets the materialiser
    read it, because what "the entry's bytes" are (a file? a package? a
    canonical zip?) is a *category* question the fetch layer must not answer.
    """

    checkout: GitCheckout
    source_url: str
    subpath: Optional[str]
    moved_from: Optional[str]

    def files(self) -> list[tuple[str, bytes]]:
        try:
            return self.checkout.files(self.subpath)
        except FetchRefusedError as exc:
            raise EntryFetchError(str(exc)) from exc

    def read_file(self) -> bytes:
        try:
            return self.checkout.read_file(self.subpath)
        except FetchRefusedError as exc:
            raise EntryFetchError(str(exc)) from exc

    def receipt_url(self) -> str:
        """The W11 identity for this entry's git-sourced bytes."""
        return git_receipt_url(self.source_url, self.checkout.sha, self.subpath)

    def moved_note(self) -> Optional[str]:
        """The non-strict road's report line about a moved ref."""
        if self.moved_from is None:
            return None
        return (
            f"ref moved: the last apply recorded {self.moved_from}, "
            f"this one resolved {self.checkout.sha}"
        )
```

`EntryFetcher` 类内（`fetch` 方法之后、`_fetch` 之前）加两个方法：

```python
    def fetch_declared(
        self,
        ctx: "ApplyContext",
        *,
        entry: "Mapping[str, Any]",
        category: str,
        entry_identity: Optional[str] = None,
    ) -> "FetchedEntry | GitEntrySource":
        """Resolve one entry's declared source — inline, or by ``from`` name —
        and acquire it. Raises :class:`EntryFetchError`.

        The URL roads (inline string ``source``, or a ``from`` source that
        declares ``url``) delegate to :meth:`fetch` unchanged, fold in the
        *source's* ``auth``, and inherit its pinned/keep_last policy. The git
        road resolves the ref once per ``(url, ref)`` per apply through the
        context's source session, enforces ``mode`` against the last apply's
        resolved SHA, and hands back a :class:`GitEntrySource` — the tree is
        the entry's to interpret, and ``keep_last`` falls back to the
        baseline-SHA receipt with the same keep_last-only ruling as wire
        failures.
        """
        expired = ctx.budget.expired() if ctx.budget is not None else None
        if expired is not None:
            raise EntryFetchError(expired)

        session = ctx.source_session
        if session is None:
            raise EntryFetchError(
                "this apply carries no source session: a 'from' or git "
                "source needs one (the apply service builds it per apply)"
            )

        inline = entry.get("source")
        keep_last = entry.get("on_fetch_failure", "keep_last") == "keep_last"
        name: Optional[str] = None
        decl: Optional["Mapping[str, Any]"] = None

        if isinstance(entry.get("from"), str):
            name = entry["from"]
            decl = session.sources.get(name)
            if decl is None:
                raise EntryFetchError(
                    f"'from' names source {name!r}, which is not declared "
                    "under 'sources'"
                )
        elif isinstance(inline, str):
            return self.fetch(
                ctx,
                source_url=inline,
                digest=entry.get("digest"),
                auth=entry.get("auth"),
                category=category,
                keep_last=keep_last,
                entry_identity=entry_identity,
            )
        elif isinstance(inline, Mapping):
            decl = inline
        else:
            raise EntryFetchError(
                "an entry must name one of 'from', 'source' or 'content'"
            )
        assert decl is not None

        if "git" not in decl:
            # A named or inline URL source: the same road, with the source's
            # own auth — the declaration, not the entry, carries it (W7).
            return self.fetch(
                ctx,
                source_url=decl["url"],
                digest=entry.get("digest"),
                auth=decl.get("auth"),
                category=category,
                keep_last=keep_last,
                entry_identity=entry_identity,
            )

        if entry.get("digest") is not None:
            # v1 narrowing, documented: a pin against git-sourced bytes has no
            # stable meaning across the fresh-tree/canonical-zip roads. A
            # SHA-pinned ref is the pin this source speaks.
            raise EntryFetchError(
                "digest pinning is not supported on a git source in v1 — "
                "pin by writing the commit SHA as the source's ref"
            )

        spec = GitSourceSpec(
            url=_substitute(ctx, decl["git"]),
            ref=decl.get("ref") or "HEAD",
            subpath=decl.get("subpath"),
            mode=decl.get("mode") or "non_strict",
        )
        display = name if name is not None else spec.url
        auth = decl.get("auth")

        try:
            headers: dict[str, str] = {}
            if auth:
                binding = self._credentials.binding(name=auth)
                binding.reauthorize(httpx.URL(spec.url))
                headers = dict(binding.headers_for(httpx.URL(spec.url)))
            checkout = session.checkout(
                spec, headers=headers, display=display, auth_name=auth
            )
        except CredentialError as exc:
            raise EntryFetchError(str(exc)) from exc
        except PrefixAuthorizationError as exc:
            raise EntryFetchError(str(exc)) from exc
        except FetchRefusedError as exc:
            raise EntryFetchError(str(exc)) from exc
        except FetchFailedError as exc:
            fallback = self._git_keep_last(
                ctx, session=session, spec=spec, display=display,
                keep_last=keep_last, wire_error=exc,
            )
            if fallback is not None:
                return fallback
            raise EntryFetchError(str(exc)) from exc

        baseline = session.baseline(display)
        if (
            spec.mode == "strict"
            and baseline is not None
            and baseline != checkout.sha
        ):
            raise EntryFetchError(
                f"strict source {display!r} moved: the last apply recorded "
                f"{baseline}, this one resolved {checkout.sha} — the entry "
                "is refused and the bot keeps running what it has"
            )
        moved = baseline if (baseline is not None and baseline != checkout.sha) else None
        return GitEntrySource(
            checkout=checkout,
            source_url=spec.url,
            subpath=spec.subpath,
            moved_from=moved,
        )

    def _git_keep_last(
        self,
        ctx: "ApplyContext",
        *,
        session: SourceSession,
        spec: GitSourceSpec,
        display: str,
        keep_last: bool,
        wire_error: FetchFailedError,
    ) -> "Optional[FetchedEntry]":
        """`keep_last` for the git road: the receipt of the *last-resolved*
        SHA, when there was one. A first-time source has no baseline — and
        therefore no stored copy entitled to answer for it."""
        if not keep_last:
            return None
        baseline = session.baseline(display)
        if baseline is None:
            return None
        target = git_receipt_url(spec.url, baseline, spec.subpath)
        try:
            receipt = self._content.latest_receipt(
                scope_of(ctx), source_url=target
            )
            if receipt is None:
                return None
            return FetchedEntry(
                content=self._content.read(receipt.digest),
                digest=receipt.digest,
                from_store=True,
                content_type=receipt.content_type,
                source_url=target,
                fallback_reason=(
                    "delivered from the platform's stored copy (keep_last): "
                    f"the git fetch failed — {wire_error}"
                ),
            )
        except (ContentStoreError, ContentStoreFault) as exc:
            raise EntryFetchError(str(exc)) from exc

    def file_bytes(
        self,
        ctx: "ApplyContext",
        *,
        content: bytes,
        source_url: str,
        category: str,
        entry_identity: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> str:
        """File entry-level bytes the wire never fetched — the git road's
        canonical form (a package's canonical zip, a single file's bytes)
        — so audit and ``keep_last`` read the same store everyone else does.

        Returns the content digest. Raises :class:`EntryFetchError` on a
        store fault; the charge against the apply budget keeps the ledger
        honest about what the entry cost, disk-read or not.
        """
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        obj = FetchedObject(
            bytes=content, sha256=digest, url=source_url,
            content_type=content_type,
            fetched_at=datetime.now(timezone.utc), size_bytes=len(content),
        )
        try:
            self._content.store(
                obj, scope=scope_of(ctx), source_url=source_url,
                modifier=ctx.actor_id, apply_id=ctx.apply_id,
                category=category, entry_identity=entry_identity,
            )
        except (ContentStoreError, ContentStoreFault) as exc:
            raise EntryFetchError(
                "the bytes could not be filed with the platform's store: "
                f"{exc}"
            ) from exc
        if ctx.budget is not None:
            ctx.budget.charge(len(content))
        return digest
```

`__all__` 加 `"GitEntrySource"`。`httpx` 与 `Mapping`/`Any` 导入若无则补（文件已有 `Optional`、`dataclass`；`FetchRequest/FetchedObject` 已导入——去掉 `FetchedObject` 若原先未导入则保留）。

测试 `test_fetch_declared_gives_the_git_road_a_checkout` 里 `_ScriptedGit` fake 的 members 字段形如 `(("100644", path), ...)`，与真 `GitCheckout` 一致（Task 5 测试里 `decl.files()` 走 fake 的 `files` 闭包，不依赖 members 结构）。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/community/core/bot_config_manifest/apply/test_entry_fetch.py -q`
Expected: PASS（原有用例 + 新增全绿——原用例除非 `make_context` 签名回归，不应有任何改动）

再跑全套兜底：
Run: `python -m pytest tests/community/core/bot_config_manifest -q`
Expected: PASS（单例字段默认 None，无行为面变化）

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/bot_config_manifest/apply/context.py \
        src/agentclaw/community/core/bot_config_manifest/apply/entry_fetch.py \
        tests/community/core/bot_config_manifest/apply/_fakes.py \
        tests/community/core/bot_config_manifest/apply/test_entry_fetch.py
git commit -m "feat(w7): declared-source resolution and the git entry road"
```

---

### Task 6: identity materialiser 的 git 路

**Files:**
- Modify: `<R>/apply/materialisers/identity.py`
- Test: `<T>/apply/test_identity_materialiser.py`（追加；沿用该文件现有的 materialiser 构造 helper——构造参数 `IdentityMaterialiser(identity_service, fetcher)`，fakes 来自 `_fakes.py`）

- [ ] **Step 1: Write the failing tests**

追加到 `<T>/apply/test_identity_materialiser.py`（文件内已有的导入与 ctx 构造 helper 直接复用；若文件用 `make_context`，须带 `source_session`）：

```python
class _StaticGit:
    """A git client that serves one checkout with stable bytes."""

    def __init__(self, sha: str = "a" * 40):
        self.sha = sha
        self.specs: list = []

    def fetch(self, spec, *, headers=None):
        self.specs.append(spec)
        return SimpleNamespace(
            root=None, sha=self.sha, url=spec.url, ref=spec.ref,
            members=(("100644", spec.subpath or "files/id_rsa.pub"),),
            files=lambda subpath=None: [],
            read_file=lambda: b"ssh-ed25519 AAAA...",
        )


def _git_ctx(git=None, *, sources=None, baselines=None):
    session = SourceSession(
        sources=sources or {}, baselines=baselines or {}, git=git or _StaticGit()
    )
    return make_context(source_session=session)


IDENTITY_GIT_SOURCE = {"git": "https://git.corp/id.git", "ref": "main",
                       "subpath": "files/id_rsa.pub", "auth": None}
```

用例（沿用该文件现有的 service/intent 断言风格——`FakeIdentityService.writes` 计数、`resolve` 失败面）：

```python
async def test_an_identity_entry_can_read_one_file_from_a_git_source(<现有 rig>):
    # resolve succeeds and the intent carries the file's bytes
    ...


async def test_a_git_identity_without_subpath_is_a_resolve_failure(<现有 rig>):
    # entry fails with 'subpath' in the reason, no intent, no write
    ...


async def test_a_moved_ref_on_the_git_road_lands_in_the_note(<现有 rig>):
    # baselines carry an old SHA -> intent.note mentions both SHAs
    ...


async def test_the_git_road_files_its_bytes_with_the_store(<现有 rig>):
    # FakeManifestContent.store_calls[-1]['source_url'] == 'git+…@<sha>:files/id_rsa.pub'
    ...
```

`<现有 rig>` 处写该文件实际在用的 fixture/构造方式——机制上等价于：

```python
identity = FakeIdentityService()
content = FakeManifestContent()
fetcher = EntryFetcher(FakeGuardedFetcher(), content, FakeCredentials())
materialiser = IdentityMaterialiser(identity, fetcher)
ctx = _git_ctx()
result = await materialiser.resolve(
    ctx, [{"type": "ssh_public_key", "from": "id"}]
)
```

即每个用例照此展开为完整测试函数（resolve 断 intent / failures；write 断 `identity.writes`），不使用省略号。`mode: strict` 的失败路径已在 Task 5 覆盖，此处不重复。

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/community/core/bot_config_manifest/apply/test_identity_materialiser.py -q`
Expected: 新用例 FAIL——现 materialiser 只认字符串 `source`，git 条目全部 resolve 失败

- [ ] **Step 3: Write minimal implementation**

`<R>/apply/materialisers/identity.py` 的 resolve 循环中，把「inline `content` 块之后、仅接受字符串 `source`」的那段替换为（导入区补 `GitEntrySource`）：

```python
            if "from" not in entry and "source" not in entry:
                failures.append(
                    ResolveFailure(
                        file_type,
                        "an identity entry must declare 'source', 'from' "
                        "or 'content'",
                    )
                )
                continue

            try:
                # The fetch is blocking network/disk I/O ... (keep the
                # existing to_thread ruling verbatim)
                decl = await asyncio.to_thread(
                    self._fetcher.fetch_declared,
                    ctx,
                    entry,
                    category=FetchCategory.IDENTITY.value,
                    entry_identity=file_type,
                )
            except EntryFetchError as exc:
                failures.append(ResolveFailure(file_type, exc.reason))
                continue

            if isinstance(decl, GitEntrySource):
                if decl.subpath is None:
                    # Category knowledge stays here: identity reads exactly
                    # one file, and the source's subpath is where it is named.
                    failures.append(
                        ResolveFailure(
                            file_type,
                            "an identity entry from a git source must set "
                            "the source's 'subpath' to a single file",
                        )
                    )
                    continue

                def _read_and_file() -> bytes:
                    body = decl.read_file()
                    self._fetcher.file_bytes(
                        ctx,
                        content=body,
                        source_url=decl.receipt_url(),
                        category=FetchCategory.IDENTITY.value,
                        entry_identity=file_type,
                    )
                    return body

                try:
                    body = await asyncio.to_thread(_read_and_file)
                except EntryFetchError as exc:
                    failures.append(ResolveFailure(file_type, exc.reason))
                    continue
                intents.append(Intent(file_type, <现有 utf-8 解码逻辑>(body), note=decl.moved_note()))
                continue

            # ── the URL road, exactly as today ──
            fetched = decl
            <现有的 decode → Intent 块保持不变，fallback_reason 通道照旧>
```

`<现有 utf-8 解码逻辑>(body)` 处把现有 decode/`ResolveFailure("the fetched identity source is not UTF-8 text", ...)` 逻辑直接内联成对 `body` 的同款代码块（与 `fetched.content.decode` 共用同一份实现——抽一个模块内 `def _as_utf8(text_bytes, file_type, failures)` helper 并在两条路共用，避免复制）。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/community/core/bot_config_manifest/apply/test_identity_materialiser.py -q`
Expected: PASS（原有 + 新增）

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/bot_config_manifest/apply/materialisers/identity.py \
        tests/community/core/bot_config_manifest/apply/test_identity_materialiser.py
git commit -m "feat(w7): identity entries can read one file from a git source"
```

---

### Task 7: skills materialiser 的 git 路

**Files:**
- Modify: `<R>/apply/materialisers/skills.py`
- Test: `<T>/apply/test_skills_materialiser.py`（追加；该文件现有构造：`SkillsMaterialiser(upload, activation, capability_reader, validator, fetcher)`，`real_validator()` / `FakeSkillUploadService` 等 helper 已在文件内）

- [ ] **Step 1: Write the failing tests**

追加（`_StaticGit` 同 Task 6——测试文件各自持有一份，不跨测试文件 import）：

```python
SKILL_GIT_SOURCE = {"git": "https://git.corp/skills.git", "ref": "main",
                     "subpath": "pkg", "auth": None}


async def test_a_skills_entry_from_a_git_tree_builds_a_package(<现有构造>):
    # ctx = make_context(source_session=SourceSession(
    #     sources={"src": SKILL_GIT_SOURCE}, baselines={}, git=_StaticGit()))
    # entry = {"name": "demo", "from": "src"}  # fake checkout serves a tree
    #   with pkg/SKILL.md naming the skill "demo" — reuse this file's
    #   existing directory-validator fixtures to build a valid one.
    # assert resolve ok, one intent, upload got a canonical zip。
    ...


async def test_the_git_tree_road_files_the_canonical_zip_with_the_store(<现有构造>):
    # content.store_calls[-1]['source_url'].startswith('git+https://git.corp/skills.git@')
    #   and its content_type == 'application/zip'
    ...


async def test_a_moved_ref_note_survives_into_the_package(<现有构造>):
    # baselines={'src': 'b'*40}; materialiser's Intent(note) carries both SHAs
    ...


async def test_git_keep_last_serves_the_stored_zip_through_the_zip_road(<现有构造>):
    # git fetch fails; receipt at git+…@<baseline>:pkg holds a canonical zip
    #   the validator accepts (the zip fixture the file already builds).
    # fetch_declared answers FetchedEntry(from_store=True,
    #   content_type='application/zip') — 已在 Task 5 单测覆盖，这里断
    #   materialiser 把它走成成功 intent 而非 resolve 失败。
    ...
```

同 Task 6：`...` 处展开为完整测试函数，用该文件实际已有 zip/directory fixture 组装一个合法 SKILL.md 树；`_StaticGit.files` 返回 `[("SKILL.md", b"..."), ...]`。

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/community/core/bot_config_manifest/apply/test_skills_materialiser.py -q`
Expected: 新用例 FAIL

- [ ] **Step 3: Write minimal implementation**

`<R>/apply/materialisers/skills.py`——导入 `GitEntrySource`、`FetchedEntry`。resolve 循环里把 `source_url = entry.get("source")` 的字符串检查块替换为：

```python
            if "from" not in entry and not isinstance(
                entry.get("source"), (str, dict)
            ):
                failures.append(
                    ResolveFailure(name, "a skills entry must declare 'source' or 'from'")
                )
                continue

            try:
                decl = await asyncio.to_thread(
                    self._fetcher.fetch_declared,
                    ctx,
                    entry,
                    category=_FETCH_CATEGORY,
                    entry_identity=name,
                )
            except EntryFetchError as exc:
                failures.append(ResolveFailure(name, exc.reason))
                continue
```

紧随其后（原 `_build_package` 调用处）改为：

```python
            try:
                if isinstance(decl, GitEntrySource):
                    package = await asyncio.to_thread(
                        self._git_package, ctx, decl, name
                    )
                else:
                    package = await asyncio.to_thread(
                        self._build_package,
                        entry=entry,
                        fetched=decl,
                        source_url=decl.source_url
                        or (entry["source"] if isinstance(entry.get("source"), str) else ""),
                    )
            except _PackageRefusal as exc:
                failures.append(ResolveFailure(name, str(exc)))
                continue
```

私有方法（`_build_package` 之后）：

```python
    def _git_package(
        self, ctx: "ApplyContext", decl: GitEntrySource, name: str
    ) -> _SkillPackage:
        """A git checkout's tree → a validated package, plus its W11 receipt.

        The canonical zip the validator returns is what this entry delivers,
        so it is also what the platform stores: the receipt a later keep_last
        falls back to must be the deliverable bytes, not a re-derivation.
        """
        try:
            files = decl.files()
        except EntryFetchError as exc:
            raise _PackageRefusal(str(exc)) from exc
        validated = self._validate(self._validator.validate_directory, files)
        try:
            self._fetcher.file_bytes(
                ctx,
                content=validated.canonical_zip,
                source_url=decl.receipt_url(),
                category=_FETCH_CATEGORY,
                entry_identity=name,
                content_type="application/zip",
            )
        except EntryFetchError as exc:
            raise _PackageRefusal(str(exc)) from exc
        return _SkillPackage(
            validated.name,
            validated.canonical_zip,
            from_store=False,
            note=decl.moved_note(),
        )
```

（keep_last 回落（`FetchedEntry` + `content_type='application/zip'`）自动落进既有 `_build_package` 的 zip-无-subpath 路：`_archive_kind` 依 content type 判 zip，entry 的 `subpath` 为 None（git 条目的 subpath 在源上，不在条目上），`kind=="zip" and not subpath` 直接 `validate_zip`。）

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/community/core/bot_config_manifest/apply/test_skills_materialiser.py -q` \
      && `python -m pytest tests/community/core/bot_config_manifest/apply/test_identity_materialiser.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/bot_config_manifest/apply/materialisers/skills.py \
        tests/community/core/bot_config_manifest/apply/test_skills_materialiser.py
git commit -m "feat(w7): skills entries can build a package from a git tree"
```

---

### Task 8: 报告与服务接线——session 桥、基线、DI、dry_run

**Files:**
- Modify: `<R>/apply/orchestrator.py`、`<R>/services/config_manifest_apply_service.py`、`di/modules/manifest_fetch_module.py`
- Test: `<T>/apply/test_apply_service_lifecycle.py`（fixture + 新用例）；报告 sources 断言加在 `<T>/apply/test_orchestrator_stays_generic.py` 或 `test_apply_engine.py`（看哪个文件已构造 orchestrator——若构造繁琐则全部放 lifecycle 端到端）

- [ ] **Step 1: Write the failing tests**

`<T>/apply/test_apply_service_lifecycle.py`——`world` fixture 的 service 构造加：

```python
        git_client_provider=lambda: _fakes.FakeGitClient(),
```

并在文件（或 _fakes）加：

```python
class FakeGitClient:
    """The git transport for suites whose documents fetch nothing — every
    call is a wiring bug, so it counts and refuses."""

    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, spec, *, headers=None):
        self.calls += 1
        raise AssertionError(
            "this suite's document declares no git sources; "
            f"git fetch({spec.url!r}) must not run"
        )
```

新端到端用例（沿用该文件 `world` / `_start` / poll helper 的既有风格）：

```python
def test_a_git_sourced_identity_entry_ends_in_the_report_with_its_sha(world):
    # 1) 写入一份带 sources 声明的 manifest（该文件的 _DOCUMENT 之外建
    #    一个 scope 内新 bot，或参数化现有 helper）：
    #      sources: {id: {git: file://<bare>, ref: main,
    #                      subpath: files/id_rsa.pub, auth: null}}
    #      identity: [{type: ssh_public_key, from: id}]
    # 2) FakeGitClient 需换成真实 SubprocessGitClient(allowed_schemes={'file'})
    #    + on-disk bare repo（Task 3 的 _serve_repo 同款夹具）——即 world
    #    fixture 参数化 git_client_provider。
    # 3) start → poll → get_apply：
    #    断 report payload['sources'] == [{name:'id', ref:'main',
    #      resolved_sha: <sha>, auth: None}]
    #    断 identity 类目成功，bot 文件写入。
    ...


def test_the_second_apply_reads_the_strict_baseline_from_the_last_report(world):
    # 第一次 apply（non-strict）后移动 ref/重写 manifest 的 ref 到新 SHA +
    # mode: strict；第二次 apply 该条目 FAILED，reason 提到两个 SHA；
    # 且 ApplyReport.sources 记录新 SHA —— 基线与报告同源。
    ...
```

展开为完整测试函数时：`_serve_repo` 夹具从 `fetch/test_git_source.py` 提到 `apply/_fakes.py`（或复制；不跨测试目录 import）。

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/community/core/bot_config_manifest/apply/test_apply_service_lifecycle.py -q`
Expected: FAIL——`BotConfigManifestApplyService.__init__() missing 'git_client_provider'` 与新用例

- [ ] **Step 3: Write minimal implementation**

**`orchestrator.py`**——`ApplyReport(...)` 构造（`apply` 方法末尾）加实参：

```python
            sources=(
                tuple(ctx.source_session.resolution_records())
                if ctx.source_session is not None
                else ()
            ),
```

**`config_manifest_apply_service.py`**——

1. 导入：`SourceSession`、`GitSourceClient`（TYPE_CHECKING 下即可）。
2. `__init__` 参数表尾加 `git_client_provider: Callable[[], GitSourceClient]`，与其它 provider 同样注释风格：
   ```python
           # W7's git transport, held the same lazy way: the source session
           # wants a fresh client reference per apply and the DI singleton
           # provider stays out of the import graph this file owns.
           self._git_client_provider = git_client_provider
   ```
3. `start_apply` 的 `try:` 块里，`parsed = ...` 之后、`apply_id = ...` 之前加：
   ```python
           session = SourceSession(
               sources=parsed.get("sources") or {},
               baselines=self._last_resolutions(
                   entity_id=entity_id, bot_id=bot_id
               ),
               git=self._git_client_provider(),
           )
   ```
   `session: Optional[SourceSession] = None` 提到 `try:` 之前；`except BaseException` 块末尾（`raise` 前）加 `if session is not None: session.close()`；`ctx = self._context(...)` 补 `source_session=session,`。
4. `_context` 签名加 `source_session: Optional[SourceSession] = None`，透传给 `ApplyContext(..., source_session=source_session)`。
5. `_run` 的最内层 `finally`（释放锁的那个之前）加：
   ```python
               if ctx.source_session is not None:
                   ctx.source_session.close()
   ```
6. `dry_run`——构造 `ctx` 处补：
   ```python
           source_session=SourceSession(
               sources=parsed.get("sources") or {},
               baselines=self._last_resolutions(
                   entity_id=entity_id, bot_id=bot_id
               ),
               git=self._git_client_provider(),
           ),
   ```
   并把 return 包进 try/finally 关闭 session。
7. 私有方法（`last_apply` 之后）：
   ```python
       def _last_resolutions(
           self, *, entity_id: str, bot_id: str
       ) -> dict[str, str]:
           """Each source's SHA as the LAST apply recorded it (W7 strict).

           The previous apply's report is where "what did we resolve" already
           lives (``ApplyReport.sources``), so strict mode reads it back
           rather than keeping a second table the two could drift apart on.
           A report with no resolutions — or no report — yields no opinions.
           """
           last = self.last_apply(entity_id=entity_id, bot_id=bot_id)
           if last is None:
               return {}
           return {
               source.name: source.resolved_sha
               for source in last.sources
               if source.resolved_sha is not None
           }
   ```

**`manifest_fetch_module.py`**——导入 `GitSourceClient, SubprocessGitClient`，providers 区加：

```python
    @singleton
    @provider
    def manifest_git_source_client(self) -> GitSourceClient:
        """W7's git transport: the CLI subprocess client.

        Like ``GuardedFetcher``, everything about it except construction is
        the shipped default — https-only scheme, hermetic env, header-only
        credential injection — and not configurable from here.
        """
        return SubprocessGitClient()

    @singleton
    @provider
    @inject
    def manifest_git_client_factory(
        self, injector: Injector
    ) -> Callable[[], GitSourceClient]:
        """The lazy lookup the apply service builds each apply's source
        session through — a fresh map and fresh checkouts every apply."""
        return lambda: injector.get(GitSourceClient)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/community/core/bot_config_manifest -q`
Expected: PASS

Run（DI/模块面）: `python -m pytest tests/community -q -k "module or injector" | tail -3`
Expected: 无新增失败；若 DI 绑定测试需要 `git_client_factory`，按其缺省错误提示补钉。

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/bot_config_manifest/apply/orchestrator.py \
        src/agentclaw/community/core/bot_config_manifest/services/config_manifest_apply_service.py \
        src/agentclaw/community/di/modules/manifest_fetch_module.py \
        tests/community/core/bot_config_manifest/apply/test_apply_service_lifecycle.py \
        tests/community/core/bot_config_manifest/apply/_fakes.py
git commit -m "feat(w7): thread the source session through apply, with strict baselines"
```

---

### Task 9: 收尾——全套回归、架构守卫、coverage gate、文档

**Files:**
- Test: 全套；`src/backend/docs/bot-config-manifest/work-items.zh-CN.md` 与 `work-items.md` 的 W7 节加完成标记

- [ ] **Step 1: 全量回归（含架构守卫）**

Run: `python -m pytest tests/community/core/bot_config_manifest -q`
Expected: PASS

Run: `python -m pytest tests/community/architecture -q`
Expected: PASS——若新文件触发既有守卫（模块分层/命名规则），按守卫错误提示调整放置而非放宽守卫（记忆：worktree 残留 `__pycache__` 会误报无关模块——先 `find src -name __pycache__ -type d -exec rm -rf {} +` 再跑）。

Run: `python -m pytest tests/community -q -x --timeout=600 2>/dev/null || python -m pytest tests/community -q -x`
Expected: PASS（W7 改动全部向后兼容；单例默认 None 是它的安全面）

- [ ] **Step 2: changed-line coverage gate（推送前唯一可信复现）**

Run: 仓库根的 `ci_test.sh`（按 `verify-coverage-gate-before-push` 备忘的既有口径）
Expected: changed-line coverage ≥ 80%。缺口则补测试而非调门槛。

- [ ] **Step 3: work-items 文档完成标记**

在 `src/backend/docs/bot-config-manifest/work-items.zh-CN.md` 的 `#### W7` 节首（Owner 行后）加（英文版 `work-items.md` 对齐同款式）：

```markdown
> **✅ 本项已完成（2026-09-02）。**命名源与 git 源已交付：subprocess git CLI
> 浅层单 ref fetch（W2 同门拒绝语义与限额、包含性检查在 W11 之前）、
> `from`/`sources` 解析进 `EntryFetcher.fetch_declared`、per-apply
> `SourceSession`（一次 `{git, ref}` 拉取复用）、`SourceResolution` 进
> apply 报告、strict 基线读上次 apply 报告。v1 收窄：git 源不支持
> `digest`（以 SHA 形式的 ref 钉住）；W6 资源目录条目对 git 源的消费随
> W6 分支合入。
```

（实际完成日期以收尾日为准。）

- [ ] **Step 4: Commit**

```bash
git add src/backend/docs/bot-config-manifest/work-items.zh-CN.md \
        src/backend/docs/bot-config-manifest/work-items.md
git commit -m "docs(backend): mark W7 delivered in the work items"
```

- [ ] **Step 5: 交付确认**

按 `pr-title-desc-conventions` 备忘起 PR：`feat(backend): deliver manifest named and git sources (W7)`，Problem/Solution/Validation 三段；目标分支 `dev`，推送前确认 rebase 于最新 `origin/dev`（`rebase-not-merge-dev-conflicts` 备忘）。

---

## Self-Review 记录

- **Spec coverage**：W7 验收标准逐条 → ①`sources`/`from`/互斥/未引用警告（W1 已交付，apply 侧缺名拒绝 Task 5）②auth 在源上（Task 5 fetch_declared 读 decl.auth）③atomic 解析、单次拉取复用（Task 4 cache）④ref→SHA 记入报告（Task 4+8）⑤浅层单 ref（Task 2）⑥只读、无 hook/filter（Task 2 docstring + env 隔离）⑦包含性检查在 W11 之前（Task 2 `_enumerate` + Task 3 readers）⑧解开后限额（Task 3）⑨移动 tag 收敛（Task 3 测试）⑩git 目录条目免 unpack（schema 已定，W6 消费；Task 7 单测钉住 zip 路不受影响）⑪mode 字段（schema 已定 + Task 5 strict 执行）——**唯一刻意的 v1 收窄**：git 源 + `digest` 拒绝（Task 5，带文档理由）。清理临时 checkout 由 `GitCheckout` 失败路径 + `SourceSession.close()` 双保险。
- **Placeholder scan**：Task 6/7/8 中标注「展开为完整测试函数」的四处是仅有的弹性位——机制、构造方式、断言目标都已写死，展开时不得增删断言口径。
- **Type consistency**：`GitSourceSpec(url, ref, subpath, mode, auth)` / `SubprocessGitClient.fetch(spec, *, headers)` / `SourceSession.checkout(spec, *, headers, display, auth_name)` / `fetch_declared(ctx, *, entry, category, entry_identity)` / `file_bytes(ctx, *, content, source_url, category, entry_identity, content_type)` 各任务间一致；`git_receipt_url(url, sha, subpath)` 单一定义点。

---

## 双 agent 协作约定(B 会话于 2026-09-02 16:45 追加;单干时本节作废)

两个会共享同一 worktree 与分支,唯一同步介质 = 本文件 + git 历史。任务分工:

| 任务 | 归属 |
| --- | --- |
| Task 1(已完成)、Task 2、Task 3、Task 5、Task 8、Task 9 | **A**(本计划作者,W7 主会话) |
| Task 4、以及 Task 6/7(Task 5 合入后认领) | **B**(协作会话) |

规则(与 superpowers 协作契约一致,浓缩为四条):

1. **claim 先行**:认领写在任务标题下的引用块里(格式见 Task 4);开工前先 `git log --oneline -8` 查任务是否已被提交。
2. **按文件 add**:只 `git add` 自己所有权表里的文件,**绝不 `git add -A` / `git add .` / `git stash`**——`git status` 里出现的对方 WIP 是只读信息。
3. **提交 message 带任务号**,沿用本计划各 Task Step 5 的 message。
4. **同文件冲突兜底**:本文件是唯一双写点,且约定 append-only;对方 claim 的任务 30 分钟无提交且会话确认死亡,方可在本文件留 takeover 记录后接管。

