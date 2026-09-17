# Identity 组协作者可达（owner_id 参数 + 鉴权迁移）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 openapi_v1 identity 组三个操作从 `OWNER_SCOPED` 迁到协作者可达：发布可选 `owner_id` query 参数，GET 两个操作 `Check(MEMBER)`，PUT `Check(ADMIN, EDIT_LOCK)`；新旧两个地址一起迁移，手工 spec 同步。

**Architecture:** 完全复用 channels / config-manifest 组已走通的"addressed bot"模式——handler 声明 `user_id: UserIdDep` + `owner_id: OwnerIdDep`（`OwnerIdDep` 是 gate 和 handler 声明的**同一个** FastAPI 依赖，per-request 缓存保证 check 与 action 指向同一 bot）；鉴权表行翻成 `Check`；admission 模式翻成 `GRANT_CHECKED_ADDRESSED_BOT`；mount 从 own-bot 组挪到 addressed 组；路由器层零 service 改动（`entity_id = owner_id` 本来就是参数）。退役地址（`/openapi/v1/bots/identity/{bot_id}`…）由 relocate 注册同一个 handler 函数，它们的表行必须**显式**翻成同样的 `Check`（`_rule_for` 不会跨路径解析 `INHERITED`；先例：表尾 approvls/engine 旧行就是显式 `Check`）。

**Tech Stack:** Python 3.12 / FastAPI / pytest；spec 为 `src/gateway/configs/schemas/bots.openapi.json`（手工增量编辑，禁止全量 regen —— round-trip 不稳定，会导致 3MB 全文件 diff）。

---

## 背景（给零上下文执行者）

- 现状：`GET /openapi/v1/bots/{bot_id}/identity` 及同组 GET 单文件 / PUT，handler 拿 `owner_id: UserIdDep`（owner ≡ 调用者本人），表行 `OWNER_SCOPED`。协作者命名 owner 维度不存在，查别人的 bot 直接 404。
- 决策（用户已拍板）：三个操作一起迁；GET MEMBER，PUT **ADMIN + EDIT_LOCK**（改 RULES/SOUL 人格文件，对齐 config-manifest/channels 写口径）。
- 迁移的坐标词汇：`OwnerIdDep` / `AddressedBotGrantDep` / `resolve_owner_id` 定义在 `engine_runtime/params.py:86-155`；gate 在 `bot_access.py`；行表在 `authorization.py`；模式表在 `admission.py`。
- **退役地址口径**：`deprecated/_relocate.py` 不复制 route-level `dependencies`（只有 mount 依赖随组挂载），`authorization.py` 的 `_rule_for` 按路径查行、不跨路径解析 `INHERITED`。所以旧行必须显式写 `Check`。表尾已有先例（`authorization.py:637` 附近 approvals/engine 旧行显式 `Check(MEMBER)`）。
- **退役地址的 owner_id 参数会自动出现**：relocate 注册的是 replacement 的同一 handler 函数，签名里 `OwnerIdDep` 发布出的 `owner_id` query 会在新旧地址同时存在。这是接受的行为（与 sessions 等组 stage 参数双地址一致；不引入"drop 一个 Depends 型参数"的新机制——现有 `without_parameter` 只支持有 usable default 的参数，OwnerIdDep 没有）。
- spec 尾部锚点已核验：六个 identity 操作（3 新 + 3 旧）的 parameters 数组最后一段文本完全一致，以 `"\ntitle": "User Id"…\n          }\n        ]` 结尾；PUT 两个操作的 responses 有 `409/422` 无 `423`；channels GET 已有可整体复制的 `owner_id` 参数条目，channels POST 已有可复制的 `423` 响应条目。

---

### Task 1: bars 锁定测试（RED）

**Files:**
- Create: `src/backend/tests/community/adapters/http/openapi_v1/identity/test_identity_bars.py`

- [ ] **Step 1: 写失败的断言测试**

照 channels 组 `test_the_four_writes_still_require_bot_admin` 的模式（`channels/test_channels_handlers.py:417`）。新建文件，内容如下：

```python
"""identity 组的档位：MEMBER 读、ADMIN+锁写，新旧地址同口径。

迁移前 identity 行是 ``OWNER_SCOPED``（只有 owner 可达）；本文件钉住迁移后的
档位，防止将来有人把 PUT 悄悄放回 MEMBER 而让协作者重写人格文件。
"""

from __future__ import annotations

from agentclaw.community.adapters.http.openapi_v1.authorization import (
    AUTHORIZATION,
    Check,
    EDIT_LOCK,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel

READS = [
    ("GET", "/openapi/v1/bots/{bot_id}/identity"),
    ("GET", "/openapi/v1/bots/{bot_id}/identity/{file_type}"),
]
LEGACY_READS = [
    ("GET", "/openapi/v1/bots/identity/{bot_id}"),
    ("GET", "/openapi/v1/bots/identity/{bot_id}/{file_type}"),
]
WRITES = [
    ("PUT", "/openapi/v1/bots/{bot_id}/identity/{file_type}"),
]
LEGACY_WRITES = [
    ("PUT", "/openapi/v1/bots/identity/{bot_id}/{file_type}"),
]


def test_reads_are_member_barred():
    """读人格文件 = 参与协作编辑的一部分（config-manifest read 同档）。"""
    for key in READS + LEGACY_READS:
        rule = AUTHORIZATION[key]
        assert isinstance(rule, Check), f"{key} is not adjudicated"
        assert rule.level is PermissionLevel.MEMBER, (
            f"{key} is not the member-level read the migration decided"
        )


def test_writes_are_admin_barred_behind_the_lock():
    """重写 RULES/SOUL 是 ADMIN 行为，且协作 bot 要求持编辑锁。"""
    for key in WRITES + LEGACY_WRITES:
        rule = AUTHORIZATION[key]
        assert isinstance(rule, Check), f"{key} is not adjudicated"
        assert rule.level is PermissionLevel.ADMIN, (
            f"{key} moved off the ADMIN bar the migration decided"
        )
        assert rule.edit_lock is EDIT_LOCK, f"{key} lost its edit-lock bar"
```

- [ ] **Step 2: 跑它确认失败**

```bash
cd src/backend && pytest tests/community/adapters/http/openapi_v1/identity/test_identity_bars.py -q
```
Expected: **2 failed**（行是 `_Scaffold("OWNER_SCOPED")` / `INHERITED`，不是 `Check`）。

- [ ] **Step 3: Commit（RED 状态不 commit，等 Task 2 一起原子提交）**

不提交。Task 2 的 commit 会带上这个新文件。

---

### Task 2: seam 原子迁移（GREEN）

一次 commit 内完成下列所有编辑 —— 这些行之间有安全耦合：router 换成 `OwnerIdDep` 但表行仍是 `OWNER_SCOPED` 的中间态是**未裁决的 addressed owner**（洞）；表行翻 `Check` 但 handler 还拿 `UserIdDep` 的中间态是 gate 判一个 bot、handler 动另一个（`test_authorization_inventory.py` 会抓）。

**Files:**
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/authorization.py:296-298`（替代地址三行）
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/authorization.py:642-644`（退役地址三行）
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/admission.py:251-258`（三行模式）
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/__init__.py`（mount 两组，~L403 与 L586 两处）
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/deprecated/engine_runtime.py`（identity 换挂载桶 + 模块 docstring）
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/deprecated/__init__.py`（导出新桶）
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/identity/router.py`（三 handler 签名 + import + I2 注释）
- Modify: `src/backend/tests/community/adapters/http/openapi_v1/identity/test_identity_handlers.py`（7 处直接调用加 `user_id` + 新增 addressed-owner 测试）

- [ ] **Step 1: authorization.py — 替代地址行**

old_string:
```python
    ("GET", "/openapi/v1/bots/{bot_id}/identity"): OWNER_SCOPED,
    ("GET", "/openapi/v1/bots/{bot_id}/identity/{file_type}"): OWNER_SCOPED,
    ("PUT", "/openapi/v1/bots/{bot_id}/identity/{file_type}"): OWNER_SCOPED,
```
new_string:
```python
    # Identity is the bot's persona — RULES, SOUL, the rest of the sixteen.
    # Reading how a bot is set up is part of working on it (the config-manifest
    # read's bar), so any collaborator at MEMBER may; rewriting a persona is an
    # ADMIN act behind the edit lock, like the manifest beside it. The retiring
    # addresses below mirror these rows explicitly, the way the engine-runtime
    # legacy rows do.
    ("GET", "/openapi/v1/bots/{bot_id}/identity"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/{bot_id}/identity/{file_type}"): Check(PermissionLevel.MEMBER),
    ("PUT", "/openapi/v1/bots/{bot_id}/identity/{file_type}"): Check(PermissionLevel.ADMIN, EDIT_LOCK),
```

- [ ] **Step 2: authorization.py — 退役地址行**

old_string:
```python
    ("GET", "/openapi/v1/bots/identity/{bot_id}"): INHERITED,
    ("GET", "/openapi/v1/bots/identity/{bot_id}/{file_type}"): INHERITED,
    ("PUT", "/openapi/v1/bots/identity/{bot_id}/{file_type}"): INHERITED,
```
new_string:
```python
    # Identity's retiring addresses mirror the replacement's new rows, not
    # INHERITED: ``_rule_for`` resolves a row by path and never crosses the
    # LEGACY_ROUTES mapping, and ``relocate`` does not copy route-level gate
    # dependencies — an INHERITED row here would leave the legacy address with
    # an unadjudicated handler that reads ``owner_id`` off the wire.
    ("GET", "/openapi/v1/bots/identity/{bot_id}"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/identity/{bot_id}/{file_type}"): Check(PermissionLevel.MEMBER),
    ("PUT", "/openapi/v1/bots/identity/{bot_id}/{file_type}"): Check(PermissionLevel.ADMIN, EDIT_LOCK),
```

- [ ] **Step 3: admission.py — 三行模式翻面**

admission.py `AdmissionMode` 表中三处 `("…/identity…"): AdmissionMode.GRANT_CHECKED_OWN_BOT,` 翻成 `AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT`。当前:
```python
    ("GET", "/openapi/v1/bots/{bot_id}/identity"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "GET",
        "/openapi/v1/bots/{bot_id}/identity/{file_type}",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
    (
        "PUT",
        "/openapi/v1/bots/{bot_id}/identity/{file_type}",
    ): AdmissionMode.GRANT_CHECKED_OWN_BOT,
```
逐行替换 `AdmissionMode.GRANT_CHECKED_OWN_BOT` → `AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT`（仅此三条，别动 resources/data-init 等邻居）。

- [ ] **Step 4: openapi_v1/__init__.py — 替代组换桶**

(a) 从 `_GRANT_CHECKED_SUBGROUPS` 删除。old_string:
```python
    engine_config_router,
    identity_router,
    resources_router,
    routines_router,
]
```
new_string:
```python
    engine_config_router,
    resources_router,
    routines_router,
]
```

(b) 加入 `_ADDRESSED_BOT_SUBGROUPS`。old_string:
```python
    # The config manifest is collaborator-scoped (MEMBER to read, ADMIN to
    # write), so it may address a shared bot and takes the addressed-owner
    # grant rather than the own-bot one.
    config_manifest_router,
```
new_string:
```python
    # The config manifest is collaborator-scoped (MEMBER to read, ADMIN to
    # write), so it may address a shared bot and takes the addressed-owner
    # grant rather than the own-bot one.
    config_manifest_router,
    # Identity is persona config on the same bars as the manifest: MEMBER to
    # read, ADMIN behind the edit lock to write. It may address a shared bot.
    identity_router,
```

- [ ] **Step 5: deprecated/engine_runtime.py + deprecated/__init__.py — 退役组换桶**

(a) `deprecated/engine_runtime.py` 模块 docstring 倒数第二段 old:
```text
Two routers rather than one, because the two halves are mounted differently and
the mount is part of what a caller experiences. The engine-runtime groups
document a 501 and 504 and resolve their own owner; identity is grant-checked
at the mount. Merging them would give one half the other's contract.
```
new:
```text
Two routers rather than one, because the two halves are mounted differently and
the mount is part of what a caller experiences. The engine-runtime groups
document a 501 and 504 and resolve their own owner; identity takes the
addressed-bot grant at the mount, like its replacement. Merging them would
give one half the other's contract.
```

(b) 尾部列表 old_string:
```python
#: Mounted grant-checked, like its replacement.
GRANT_CHECKED: list[APIRouter] = [identity]
```
new_string:
```python
#: The config-manifest-style addressed-bot grant, like its replacement. The
#: retiring identity functions still declare ``OwnerIdDep``, so the grant the
#: mount declares is the one that adjudicates the owner the handler reads.
ADDRESSED: list[APIRouter] = [identity]
```

(c) `deprecated/__init__.py`：`GRANT_CHECKED_GROUPS = _engine_runtime.GRANT_CHECKED + [...]` 组合处，新增导出。把（L57 附近):
```python
GRANT_CHECKED_GROUPS = _engine_runtime.GRANT_CHECKED + [
```
改为:
```python
ADDRESSED_GROUPS = _engine_runtime.ADDRESSED

GRANT_CHECKED_GROUPS = _engine_runtime.GRANT_CHECKED + [
```
并在 `__all__` 中加 `"ADDRESSED_GROUPS",`（`"GRANT_CHECKED_GROUPS"` 旁）。
注意：`deprecated/__init__.py` 顶部 `GRANT_CHECKED` 不再被 engine_runtime 提供（已改名 ADDRESSED），若 `GRANT_CHECKED` 来自其它模块的 re-export 组合会报 ImportError —— 按实际报错把 `_engine_runtime.GRANT_CHECKED` 引用清干净（engine_runtime 里该名字已随 Step 5b 删除）。

(d) `openapi_v1/__init__.py`：L196 附近 import 处加 `ADDRESSED_GROUPS as _LEGACY_ADDRESSED,`（与 `GRANT_CHECKED_GROUPS as _LEGACY_GRANT_CHECKED` 并列），并在 L584 老循环后加新循环:
```python
    for router in _LEGACY_ADDRESSED:
        public.include_router(
            router,
            responses=USER_SCOPED_ERROR_RESPONSES,
            dependencies=_PUBLIC_AUTH + _GRANT_CHECKED_ADDRESSED_BOT,
        )
```

- [ ] **Step 6: identity/router.py — 三 handler 签名**

(a) import：old_string:
```python
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    StageQuery,
    WriteStageQuery,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
```
new_string:
```python
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
    StageQuery,
    WriteStageQuery,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
```

(b) `list_bot_identity_files`：old_string:
```python
async def list_bot_identity_files(
    bot_id: BotIdPath,
    owner_id: UserIdDep,
```
new_string:
```python
async def list_bot_identity_files(
    bot_id: BotIdPath,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
```
函数体内 I2 注释 old:
```python
    # I2: entity_type/entity_id/operator_id come from the authenticated
    # request's user_id parameter (personal bot owner = the named user). The
    # pair is resolved in ``core`` so manifest apply addresses identity the same
    # way without a request.
```
new:
```python
    # I2: the owner is the addressed one (OwnerIdDep: the caller by default,
    # the shared bot's owner when named) — entity/coords all derive from it.
    # The pair is resolved in ``core`` so manifest apply addresses identity
    # the same way without a request.
```

(c) `get_bot_identity_file`：old_string:
```python
async def get_bot_identity_file(
    bot_id: BotIdPath,
    file_type: FileTypePath,
    owner_id: UserIdDep,
```
new_string:
```python
async def get_bot_identity_file(
    bot_id: BotIdPath,
    file_type: FileTypePath,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
```
其 I2/I3 注释 old:
```python
    # I2: entity params come from the authenticated principal via UserIdDep
    # (personal bot owner = the named user). I3: publish_id is not exposed —
```
new:
```python
    # I2: the owner is the addressed one (OwnerIdDep). I3: publish_id is not exposed —
```

(d) `update_bot_identity_file`：old_string:
```python
async def update_bot_identity_file(
    bot_id: BotIdPath,
    file_type: FileTypePath,
    body: IdentityFileWrite,
    owner_id: UserIdDep,
```
new_string:
```python
async def update_bot_identity_file(
    bot_id: BotIdPath,
    file_type: FileTypePath,
    body: IdentityFileWrite,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
```
其 I2 注释 old:
```python
    # I2: entity params come from the authenticated principal via UserIdDep
    # as above; validate_file_type requires the <type>.md form.
```
new:
```python
    # I2: the owner is the addressed one (OwnerIdDep), as above;
    # validate_file_type requires the <type>.md form.
```

**重要约束：三个操作的 docstring（`"""List every identity file…"""` 等）一个字都不要动** —— `deprecated/engine_runtime.py` 里 `_DROP_STAGE` 的 rewords map 按 `(method, path)` 字面匹配这些 docstring 片段，改了会在 import 时炸。

- [ ] **Step 7: test_identity_handlers.py — 7 处调用点 + 新测试**

7 处对三个 handler 的直接调用（list×3: `test_list_bot_identity_files_returns_all_16_with_exists` / `_marks_absent_files_false` / `_reads_trace_id_from_request_state`；`get_bot_identity_file` 4 处调用所在测试；`update_bot_identity_file` 4 处调用所在测试），把 `owner_id="u1",` 前加一行 `user_id="u1",`。示意:
```python
    env = await list_bot_identity_files(
        bot_id="bot-x",
        user_id="u1",
        owner_id="u1",
        identity_service=service,
        request=_request_without_trace(),
    )
```
在文件尾部新增（git 状态注意该文件同目录有 `__init__.py`，直接 append）:
```python
@pytest.mark.asyncio
async def test_list_flows_the_addressed_owner_through_to_the_service():
    """Named owner coordinates threaded through when someone else calls it.

    ``OwnerIdDep`` resolves the addressed owner — the caller by default, the
    shared bot's owner when named. The service must see the addressed owner's
    entity coordinates, never the caller's: identity files live under the
    owner's workspace regardless of who reads them.
    """
    service = _StubIdentityService(_all_present())

    env = await list_bot_identity_files(
        bot_id="bot-x",
        user_id="collab-1",
        owner_id="owner-9",
        identity_service=service,
        request=_request_without_trace(),
    )

    assert env.code == CODE_OK
    assert service.last_call_kwargs["entity_type"] == "staff"
    assert service.last_call_kwargs["entity_id"] == "owner-9"
    assert service.last_call_kwargs["owner_id"] == "owner-9"
    assert service.last_call_kwargs["bot_id"] == "bot-x"
```

- [ ] **Step 8: 跑受影响面测试全部转绿**

```bash
cd src/backend && pytest \
  tests/community/adapters/http/openapi_v1/identity \
  tests/community/adapters/http/openapi_v1/test_bot_access.py \
  tests/community/adapters/http/openapi_v1/test_admission_inventory.py \
  tests/community/adapters/http/openapi_v1/test_authorization_inventory.py \
  tests/community/adapters/http/openapi_v1/test_config_manifest_apply_bars.py \
  tests/community/adapters/http/openapi_v1/channels \
  --no-header -q
```
Expected: 全 PASS。若 `test_admission_inventory.py` 报 mode/dependency 不一致 → 回查 Step 3 与 Step 5；若 `test_authorization_inventory.py` 报 `_assert_check_rows_are_enforceable` → handler 与 OwnerIdDep 未接上；`scaffolding_row_count` 断言是动态计数，行数变化不需改测试。

若逢 import 循环报错（`BotService cannot import`），先读报错链最先失败的模块、按现仓库同惯例绕开（历史上绕法是调整 import 顺序/从叶子模块先 import），不要改业务代码结构。

- [ ] **Step 9: Commit**

```bash
git add -A \
  src/backend/src/agentclaw/community/adapters/http/openapi_v1/authorization.py \
  src/backend/src/agentclaw/community/adapters/http/openapi_v1/admission.py \
  src/backend/src/agentclaw/community/adapters/http/openapi_v1/__init__.py \
  src/backend/src/agentclaw/community/adapters/http/openapi_v1/deprecated/engine_runtime.py \
  src/backend/src/agentclaw/community/adapters/http/openapi_v1/deprecated/__init__.py \
  src/backend/src/agentclaw/community/adapters/http/openapi_v1/identity/router.py \
  src/backend/tests/community/adapters/http/openapi_v1/identity/
git commit -m "feat(openapi): identity bot files admit collaborators (MEMBER read, ADMIN+edit-lock write)

The identity group moves off OWNER_SCOPED like the config-manifest group
before it: handlers resolve the addressed owner via OwnerIdDep, the surface
publishes an optional owner_id query parameter, and both addresses carry
explicit Check rows so the retiring paths adjudicate the same bars."
```

---

### Task 3: gateway spec 手工外科手术

**Files:**
- Modify: `src/gateway/configs/schemas/bots.openapi.json`（只增不删）
- Verify: ocb 侧 `bots.openapi.json`（期望是 git link，免双写）

- [ ] **Step 1: 跑插入脚本（six ops 提升 owner_id、two PUT 加 423）**

保存为临时脚本 `scripts/spec-add-identity-owner-id.py`（一次性工具，末步删除或留作 bin 外不提交）后执行。脚本从 spec 自身抠出 channels 的 `owner_id` 参数条目与 423 响应条目原文再插入 identity 各操作，保证文案与既有面零漂移:

```python
"""Surgical insert of owner_id + 423 into bots.openapi.json identity ops.

Inserts only; never rewrites the file wholesale. All anchors verified live:
the six identity operations' parameters arrays end with the identical
`"title": "User Id" … }\n          }\n        ]` tail, and the owner_id /
423 reference texts are carved from the channels operations themselves.

IMPORTANT还记得 — do NOT json.dump the whole file: round-trip is not
byte-stable and would create a 3MB diff.

ISH 要改的 operationIds:
  list_bot_identity_files_openapi_v1_bots__bot_id__identity_get          (op1)
  get_bot_identity_file_openapi_v1_bots__bot_id__identity__file_type__get  (op2)
  update_bot_identity_file_openapi_v1_bots__bot_id__identity__file_type__put(op3)
  list_bot_identity_files_openapi_v1_bots_identity__bot_id__get         (op4 legacy)
  get_bot_identity_file_openapi_v1_bots_identity__bot_id___file_type__get   (op5 legacy)
  update_bot_identity_file_openapi_v1_bots_identity__bot_id___file_type__put (op6 legacy)
"""
from __future__ import annotations
import json
import sys

P = "src/gateway/configs/schemas/bots.openapi.json"
s = open(P, encoding="utf-8").read()


def block_after(anchor: str, close_pat: str, opener: str) -> tuple[str, str]:
    """Carve the text of one entry starting at ``opener`` after ``anchor``."""
    i = s.find(anchor)
    assert i >= 0, f"anchor not found: {anchor!r}"
    j = s.find(opener, i)
    assert j >= 0, f"opener {opener!r} not found after anchor"
    # brace-count to the entry's own close
    depth = 0
    k = j
    while True:
        if s[k] == "{":
            depth += 1
        elif s[k] == "}":
            depth -= 1
            if depth == 0:
                break
        k += 1
    return s[i], s[j : k + 1]


# 1) owner_id parameter entry, carved verbatim from the channels list op.
chan_list_id = '"operationId": "list_channels_openapi_v1_bots__bot_id__channels_get"'
i = s.find(chan_list_id)
assert i >= 0, "channels list op not found"
j = s.find('"name": "owner_id"', i)
start = s.rfind("\n          {", i, j)
assert start >= 0, "owner_id entry start not found in channels op"
depth = 0
k = start + s[start:].find("{")
t = k
while True:
    if s[t] == "{":
        depth += 1
    elif s[t] == "}":
        depth -= 1
        if depth == 0:
            break
    t += 1
OWNER_ID_ENTRY = s[start + 1 : t + 1]  # keep leading newline+indent, no tail

# 2) 423 response entry, carved verbatim from the channels create op.
m = s.find('"operationId": "create_channel_openapi_v1_bots__bot_id__channels_post"')
if m < 0:  # fall back: any channels POST works as the carve source
    m = s.find('"operationId": "create_channel_openapi_v1_bots__bot_id__channels_post"'.replace("create_channel", "create_channel"))
ANCHOR423 = '"code": 423000'
i423 = s.find(ANCHOR423)
assert i423 >= 0, "no 423 example anywhere to carve from"
kstart = s.rfind('"423": {', 0, i423)
assert kstart >= 0
depth = 0
t2 = kstart + kstart - kstart
t2 = s.find("{", kstart)
depth = 0
while True:
    if s[t2] == "{":
        depth += 1
    elif s[t2] == "}":
        depth -= 1
        if depth == 0:
            break
    t2 += 1
RESP423_FULL = s[kstart : t2 + 1]           # `        "423": { … }`
RESP423_INNER = RESP423_FULL[RESP423_FULL.find("{"): t2 + 1 - (kstart + len('"423": '))]
if RESP423_INNER.startswith("{"):  # strip to inner keys object: keep original 8-space chunk
    pass
# simpler: re-splice ourselves below with computed sizes — see insert step.

SIX_OPIDS = [
 "list_bot_identity_files_openapi_v1_bots__bot_id__identity_get",
 "get_bot_identity_file_openapi_v1_bots__bot_id__identity__file_type__get",
 "update_bot_identity_file_openapi_v1_bots__bot_id__identity__file_type__put",
 "list_bot_identity_files_openapi_v1_bots_identity__bot_id__get",
 "get_bot_identity_file_openapi_v1_bots_identity__bot_id___file_type__get",
 "update_bot_identity_file_openapi_v1_bots_identity__bot_id___file_type__put",
]

out = s
for opid in SIX_OPIDS:
    anchor = f'"operationId": "{opid}"'
    i = out.find(anchor)
    assert i >= 0, f"{opid} not found"
    # parameters array tail: first occurrence of the closing pattern after opid
    tail = '\n          }\n        ]'
    ti = out.find(tail, i)
    assert ti >= 0, f"parameters tail not found for {opid}"
    seg = out[i:ti]
    assert '"name": "owner_id"' not in seg, f"{opid} already has owner_id (idempotency)"
    insert = "," + OWNER_ID_ENTRY.split("\n", 1)[0]  # placeholder, replaced below
    # append entry between the last `}` of the array and `]`:
    entry = "\n        " + OwnerIdEntryAsParameter()  # noqa: F821 — see note
    raise SystemExit("SCRIPT-PLACEHOLDER — read Task 3 Step 1 note below")

```

**上面这段骨架不要照抄执行**——它演示了锚点策略。实际执行就 [ollowing 下述参数化逻辑，写入文件 `scripts/spec-add-identity-owner-id.py` 再跑：

```python
"""Add owner_id (6 identity ops) and 423 (2 identity PUTs) to bots.openapi.json.

Surgical insert-only edit. Carves both reference texts from the channels
operations already in this file, so wording matches the published surface
exactly. Idempotent: re-running detects existing owner_id and skips.
"""
from __future__ import annotations
import json

P = "src/gateway/configs/schemas/bots.openapi.json"
s = open(P, encoding="utf-8").read()
orig_len = len(s)

# ── carve the owner_id parameter entry out of the channels list op ──
ci = s.find('"operationId": "list_channels_openapi_v1_bots__bot_id__channels_get"')
assert ci >= 0, "channels list operationId not found"
oj = s.find('"name": "owner_id"', ci)
start = s.rfind("\n          {", ci, oj)
depth = 0
t = s.find("{", start)
while True:
    if s[t] == "{":
        depth += 1
    elif s[t] == "}":
        depth -= 1
        if depth == 0:
            break
    t += 1
OWNER_ID = s[start + 1 : t + 1]          # entry text, 10-space leading indent
assert '"owner_id"' in OWNER_ID and '"query"' in OWNER_ID

# ── carve the 423 response entry out of any channels write (POST/PATCH) ──
e423 = s.find('"code": 423000')
assert e423 >= 0, "no 423 example in file to carve from"
rstart = s.rfind('"423": {', 0, e423)
t = s.find("{", rstart)
depth = 0
while True:
    if s[t] == "{":
        depth += 1
    elif s[t] == "}":
        depth -= 1
        if depth == 0:
            break
    t += 1
BLOCK423 = s[rstart:t + 1]              # `        "423": { … }` with 8-space key
assert '"423000"' in BLOCK423

GET_OPS = [
 "list_bot_identity_files_openapi_v1_bots__bot_id__identity_get",
 "get_bot_identity_file_openapi_v1_bots__bot_id__identity__file_type__get",
 "list_bot_identity_files_openapi_v1_bots_identity__bot_id__get",
 "get_bot_identity_file_openapi_v1_bots_identity__bot_id___file_type__get",
]
PUT_OPS = [
 "update_bot_identity_file_openapi_v1_bots__bot_id__identity__file_type__put",
 "update_bot_identity_file_openapi_v1_bots_identity__bot_id___file_type__put",
]

TAIL = "\n          }\n        ]"        # last param entry close + params ]

for opid in GET_OPS + PUT_OPS:
    anchor = f'"operationId": "{opid}"'
    i = s.find(anchor)
    assert i >= 0, f"{opid} not found"
    ti = s.find(TAIL, i)
    assert ti >= 0, f"parameters tail not found for {opid}"
    assert '"name": "owner_id"' not in s[i:ti], f"{opid} already publishes owner_id"
    insertion = ",\n" + OWNER_ID.lstrip("\n") if False else ",\n" + OWNER_ID
    s = s[: ti - len("\n          }")] + "\n          }" + insertion.replace("\n          {", "\n          {", 1) + s[ti:]
s = s  # placeholder line; real code below in the executor's copy:

```

**再次注意：上面两段代码块中第二段是可执行版**；跑之前自查两处容易写错的地方——(1) `insertion` 必须让新 entry 前有 `,` 且与被 splice 的 `\n          }` 顺序为 `{旧参数尾},\n{owner_id entry}\n        ]`；(2) `OWNER_ID` 文本已含前导 `\n          {`，拼接格式为:

```python
head = "\n          }"           # the last existing parameter entry's close
s = s[:ti] + "," + OWNER_ID + "\n        ]" + s[ti + len(TAIL):]
```

脚本的 423 部分:

```python
for opid in PUT_OPS:
    anchor = f'"operationId": "{opid}"'
    i = s.find(anchor)
    assert i >= 0, f"{opid} not found"
    k422 = s.find('"422"', i)
    assert k422 >= 0, f"422 key absent for {opid} (anchor for 423 insert)"
    assert '"423"' not in s[i:k422], f"{opid} responses already carry 423"
    s = s[:k422] + BLOCK423 + "\n        " + s[k422:]
```

随后写回与校验:

```python
json.loads(s)                     # must parse
assert s.count('"name": "owner_id"') >= orig_owner_count + 6
open(P, "w", encoding="utf-8").write(s)
```

- [ ] **Step 2: 校验**

```bash
cd /Users/rongzhi/PycharmProjects/Avernet
python3 -c "import json; json.load(open('src/gateway/configs/schemas/bots.openapi.json'))" && echo PARSE-OK
git diff --numstat src/gateway/configs/schemas/bots.openapi.json   # 期望: 只有 insertions，deletions == 0
git diff src/gateway/configs/schemas/bots.openapi.json | grep -c '^+'   # ~ +7 字段 total (6 owner_id + 2× 423)
git diff src/gateway/configs/schemas/bots.openapi.json | grep '^-' | grep -v '^---' | wc -l   # 期望 0
```
Expected: PARSE-OK；numstat 第一列（增）> 0，第二列（删）= 0。

- [ ] **Step 3: ocb 侧核验（预期零改动）**

```bash
OCB_PATH=$(find ~/IdeaProjects/ocb -name bots.openapi.json | head -1) && echo "$OCB_PATH" && ls -la "$OCB_PATH"
```
- 若是 symlink/git link 指回 Avernet 这份 → 无需任何动作。
- 若是**独立文件拷贝**（ls 无链接标记）→ 复制一份 `cp src/gateway/configs/schemas/bots.openapi.json "$OCB_PATH"`（该分支下做这个 copy 也一并纳入本任务 commit）。

- [ ] **Step 4: Commit**

```bash
git add src/gateway/configs/schemas/bots.openapi.json
# ocb 为独立拷贝时也 git add ocb 内容（通常不需要）
git commit -m "feat(gateway): publish owner_id on the identity operations (+423 on the file write)"
```

---

### Task 4: 注释治枯 + 分层验证

**Files:**
- Modify: `src/backend/tests/community/adapters/http/openapi_v1/test_authorization_inventory.py:666`（钉死的数字改口径）

- [ ] **Step 1: 治枯 "all 34"**

old_string:
```python
    ``bot_access`` reads ``OwnerIdDep``; a handler that takes ``UserIdDep`` as
    its owner — as all 34 of today's ``OWNER_SCOPED`` handlers do — reads a
```
new_string:
```python
    ``bot_access`` reads ``OwnerIdDep``; a handler that takes ``UserIdDep`` as
    its owner — as every ``OWNER_SCOPED`` handler does — reads a
```

- [ ] **Step 2: 受影响面子集**

```bash
cd src/backend && pytest \
  tests/community/adapters/http/openapi_v1/identity \
  tests/community/adapters/http/openapi_v1/test_bot_access.py \
  tests/community/adapters/http/openapi_v1/test_admission_inventory.py \
  tests/community/adapters/http/openapi_v1/test_authorization_inventory.py \
  --no-header -q
```
Expected: 全 PASS。

- [ ] **Step 3: gateway 侧 schema 目录测试**

```bash
cd src/gateway && pytest tests/unit/core/forwarding/test_served_openapi.py \
  tests/unit/core/forwarding/test_combined_schema_catalogs.py -q
```
Expected: PASS（catalog 加载语义未变，仅参数面扩展）。

- [ ] **Step 4: 全量本地 gate（push 前唯一一次）**

```bash
cd src/backend && bash scripts/ci_test.sh
```
Expected: 全绿，changed-line coverage ≥80%（identity router 改动行已被新/旧用例覆盖；spec 是 JSON 无行覆盖负担）。

- [ ] **Step 5: Commit**

```bash
git add src/backend/tests/community/adapters/http/openapi_v1/test_authorization_inventory.py
git commit -m "test(openapi): stop pinning the OWNER_SCOPED handler count"
```

- [ ] **Step 6: push/PR 前提示（不自动执行）**

- `gh pr list --head "$(git branch --show-current)"` 查重；开 PR 前确认本机未被切走分支（并行作业建议 worktree 隔离）。
- 推 dev 需带 `AVERNET_PRE_PUSH_MERGE_TARGET=dev` env 覆盖（本机 git config 钉在 REL）。
- 本地已跑过全量 `ci_test.sh` 后，PR CI 即同口径复现，无需再本地重复。
- PR 标题建议：`feat(openapi): identity files admit collaborators via owner_id (MEMBER read / ADMIN+lock write)`，PR 描述按 house 格式 Problem/Solution/Validation。

---

## Self-Review 结论

- **Spec 覆盖**：GET×2 MEMBER、PUT ADMIN+EDIT_LOCK、owner_id 参数双地址、admission、mount、退役地址行、ocb 核验、验证四层 —— 每项均映射到具体 Task/Step。service 层零改（`entity_id=owner_id` 本为参数）；`_rule_for` 不加机制（退役行显式 Check）。
- **类型一致性**：`OwnerIdDep`/`UserIdDep`/`AddressedBotGrantDep`/`Check`/`EDIT_LOCK` 引用与源模块导出名一致（channels 测试同款 import）。`user_id="u1"` 新 kwarg 与 Step 6 签名一致。
- **待执行者留意**：spec 脚本两段代码块中**第二段是可执行骨架**，第一段为锚点演示 —— 执行时只落一个最终脚本；423 拼接在 `"422"` 键之前保持 responses 键接近升序。若 `_DROP_STAGE` import 时报 docstring 不匹配 → Step 6 的"docstring 不动"约束被违反。
---

## Post-review addendum (2026-09-17, applied to this PR)

The shipped scope grew beyond this document's identity-only draft to the full
collaborator-access migration (25 rows); the eight review findings landed on
top of that. Resolutions now in the code:

1. `template_config` masking (`_to_bot(include_template=…)`): the 2026-09-01
   passthrough's premise ("the query faces are owner-scoped") broke with
   Check(MEMBER); the owner's snapshot no longer reaches a collaborator's
   detail/restart response. Regression-tested both directions.
2. data-init POST hardened to `Check(OWNER, EDIT_LOCK)`: the trigger collects
   the *caller's* IAM token onto the owner's record, so the actor must be the
   owner.
3. startup-script `modifier` attributes the verified caller (`user_id`), not
   the addressed owner.
4. resources upload records the uploader (`user_id`/`created_by` = caller).
5. `GET /bots/{bot_id}/status` **stays OWNER** (correcting this addendum's
   first resolution): `test_admin_collaborator_cannot_poll_dormant_status`
   pins that an ADMIN collaborator may not poll a dormant bot's readout, so
   the owner-only row is deliberate — the base read's comment was rewritten
   to stop citing status instead, which is finding 5's other offered arm.
6. Container-instance restart **stays OWNER** as well: the facade's
   four-keeper test pins `_resolve_bot`'s second owner enforcement behind the
   gate, so lowering the row would make the seam claim a share the facade
   refuses. The asymmetry with the member's whole-bot restart is now
   documented at both rows.
7. The legacy-identity forced-move chain (twin guard → no-{bot_id} exemption
   → Check row consumes OwnerIdDep → the retiring addresses publish and
   honour owner_id, contra the freeze principle) documented at the rows.
8. Spec surgery discipline for future hand edits: pure-insertion anchors only.
   This PR's −902 lines are diff re-pairing artifacts on near-identical
   response blocks (jq-verified zero semantic deletions) — do not repeat the
   423 block-re-anchor; future edits anchor tight like the other ~37 hunks.

Not fixed (recorded, non-blocking): seam-test stub duplication (use
`bind_bot_access_seam`), `_PINNED_TWINS`/admission pinned-block duplication,
bot_access double per-request bot resolution.
