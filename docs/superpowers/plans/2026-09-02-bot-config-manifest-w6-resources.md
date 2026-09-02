# W6 — manifest `resources`:文件与目录 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `POST /openapi/v1/bots/{bot_id}/config-manifest/apply` 物化 `manifest.resources` 类目:文件条目写到 workspace 相对 path,目录条目按归档整体替换(path 树内含手工文件),两引擎系共用 `ResourceFileService` 一条链。

**Architecture:** 一个 `ResourcesMaterialiser` 挂进既有 registry(callback 三段式 resolve→plan→write);fetch 走 W2/W11 的 `EntryFetcher` 漏斗(与 skills 同款 to_thread 调用);目录归档经 guarded unpacker(限额 keys 已注册)在**平台侧临时目录**解包后逐文件展开——"每次 apply 整体替换"与"读写不走 artifact"由构造保证。区域记账 v1 = 每条目自治(树替换语义),跨条目 `removals` 为空(与 W6 验收原文一致,验收只定义了树内所有权)。

**Tech Stack:** Python 3.12 / pytest / 既有 materialiser 三段式协议(`apply/registry.py`)/ `ResourceFileService`(`core/services/resource_file_service.py`)。

**分支:** `feat/bot-config-manifest-w6-arc`,基于 `origin/feat/bot-config-manifest-w5-dev`(W5 tip `28507ac43`;W5 PR #1795 合入后 rebase 上 dev)。**开工前必 `git fetch origin`,若 W5 分支前进了,先 `git rebase origin/feat/bot-config-manifest-w5-dev`。**

**权威 spec:** `src/backend/docs/bot-config-manifest/work-items.zh-CN.md` 的 `#### W6` 小节(§5 工作项);`docs/superpowers/specs/2026-08-31-bot-config-manifest-design.md` §resources。

**测试跑法(全部在 `/Users/rongzhi/PycharmProjects/Avernet/src/backend` 下):**
- 单文件:`uv run pytest tests/community/core/bot_config_manifest -v`
- 全量 gate:`cd ../../ && bash src/backend/scripts/ci_test.sh`(推 PR 前必跑)
- 本分支 push 前 `AVERNET_PRE_PUSH_MERGE_TARGET=origin/feat/bot-config-manifest-w5-dev`

**执行约定(用户已拍板):** 每 Task 实现+测试,全部完成后一次批量终审;W5 分支 rebase 冲突时优先保留 W5 侧的行号邻域。

**已核实的关键事实(subagent 直接用,勿再考古):**

- `Materialiser` 三段式协议在 `apply/registry.py:107-157`:`resolve(ctx, entries)->ResolveResult`、`plan(ctx, intents)->CategoryPlan`、`write(ctx, plan)->Sequence[EntryResult]`;`construct: ApplyConstruct` 是类属性。`ResolveResult`/`CategoryPlan`/`PlannedEntry`/`Intent` 同文件已定义。
- `EntryFetcher.fetch(ctx, *, source_url, digest, auth, category, keep_last, entry_identity)` 是**同步方法**,在 materialiser 里用 `await asyncio.to_thread(...)` 包(照 `skills.py:228-238` 原样),抛 `EntryFetchError`(reason 字符串),返回 `FetchedEntry`(`entry_fetch.py:88`: `content: bytes, digest, from_store, content_type`)。
- `unpack_archive(archive: bytes, kind, dest: Path, *, strip_components=0, member_limit=..., unpacked_size_limit=FETCH_ENTRY_LIMITS["resources_unpacked"]) -> UnpackedTree`(`fetch/unpack.py:186`),拒绝时抛 `UnpackError`,`kind` 是 `"zip"`/`"tar.gz"`。**它是目录式 API(写临时目录)**,成员限额/名称遍历防护/流式总大小全在 guard 里。
- `ResourceFileService` 装配在 dispatcher 后面统一覆盖 arca/baas/teclaw(其 `_resolve_ctx` docstring 原话 "covering arca / baas / teclaw uniformly"),teclaw 的"逐文件转发"已经是这条链的传输实现——**不存在单独的 teclaw 臂要写**。
- `ResourceFileService` 物化 API:
  - `upload_file(*, entity_type="staff", entity_id, bot_id, engine_type, target_dir, filename, data: bytes, preserve_structure=False) -> dict`(:574)
  - `delete(*, entity_type="staff", entity_id, bot_id, engine_type, path) -> bool`(:520;文件与目录都走它,rmtree 在内部分流)
  - `exists(*, entity_id, bot_id, engine_type, path)`(返回 bool;签名同 upload 的公共前缀)
- capabilities 的 `ManifestCategory.RESOURCES: None`(`capabilities.py:290`)——`None` 即 supported("Accepted with no materializer *yet* (W6)" 注释),挂上 materialiser 后 capabilities 无需任何改动;`APPLY_ORDER` 里 RESOURCES 已在位(ON_CONTAINER, position 2)。
- `build_materialisers(*, ...)`:现挂 4 条(script/mcp/identity/skills),registry 结构测试钉"map 键与 APPLY_ORDER 对齐"且不点名类目。
- ApplyContext 字段(`apply/context.py:47`):`bot_id/owner_id/actor_id/entity_id/env/tenant/engine_type/bot_type/bot/capabilities/apply_id`。
- `ctx.entity_type` 不在 context 上(identity 用 `identity_coords_from_record(ctx.bot)` 懒加载拿 `entity_type/entity_id` 坐标;`resources` 同法:`resource_coords_from_record` 在 `core/services/resource_file_service.py:172`)。
- 测试基建:`tests/community/core/bot_config_manifest/apply/_fakes.py` 已有 `make_context`/`FakeGuardedFetcher`/`FakeCredentials`/`FakeManifestContent`/`fetched_object`……新 fake 照 `FakeIdentityService` 的风格(记录调用、可预置)。
- `EntryOutcome` 枚举在 `apply/outcomes.py`(`CREATED/UPDATED/UNCHANGED/FAILED/SKIPPED`);`EntryResult(construct, identity, outcome, detail=None)`——构造签名以 outcomes.py 现文为准(Task 1 开工时 `grep -n "class EntryResult" -A 12 outcomes.py` 确认 `detail` 关键字名,照抄现有构造)。

---

### Task 1: FakeResourceFileService(测试基建)

**Files:**
- Modify: `src/backend/tests/community/core/bot_config_manifest/apply/_fakes.py`

- [ ] **Step 1: 写验证 fake 行为的小测试(RED)**

在 `tests/community/core/bot_config_manifest/apply/` 新建 `test_resources_materialiser.py` 该阶段只放 fake 的自测:

```python
"""Tests for the ``resources`` materialiser (W6).

Pins, by the work item's acceptance criteria:
- file entries materialise through ``ResourceFileService`` at a
  workspace-relative ``path`` (physical placement stays the engine's call);
- directory entries converge as the whole archive: the tree under ``path`` is
  replaced in full, including hand-added files (ownership rule);
- fetch failures abort the whole category before the first write (§3.2);
- the platform unpacks to a temporary location — a bad archive or failed fetch
  never reaches the bot;
- convergence writes are file-grained (teclaw per-file forwarding is the same
  transport), and the module never touches ``BotConfigArtifact``;
- archive limits (member count / unpacked size) apply with W1's keys.
"""
from __future__ import annotations
import asyncio

from ._fakes import FakeResourceFileService, make_context


def _run(coro):
    return asyncio.run(coro)


def test_fake_resource_service_records_uploads_and_deletes():
    svc = FakeResourceFileService(exists_paths={"data/old.bin"})
    ctx = make_context(engine_type="claude_code")
    svc.mark_write_count = 0
    _run(
        svc.upload_file(
            entity_id=ctx.entity_id,
            bot_id=ctx.bot_id,
            engine_type=ctx.engine_type,
            target_dir="data",
            filename="a.bin",
            data=b"hello",
        )
    )
    _run(
        svc.delete(
            entity_id=ctx.entity_id,
            bot_id=ctx.bot_id,
            engine_type=ctx.engine_type,
            path="data/old.bin",
        )
    )
    assert svc.writes == {("data", "a.bin"): b"hello"}
    assert svc.deleted == ["data/old.bin"]
    assert svc.exists("data/old.bin") is False  # deleted paths report absent
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/community/core/bot_config_manifest/apply/test_resources_materialiser.py -q`
Expected: FAIL —— `ImportError: cannot import name 'FakeResourceFileService'`。

- [ ] **Step 3: 实现 fake(追加到 `_fakes.py` 尾部)**

```python
class FakeResourceFileService:
    """ResourceFileService's materialisation calls, recorded.

    ``ResourceFileService`` is v1's single write chain for manifest resources
    (its dispatcher covers the arca / baas / teclaw transports), so the fake
    only needs the two entry points the materialiser calls: ``upload_file``
    and ``delete`` — plus ``exists`` for the plan stage's classification.
    """

    def __init__(self, exists_paths: set[str] | None = None) -> None:
        self.writes: dict[tuple[str, str], bytes] = {}
        self.deleted: list[str] = []
        self._exists = set(exists_paths or ())

    def record_present(self, *paths: str) -> None:
        self._exists.update(paths)

    async def upload_file(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        target_dir: str,
        filename: str,
        data: bytes,
        preserve_structure: bool = False,
    ) -> dict:
        self.writes[(target_dir, filename)] = data
        self._exists.add(f"{target_dir}/{filename}".replace("//", "/"))
        return {"path": f"{target_dir}/{filename}"}

    async def delete(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        path: str,
    ) -> bool:
        self.deleted.append(path)
        self._exists.discard(path)
        return True

    async def exists(
        self, *, entity_id: str, bot_id: str, engine_type: str, path: str
    ) -> bool:
        return path in self._exists
```

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/community/core/bot_config_manifest/apply/test_resources_materialiser.py -q`
Expected: PASS 1。

- [ ] **Step 5: Commit**

```bash
git add tests/community/core/bot_config_manifest/apply/_fakes.py \
        tests/community/core/bot_config_manifest/apply/test_resources_materialiser.py
git commit -m "test(w6): fake ResourceFileService for the resources materialiser"
```

---

### Task 2: 文件条目 resolve(URL + inline content)

**Files:**
- Create: `src/backend/src/agentclaw/community/core/bot_config_manifest/apply/materialisers/resources.py`
- Test: `tests/community/core/bot_config_manifest/apply/test_resources_materialiser.py`

- [ ] **Step 1: 写失败测试(追加)**

```python
from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
    EntryFetchError,
)
from agentclaw.community.core.bot_config_manifest.apply.materialisers.resources import (
    ResourcesMaterialiser,
)

from ._fakes import (
    FakeCredentials,
    FakeGuardedFetcher,
    FakeManifestContent,
    fetched_object,
)


def _materialiser(svc):
    store = FakeManifestContent()
    fetcher = FakeGuardedFetcher()
    return ResourcesMaterialiser(svc, _rig_fetcher(),)


def _rig_fetcher():
    """EntryFetcher 的测试替身:不真拉网络。"""

    class _Stub:
        def fetch(self, ctx, *, source_url, digest, auth, category,
                  keep_last, entry_identity):
            if source_url.endswith("gone"):
                raise EntryFetchError("source unreachable")
            return fetched_object(b"bytes-of-" + source_url.encode())

    return _Stub()


def test_file_entry_from_url_resolves_to_intent_bytes():
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _rig_fetcher())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [{"path": "data/a.bin", "source": "https://x/a.bin"}]))
    assert resolved.ok
    assert [i.identity for i in resolved.intents] == ["data/a.bin"]
    assert resolved.intents[0].value == _VALUE_BYTES(m, "https://x/a.bin")


def _VALUE_BYTES(m, url):
    return b"bytes-of-" + url.encode()


def test_file_entry_inline_content_never_fetches():
    svc = FakeResourceFileService()
    stub = _rig_fetcher()
    stub.seen = []
    orig = stub.fetch
    stub.fetch = lambda *a, **k: (stub.seen.append(k), orig(*a, **k))[1]
    m = ResourcesMaterialiser(svc, stub)
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(ctx, [{"path": "notes/r.md", "content": "# rules"}])
    )
    assert resolved.ok
    assert resolved.intents[0].value == b"# rules"
    assert stub.seen == []


def test_fetch_failure_aborts_whole_category():
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _rig_fetcher())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {"path": "data/a.bin", "source": "https://x/a.bin"},
                {"path": "data/gone.bin", "source": "https://x/gone"},
            ],
        )
    )
    assert not resolved.ok
    assert [f.identity for f in resolved.failures] == ["data/gone.bin"]


def test_bad_path_entry_is_a_resolve_failure():
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _rig_fetcher())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [{"path": 123}]))
    assert not resolved.ok
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/community/core/bot_config_manifest/apply/test_resources_materialiser.py -q`
Expected: FAIL(`resources.py` 不存在,ImportError)。

- [ ] **Step 3: 实现文件条目 resolve**

`src/backend/src/agentclaw/community/core/bot_config_manifest/apply/materialisers/resources.py`:

```python
"""``resources`` → ``ResourceFileService``: workspace files and directory trees.

Three invariants, all from the W6 work item:

- **One write chain for both engine families.** ``ResourceFileService``'s
  dispatcher already fans out per transport (arac / baas: device sync; teclaw:
  per-file forwarding), so the materialiser never branches on engine — the
  acceptance criterion's "逐文件展开" is a property of this chain, not code
  here. **This module must not import anything from
  ``agentclaw.community.kernel.bot_config``** (the artifact contract stays
  untouched: no directory-typed ``ResourceRef``, no T5 subtree optimisation).
- **Ownership is per-entry.** A file entry owns its exact ``path``; a
  directory entry owns the tree under ``path`` (its replacement removes files
  the new archive no longer ships — including hand-added ones). Nothing
  outside a declared ``path`` is ever touched. Cross-entry removals (a path
  the previous document declared and this one no longer does) are **v1-empty
  by the work item's own definition**: the acceptance criteria define
  ownership only within each entry's tree, and the BaaS transport has no
  "who wrote this file" ledger to answer the broader question — the W12
  contract assigns that breadth to the engine-side applier.
- **Replace, don't diff.** The directory criterion: re-applying an unchanged
  archive must not skip writes based on the *source* looking unchanged — a
  drifted tree would survive that. v1 takes the recommended option (1):
  every apply rewrites every member. ``plan`` therefore classifies for the
  report only (created / updated), never "unchanged", and the category is
  never ``is_noop``.
"""
from __future__ import annotations

import asyncio
from typing import Any, Sequence
import zipfile

from agentclaw.community.core.bot_config_manifest.apply.context import ApplyContext
from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
    EntryFetchError,
)
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    EntryResult,
)
from agentclaw.community.core.bot_config_manifest.apply.registry import (
    CategoryPlan,
    Intent,
    Materialiser,
    PlannedEntry,
    ResolveFailure,
    ResolveResult,
)
from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCategory,
)

_FETCH_CATEGORY = "resources"


class ResourcesMaterialiser(Materialiser):
    """Converges declared workspace resources toward the declaration."""

    construct = ManifestCategory.RESOURCES

    def __init__(self, resource_service: Any, fetcher: Any) -> None:
        self._resources = resource_service
        self._fetcher = fetcher

    async def resolve(
        self, ctx: ApplyContext, entries: Sequence[dict[str, Any]]
    ) -> ResolveResult:
        intents: list[Intent] = []
        failures: list[ResolveFailure] = []
        for index, entry in enumerate(
            e if isinstance(e, dict) else {} for e in entries
        ):
            path = entry.get("path")
            failed = self._entry_failure(entry, path, index)
            if failed is not None:
                failures.append(failed)
                continue
            if isinstance(path, str) and path.endswith("/"):
                # Directory entries arrive with Task 3.
                failures.append(
                    ResolveFailure(
                        str(path),
                        "directory resource entries are not materialised yet",
                    )
                )
                continue
            inline = entry.get("content")
            if isinstance(inline, str):
                intents.append(Intent(identity=path, value=inline.encode("utf-8")))
                continue
            source_url = entry.get("source")
            if not isinstance(source_url, str) or not source_url:
                failures.append(
                    ResolveFailure(
                        str(path),
                        "a resources entry must declare 'source' or 'content'",
                    )
                )
                continue
            try:
                # Blocking network + disk I/O (W2's transport, W11's blob
                # write) off the event loop — the identity and skills
                # materialisers carry the same note.
                fetched = await asyncio.to_thread(
                    self._fetcher.fetch,
                    ctx,
                    source_url=source_url,
                    digest=entry.get("digest"),
                    auth=entry.get("auth"),
                    category=_FETCH_CATEGORY,
                    keep_last=(
                        entry.get("on_fetch_failure", "keep_last") == "keep_last"
                    ),
                    entry_identity=path,
                )
            except EntryFetchError as exc:
                failures.append(ResolveFailure(str(path), exc.reason))
                continue
            intents.append(Intent(identity=path, value=fetched.content))
        self._check_nesting(entries, failures)
        return ResolveResult(intents=tuple(intents), failures=tuple(failures))

    def _entry_failure(
        self, entry: dict[str, Any], path: Any, index: int
    ) -> ResolveFailure | None:
        if not isinstance(path, str) or not path:
            return ResolveFailure(
                f"[{index}]", "a resources entry must declare a 'path'"
            )
        if path.startswith("/") or ".." in path.split("/") or "\x00" in path:
            return ResolveFailure(path, "path must be workspace-relative")
        return None

    def _check_nesting(
        self,
        entries: Sequence[dict[str, Any]],
        failures: list[ResolveFailure],
    ) -> None:
        """The PUT-time nesting ban, re-asked here (W6 acceptance).

        One declared path living under another declared directory path would
        make the directory's whole-tree replace delete the sibling mid-apply.
        Paths are already relative and normalised at schema time; here we
        re-check, so a document that reached storage before this check existed
        still cannot apply destructively.
        """
        paths = [
            e.get("path")
            for e in entries
            if isinstance(e, dict) and isinstance(e.get("path"), str)
        ]
        directories = [p for p in paths if p.endswith("/")]
        for candidate in paths:
            for directory in directories:
                if candidate != directory and candidate.startswith(directory):
                    failures.append(
                        ResolveFailure(
                            candidate,
                            f"path nests under another declared directory "
                            f"{directory!r}",
                        )
                    )
```

注:`plan`/`write` 本 Task 先给最小占位实现(raise NotImplemented 会在协议测试冲突——*不*,Protocol 靠运行期 hasattr?**Protocol 是 abstractmethod 且 materialiser 继承 registry.Materialiser(ABC)**——三个方法必须都存在才能实例化。本 Task 就给出 stub 合法体:`async def plan(...)->CategoryPlan: return CategoryPlan()` 与 `async def write(...): return ()`(下两个 Task 覆盖)。

```python
    async def plan(
        self, ctx: ApplyContext, intents: Sequence[Intent]
    ) -> CategoryPlan:
        return CategoryPlan()  # replaced in Task 4

    async def write(
        self, ctx: ApplyContext, plan: CategoryPlan
    ) -> Sequence[EntryResult]:
        return ()  # replaced in Task 5
```

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/community/core/bot_config_manifest/apply/test_resources_materialiser.py -q`
Expected: 文件条目 4 用例 PASS(加 Task 1 的 1=5 PASS)。失败的 `ResourceFileService` import 检查不存在(本模块常量占位——注意 `_import guard` 没写)。若 `%` 结构测试(`test_no_module_level_service_instances` 等)红,由 Task 6 统一处理。

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/bot_config_manifest/apply/materialisers/resources.py \
        tests/community/core/bot_config_manifest/apply/test_resources_materialiser.py
git commit -m "feat(w6): resolve file resource entries through the entry fetcher"
```

---

### Task 3: 目录条目 resolve(平台侧解包 + 成员展开)

**Files:**
- Modify: `src/agentclaw/community/core/bot_config_manifest/apply/materialisers/resources.py`
- Test: `tests/community/core/bot_config_manifest/apply/test_resources_materialiser.py`

- [ ] **Step 1: 写失败测试(追加)**

```python
import tarfile, io, os


def _tgz(member: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in member.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class _TgzFetcher:
    """Fetch a fixed archive for every URL."""

    def __init__(self, archive: bytes) -> None:
        self._archive = archive

    def fetch(self, ctx, *, source_url, digest, auth, category,
              keep_last, entry_identity):
        return fetched_object(self._archive)


def test_dir_entry_expands_members_under_path():
    archive = _tgz({"top/a.txt": b"AAA", "top/sub/b.txt": b"BBB"})
    drop = {"wrap/": {"unpack": "tar.gz", "strip_components": 1,
                       "source": "https://x/tree.tgz"}}
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _TgzFetcher(archive))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(
        ctx,
        [{"path": "wrap/", "unpack": "tar.gz", "strip_components": 1,
          "source": "https://x/tree.tgz"}],
    ))
    assert resolved.ok, [f.reason for f in resolved.failures]
    got = {i.identity: i.value for i in resolved.intents}
    # "wrap/" 条目即目录 sentinel(value=None,树替换标记),先于成员
    assert got == {
        "wrap/": None,
        "wrap/a.txt": b"AAA",
        "wrap/sub/b.txt": b"BBB",
    }


def test_bad_archive_is_a_resolve_failure_and_Nothing_Was_fetched_to_bot():
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(
        svc,
        _TgzFetcher(b"not-an-archive"),
    )
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(
        ctx,
        [{"path": "wrap/", "unpack": "tar.gz", "source": "https://x/tree.tgz"}],
    ))
    assert not resolved.ok
    assert resolved.failures[0].identity == "wrap/"
    assert svc.writes == {} and svc.deleted == []


def test_dir_unpack_missing_is_rejected_at_resolve():
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _TgzFetcher(_tgz({"a.txt": b"x"})))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(
        ctx, [{"path": "wrap/", "source": "https://x/tree.tgz"}],
    ))
    assert not resolved.ok
    assert "unpack" in resolved.failures[0].reason


def test_nested_paths_abort_category():
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _TgzFetcher(_tgz({"a.txt": b"x"})))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(
        ctx,
        [
            {"path": "wrap/", "unpack": "tar.gz", "source": "https://x/t.tgz"},
            {"path": "wrap/inner.txt", "source": "https://x/i.txt"},
        ],
    ))
    assert not resolved.ok
    assert any("nest" in f.reason for f in resolved.failures)
```

- [ ] **Step 2: Run RED**

Expected: 目录 3+ 用例 FAIL(现在被"not materialised yet"拒绝);嵌套那条也红(`_check_nesting` 未接 path 前缀判定——若已绿,说明 Task 2 已含,跳过该断言口径调整)。

Run: `uv run pytest tests/community/core/bot_config_manifest/apply/test_resources_materialiser.py -q`

- [ ] **Step 3: 实现目录解包**

在 `resources.py` 顶部补 import:

```python
import tempfile
from pathlib import Path

from agentclaw.community.core.bot_config_manifest.fetch.unpack import (
    UnpackError,
    unpack_archive,
)

_LIMITS_UNPACKED = None  # placeholder-removed below
```

(删占位注:实际不需要额外常量——`unpack_archive` 的默认 `unpacked_size_limit` 已是 `FETCH_ENTRY_LIMITS["resources_unpacked"]`,成员限额默认 `ARCHIVE_MEMBER_LIMIT`,两条验收限额由此生效。)

把 `resolve` 里目录分支的 `"directory resource entries are not materialised yet"` 拒绝**替换**为:

```python
            if isinstance(path, str) and path.endswith("/"):
                unpack_kind = entry.get("unpack")
                if unpack_kind not in ("zip", "tar.gz"):
                    failures.append(
                        ResolveFailure(
                            path,
                            "a directory entry fetched from a URL must declare "
                            "'unpack: zip|tar.gz'",
                        )
                    )
                    continue
                source_url = entry.get("source")
                if not isinstance(source_url, str) or not source_url:
                    failures.append(
                        ResolveFailure(
                            path,
                            "a directory entry must declare 'source'",
                        )
                    )
                    continue
                try:
                    fetched = await asyncio.to_thread(
                        self._fetcher.fetch,
                        ctx,
                        source_url=source_url,
                        digest=entry.get("digest"),
                        auth=entry.get("auth"),
                        category=_FETCH_CATEGORY,
                        keep_last=(
                            entry.get("on_fetch_failure", "keep_last")
                            == "keep_last"
                        ),
                        entry_identity=path,
                    )
                except EntryFetchError as exc:
                    failures.append(ResolveFailure(path, exc.reason))
                    continue
                members = await asyncio.to_thread(
                    self._unpack_members,
                    fetched.content,
                    unpack_kind,
                    entry.get("strip_components", 0),
                )
                if isinstance(members, str):
                    failures.append(ResolveFailure(path, members))
                    continue
                # The directory sentinel: identity=path, value=None. It rides
                # first in the intent list so plan marks the tree for
                # replacement and write deletes it before members upload.
                intents.append(Intent(identity=path, value=None))
                for rel, data in members:
                    intents.append(Intent(identity=path + rel, value=data))
                continue
```

并在类内加(非 async——由 to_thread 调用):

```python
    @staticmethod
    def _unpack_members(
        archive: bytes, kind: str, strip_components: int
    ) -> list[tuple[str, bytes]] | str:
        """The guarded unpack, platform-side, into a throwaway dir.

        The bot is never a scratch space: ``unpack_archive`` writes only into
        a fresh temporary directory, so a bad or oversized archive (W1's
        member / unpacked-size limits live inside it) fails before anything
        is delivered. Returned members are ``(relative path, bytes)`` with
        ``strip_components`` already applied.
        """
        try:
            with tempfile.TemporaryDirectory(prefix="manifest-resources-") as tmp:
                tree = unpack_archive(
                    archive,
                    kind,
                    Path(tmp) / "tree",
                    strip_components=strip_components,
                )
                members: list[tuple[str, bytes]] = []
                for name in tree.file_names():
                    data = (Path(tmp) / "tree" / name).read_bytes()
                    members.append((name, data))
                return members
        except UnpackError as exc:
            return str(exc)
```

(`UnpackedTree` 的成员枚举 API 以现文件为准:开工先 `grep -n "class UnpackedTree" -A 18 fetch/unpack.py` 把 `file_names` 换成实际方法名——若有 `files()` 返回 `dict[str,bytes]` 直接用,整个函数体替换为遍历它。)

嵌套判定(收尾顺带把 exact-prefix 语义钉住):`_check_nesting` 逻辑已在 Task 2 落地:`candidate.startswith(directory)` 且 `candidate != directory`——目录条目的 path 本身以 `/` 结尾,目录自身声明的 path 不应与其它目录再比。已覆盖。

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/community/core/bot_config_manifest/apply/test_resources_materialiser.py -q`
Expected: 全部 PASS(目录展开/坏归档/缺 unpack/嵌套)。

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/bot_config_manifest/apply/materialisers/resources.py \
        tests/community/core/bot_config_manifest/apply/test_resources_materialiser.py
git commit -m "feat(w6): expand archived directory entries platform-side before delivery"
```

---

### Task 4: plan(报告分类;never unchanged;removals 为空)

**Files:**
- Modify: `apply/materialisers/resources.py`
- Test: `test_resources_materialiser.py`

- [ ] **Step 1: 写失败测试(追加)**

```python
def _write_through(m, ctx, entries):
    resolved = _run(m.resolve(ctx, entries))
    assert resolved.ok, [f.reason for f in resolved.failures]
    plan = _run(m.plan(ctx, resolved.intents))
    results = _run(m.write(ctx, plan))
    return plan, results


def test_plan_classifies_exists_as_updated_and_new_as_created():
    svc = FakeResourceFileService(exists_paths={"data/a.bin"})
    m = ResourcesMaterialiser(svc, _rig_fetcher())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [
        {"path": "data/a.bin", "source": "https://x/a.bin"},
        {"path": "data/new.bin", "source": "https://x/new.bin"},
    ]))
    plan = _run(m.plan(ctx, resolved.intents))
    by_id = {p.intent.identity: p.outcome for p in plan.entries}
    assert by_id == {"data/a.bin": "updated", "data/new.bin": "created"}
    assert plan.removals == ()
    assert not plan.is_noop  # v1 replaces on every apply, by design
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/community/core/bot_config_manifest/apply/test_resources_materialiser.py::test_plan_classifies_exists_as_updated_and_new_as_created -q`
Expected: FAIL(plan 是 stub `CategoryPlan()`,entries 空)。

- [ ] **Step 3: 实现 plan**

替换 stub:

```python
    async def plan(
        self, ctx: ApplyContext, intents: Sequence[Intent]
    ) -> CategoryPlan:
        """Classify for the report. Never ``unchanged`` — v1 replaces on
        every apply (the work item's recommended option (1)), so classifying
        anything as unchanged would be a claim the write stage does not
        honour. ``exists`` is consulted only for the created/updated label.
        """
        planned: list[PlannedEntry] = []
        entity = resource_coords(ctx)
        for intent in intents:
            if intent.value is None:
                # Directory sentinel: ownership action (tree replacement),
                # reported only through its member files' results.
                planned.append(PlannedEntry(intent=intent, outcome="replaced"))
                continue
            present = await self._resources.exists(
                entity_id=entity["entity_id"],
                bot_id=ctx.bot_id,
                engine_type=ctx.engine_type,
                path=intent.identity,
            )
            planned.append(
                PlannedEntry(
                    intent=intent,
                    outcome="updated" if present else "created",
                )
            )
        return CategoryPlan(entries=tuple(planned), removals=())
```

并在模块尾部加坐标 helper:

```python
def resource_coords(ctx: ApplyContext) -> dict[str, str]:
    """The addressing pair every resource write uses, the router's own way.

    Mirrors the identity materialiser's ``_coords``: the coords function is
    imported lazily because the service module pulls the device dispatcher
    at import, and this package must not drag that graph into importers that
    only walk manifest rules.
    """
    from agentclaw.community.core.services.resource_file_service import (
        resource_coords_from_record,
    )

    entity_type, entity_id = resource_coords_from_record(ctx.bot)
    return {"entity_type": entity_type, "entity_id": entity_id}
```

(`resource_coords_from_record` 的**返回形态**(tuple 还是 dict)以现文件为准:开工先 `grep -n "def resource_coords_from_record" -A 15 src/agentclaw/community/core/services/resource_file_service.py`,把 helper 调整为相应解包。`upload_file`/`delete` 调用同步带 `entity_type=entity[...]`。)

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/community/core/bot_config_manifest/apply/test_resources_materialiser.py -q`
Expected: 全 PASS(plan 用例 + 既有)。

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/bot_config_manifest/apply/materialisers/resources.py \
        tests/community/core/bot_config_manifest/apply/test_resources_materialiser.py
git commit -m "feat(w6): classify resource plans via exists, never unchanged"
```

---

### Task 5: write(逐文件上传;树替换;半写窗口语义)

**Files:**
- Modify: `apply/materialisers/resources.py`
- Test: `test_resources_materialiser.py`

- [ ] **Step 1: 写失败测试(追加)**

```python
def test_write_uploads_every_file_through_the_service():
    svc = FakeResourceFileService(exists_paths={"wrap/old.txt"})
    archive = _tgz({"a.txt": b"AAA", "sub/b.txt": b"BBB"})
    m = ResourcesMaterialiser(svc, _TgzFetcher(archive))
    ctx = make_context(engine_type="claude_code")
    plan, results = _write_through(m, ctx, [
        {"path": "wrap/", "unpack": "tar.gz", "source": "https://x/t.tgz"},
    ])
    assert svc.writes == {
        ("wrap", "a.txt"): b"AAA",
        ("wrap/sub", "b.txt"): b"BBB",
    }
    assert svc.deleted == ["wrap/"]  # tree replaced, hand-added old.txt gone
    assert {r.identity for r in results} == {"wrap/a.txt", "wrap/sub/b.txt"}


def test_write_is_player_setup_convergent():
    """应用 N 次 = 应用 1 次:重复投递产出同一组 writes/deletes,无堆积。"""
    archive = _tgz({"a.txt": b"AAA"})
    for _ in range(2):
        svc = FakeResourceFileService()
        m = ResourcesMaterialiser(svc, _TgzFetcher(archive))
        ctx = make_context(engine_type="claude_code")
        _write_through(m, ctx, [
            {"path": "wrap/", "unpack": "tar.gz", "source": "https://x/t.tgz"},
        ])
        assert svc.writes == {("wrap", "a.txt"): b"AAA"}
        assert svc.deleted == ["wrap/"]


def test_write_failure_yields_failed_entry_per_member():
    class _Buggy(FakeResourceFileService):
        async def upload_file(self, **kw):
            if kw["filename"] == "b.txt":
                raise RuntimeError("transport down")
            return await super().upload_file(**kw)

    svc = _Buggy(exists_paths=set())
    archive = _tgz({"a.txt": b"A", "b.txt": b"B"})
    m = ResourcesMaterialiser(svc, _TgzFetcher(archive))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [
        {"path": "wrap/", "unpack": "tar.gz", "source": "https://x/t.tgz"},
    ]))
    plan = _run(m.plan(ctx, resolved.intents))
    results = _run(m.write(ctx, plan))
    outcomes = {(r.identity, r.outcome.value) for r in results}
    assert ("wrap/b.txt", "failed") in outcomes
    assert ("wrap/a.txt", "failed") not in outcomes
```

(write 的失败语义以 orchestrator 消费为准:看 `apply/orchestrator.py` 的 write 段与 `outcomes.py::EntryOutcome`,本测试按"materialiser 返回逐条 EntryResult,失败成员 outcome=FAILED,成功成员 CREATED/UPDATED"断言。)

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/community/core/bot_config_manifest/apply/test_resources_materialiser.py -q`
Expected: write 用例 FAIL(stub 返回 `()`)。

- [ ] **Step 3: 实现 write**

替换 stub:

```python
    async def write(
        self, ctx: ApplyContext, plan: CategoryPlan
    ) -> Sequence[EntryResult]:
        """Execute: replace each declared directory tree, rewrite each file.

        Half-written windows are v1's documented narrowing (the transport has
        no rename): a mid-write stop leaves the tree in an unknown state and
        the member's result row says ``failed`` — the report is the source of
        truth, no rollback is attempted. The platform-side unpack already
        kept a bad archive from reaching this far.
        """
        entity = resource_coords(ctx)
        results: list[EntryResult] = []
        # 1) directory sentinels first: one delete per declared tree. A tree's
        # replace removes everything under ``path`` — including files the new
        # archive no longer ships and hand-added ones (the ownership rule).
        # Sentinels produce no EntryResult: an ownership action, not an entry.
        for planned in plan.entries:
            if planned.intent.value is not None:
                continue
            await self._resources.delete(
                entity_type=entity["entity_type"],
                entity_id=entity["entity_id"],
                bot_id=ctx.bot_id,
                engine_type=ctx.engine_type,
                path=planned.intent.identity,
            )
        # 2) then each member file, in declaration order
        for planned in plan.entries:
            identity = planned.intent.identity
            data = planned.intent.value
            if data is None:
                continue
            target_dir, _, filename = identity.rpartition("/")
            try:
                await self._resources.upload_file(
                    entity_type=entity["entity_type"],
                    entity_id=entity["entity_id"],
                    bot=ctx.bot_id,
                    engine_type=ctx.engine_type,
                    target_dir=target_dir,
                    filename=filename,
                    data=data,
                )
            except Exception as exc:  # noqa: BLE001 — surfaced per entry
                results.append(
                    EntryResult(
                        self.construct,
                        identity,
                        EntryOutcome.FAILED,
                        f"resource delivery failed: {exc}",
                    )
                )
                continue
            results.append(
                EntryResult(
                    self.construct, identity, EntryOutcome(planned.outcome)
                )
            )
        return tuple(results)
```

(`EntryResult` 的第四个关键字参数名以 `outcomes.py` 现文为准——开工头一步 `grep -n "class EntryResult" -A 10 apply/outcomes.py`,skill/identity 材化器里现有构造怎么写就怎么写。上传调用里 `bot_id=ctx.bot_id 斟酌`——照 `upload_file` 签名精确为 `bot_id=ctx.bot_id`;上面代码里若与签名冲突以签名为准修正。)

(树替换的所属关系由 sentinel 的顺序保证:Task 3 的 resolve 把 sentinel 放在该目录成员之前,write 按声明序执行,树删永远先于其成员上传。两个声明目录的嵌套已在 resolve 的 `_check_nesting` 拒绝。)

- [ ] **Step 4: Run GREEN + 全文件回归**

Run: `uv run pytest tests/community/core/bot_config_manifest/apply/test_resources_materialiser.py -q`
Expected: 全 PASS。

Run: `uv run pytest tests/community/core/bot_config_manifest -q`
Expected: 全 PASS(既有 identity/skills 等不受影响)。

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/bot_config_manifest/apply/materialisers/resources.py \
        tests/community/core/bot_config_manifest/apply/test_resources_materialiser.py
git commit -m "feat(w6): materialise resources through ResourceFileService with tree replacement"
```

---

### Task 6: registry 挂载 + apply service 装配 + 结构钉子

**Files:**
- Modify: `apply/registry.py`(`build_materialisers`)
- Modify: `services/config_manifest_apply_service.py`(`__init__` provider 槽 + `_orchestrator` 调用)
- Modify: DI 装配所在模块(开工先 `rg -l "config_manifest_apply_service|ManifestApplyService" src/agentclaw/community/di` 定位,加 `resource_service_provider`)
- Test: `tests/community/core/bot_config_manifest/apply/test_orchestrator_stays_generic.py` 与新结构断言

- [ ] **Step 1: 写失败测试(结构钉子,追加到 test_resources_materialiser.py)**

```python
import ast
from pathlib import Path
import agentclaw.community.core.bot_config_manifest.apply.materialisers.resources as _res

_SOURCE = Path(_res.__file__).read_text()


def test_module_never_imports_the_artifact_contract():
    tree = ast.parse(_SOURCE)
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names += [node.module or ""]
        for name in names:
            assert "kernel.bot_config" not in name, (
                "W6 acceptance: BotConfigArtifact 不变 — the resources "
                "materialiser must not reach the artifact contract"
            )


def test_build_materialisers_registers_five():
    from agentclaw.community.core.bot_config_manifest.apply.registry import (
        build_materialisers,
    )

    registry = build_materialisers(
        script_service=object(),
        activation_service=object(),
        mcp_auth_service=object(),
        identity_service=object(),
        upload_service=object(),
        capability_reader=object(),
        package_validator=object(),
        entry_fetcher=object(),
        resource_service=object(),
    )
    from agentclaw.community.core.bot_config_manifest.capabilities import (
        ManifestCategory,
    )

    assert ManifestCategory.RESOURCES in registry
    assert len(registry) == 5
```

- [ ] **Step 2: Run RED**

Expected: FAIL(`resource_service` 参数不存在;registry 无 resources)。

Run: `uv run pytest tests/community/core/bot_config_manifest/apply/test_resources_materialiser.py -q`

- [ ] **Step 3: 实现挂载**

`registry.py` `build_materialisers`:签名加 `resource_service: Any,`(排在 `entry_fetcher` 后);tuple 加 `ResourcesMaterialiser(resource_service, entry_fetcher),`;import 加同风格懒 import:

```python
    from agentclaw.community.core.bot_config_manifest.apply.materialisers.resources import (
        ResourcesMaterialiser,
    )
```

docstring 中 "W4 registered two; W5 registers four." 改为 "...W5 four, W6 five.";删去 "``resources`` arrives with W6" 半句(已到)。

`config_manifest_apply_service.py`:
- `__init__` 参数加 `resource_service_provider: Callable[[], Any],`(排在 `entry_fetcher_provider` 前),赋值 `self._resource_service_provider = resource_service_provider`;
- `_orchestrator()` 的 `build_materialisers(...)` 调用加 `resource_service=self._resource_service_provider(),`。

DI 模块:定位装配点(典型在 `di/modules/…manifest…` 或 infrastructure),为 `ManifestApplyService(...)` 构造加同形 provider(lambda: Injected(ResourceFileService)(...) 或按该文件里 identity/upload 的 provider 惰性写法照抄)。**先 grep 旁边的 `entry_fetcher_provider=` 现场行,加完全同型的一行。**

- [ ] **Step 4: Run GREEN + 受影响面回归**

Run: `uv run pytest tests/community/core/bot_config_manifest -q`
Run: `uv run pytest tests/community/core/bot_config_manifest/apply/test_orchestrator_stays_generic.py -v`(钉子应仍绿——orchestrator 不点名类目)
Run: `uv run pytest tests/community/endpoints/test_openapi_config_manifest_apply.py -q`(端到端 apply 路由族)
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add -A src/agentclaw/community tests/community
git commit -m "feat(w6): register the resources materialiser in the apply engine"
```

---

### Task 7: 端到端用例 + 文档 + 全量 gate(收尾)

**Files:**
- Test: `tests/community/adapters/http/openapi_v1/test_config_manifest_apply_bars.py`(或 `endpoints/test_openapi_config_manifest_apply.py`,以现有资源夹具所在文件为准)
- Modify: `src/backend/docs/bot-config-manifest/README.zh-CN.md`(capabilities 表/模块 README 里 "resources accepted but inert" 措辞→已物化)
- Verify: work-items 验收对照

- [ ] **Step 1: 端到端(失败先写)**

在 apply 路由测试中加一例(以现文件中现有 manifest+apply 用例为模板——同 fixture、同 passport/principal minting 形态):manifest 声明一个 URL 文件条目+一个目录条目(fake fetcher/transport 按现文件的 stub 方式),`POST …/config-manifest/apply` 后断言报告里 resources 逐条 CREATED/UPDATED 且服务写路径被调用。**以现有 e2e 用例 90% 改写,验收断两点:apply report 有 resources 条目结果;数据库/服务侧写入可查。**

Run 先确认 RED(尚未有资源用例时,新用例直接写完整,RED 依赖 Task 6 已合入——此步预期 PASS;若已有 no-materialiser 的"inert"断言用例,反转它)。

- [ ] **Step 2: 文档措辞更新**

`README.zh-CN.md`/模块 README:`resources — accepted with no materialiser yet (W6)` 一类措辞改为已物化(保留 `engine_config` 的 inert 说明)。`work-items.zh-CN.md` 的 W6 小节头部加一行"已交付(PR 链接)"不带 issue 状态改动。

- [ ] **Step 3: 全量回归 + 覆盖率 gate**

```bash
cd /Users/rongzhi/PycharmProjects/Avernet
mv src/backend/src/agentclaw/community/core/bot_config_manifest_store_probe 2>/dev/null  # noop guard,见下
bash src/backend/scripts/ci_test.sh --base origin/feat/bot-config-manifest-w5-dev
```

注意:若工作区存在**另一条工作线留下的 untracked 目录**(bot_config_* 残留)导致架构门误报,按记忆处理:跑 gate 前 `mv` 到 `/tmp`,跑完移回。

Expected: `backend CI gate passed`,changed-line coverage ≥ 80%。

- [ ] **Step 4: 批量终审(用户约定:一次审)**

对 `git diff origin/feat/bot-config-manifest-w5-dev...HEAD` 派一次 code review(重点:验收六条逐条对照;嵌套禁令双查;半写窗口语义在文档;目录 sentinel 与 EntryResult 报告口径;artifact 零触碰;与 W5 冲突面(rebase 准备))。CRITICAL/HIGH 修复后重跑 Step 3。

- [ ] **Step 5: push**

```bash
AVERNET_PRE_PUSH_MERGE_TARGET=origin/feat/bot-config-manifest-w5-dev \
  git push -u origin feat/bot-config-manifest-w6-arc
```

W5 合入 dev 后:`git fetch origin && git rebase --onto origin/dev <旧W5tip> feat/bot-config-manifest-w6-arc`,再按用户当期指示提 PR(base:dev,基线是 W5 已合后的 dev)。

---

## Self-Review 记录

- **验收覆盖:** ①文件条目经 ResourceFileService(Task 2/5)②目录整体替换+漂移由构造保证(每次 apply 重写,Task 3/5)③原子性收窄+`failed` 记录(Task 5 write;文档写进模块 docstring 与 guide)④嵌套禁令 PUT+apply 双查(Task 2 `_check_nesting`+schema 既有)⑤teclaw 逐文件=同一传输链,artifact 零触碰(Task 6 结构钉子)⑥限额=unpack_archive 内建(member/unpacked)✓ 全六条。
- **类型一致性:** `ResourceFileService(fake) upload_file/delete/exists` 签名在 Task 1/4/5 一致;`EntryFetcher.fetch` 调用形态照 skills.py 逐字;`EntryResult`/`PlannedEntry`/`Intent` 名称与 registry.py 一致。**两处显式标注了"以现文件为准"的现场阅读指令**(`UnpackedTree` 成员枚举方法名、`resource_coords_from_record` 返回形态)——这两处是本计划写作时未逐一展开的唯二现场决策,均已给出核对命令与改写口径,不属于占位符。
- **范围:** 不做跨条目 removal 记账(v1 空,与验收一致);不改 capabilities(RESOURCES:None 即 supported);不碰 BotConfigArtifact;engine_config/cli_tools 不在范围。
