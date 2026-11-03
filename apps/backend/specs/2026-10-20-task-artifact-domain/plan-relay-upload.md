# 产物写口 relay 直传分支（双凭证）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `POST /api/v1/collaboration/tasks/artifacts/object-file` 与 `POST /api/v1/collaboration/tasks/artifacts` 增加双凭证——请求带 `X-Relay-Token` 时按 relay 尝试处理（token fence + creator 取节点 assignee），否则维持用户登录态现状；读口不动。

**Architecture:** token fence 的恒时比对逻辑从 adapter 下沉到 core 单函数，既有 `require_relay_attempt` 改委托（旧 relay 路由零行为变化）；两个写口的路由换用统一的"写侧 actor"依赖做分支选择（HTTP 只转协议，Rule 22）；核心服务各增一个可选 `relay_token` 入参收口领域语义。`T | None` 合法性:凭证二选一的入参在外部输入边界上（CLAUDE.md 类型契约允许）。

**Tech Stack:** Python 3.12 / FastAPI / pytest（`DEPLOY_PROFILE=test uv run pytest`），声明式端点测试框架 `tests/community/framework`。

**Spec:** `apps/backend/specs/2026-10-20-task-artifact-domain/spec.md` **§14、§14.5**（本计划从 §14 立论，执行者两份都读）。

## Global Constraints

- 测试命令一律 `cd apps/backend && DEPLOY_PROFILE=test uv run pytest <path> -q`；CI 门 `./scripts/ci_test.sh`（改动行覆盖 ≥80%）。
- 提交标题格式 `<type>(<scope>): <outcome>`，scope 用 `task`（先例 `feat(task): support draggable multi-task switching`）；rebase 不 merge。
- 不做顺手格式化：改动行之外零 whitespace/import churn（BCS 无关，Python 侧同纪律）。
- 1000 行文件帽：`artifact_object_file_routes.py`、`artifact_routes.py`、`artifact_api_service.py` 现状体积离帽尚有量,但新增代码不留超长 docstring。
- 错误映射零新增：`TaskStateError → 409`（`openapi_v1/responses.py:810` 既有）。
- **出范围**（§14.5 用户定稿,勿顺手做）：executor_app 归属对称校验、per-node 配额、下载/列产物口的 relay 分支。
- 空串 `X-Relay-Token` header 视为未带（falsy → 用户分支）。

## Review Focus

执行中优先盯这五类输入（spec 未逐字展开、但端点用例必须钉死的）：

1. **过期代次 token**——节点 `extend_props.relay_token` 已轮换后持旧 token 上传 → 预期 409"attempt has expired"，不得落行落字节。
2. **空 header**——`X-Relay-Token: ""`（空白串）→ 按用户分支处理，无登录态时 401，而非 409。
3. **未认领节点**——token 合法但 `run_info.assignee` 为空（派发后无人 claim）→ 上传口 409 拒绝；补录口不拒（creator 走既有 `system` 兜底）。
4. **双凭证齐备**——请求同时带合法 cookie 和合法 token → token 分支胜出（分支只看 header），creator=assignee；不因 cookie 存在而改走用户分支。
5. **fence 先于 attempt 快照**——token 过期且 attempt 也失配 → 409（fence 先触发），endpoints 用例须显式断言而非依赖顺序巧合。

---

### Task 1: core fence 单函数 + adapter 委托

**Files:**
- Create: `apps/backend/src/agentclaw/community/core/task/task_context/relay_attempt.py`
- Modify: `apps/backend/src/agentclaw/community/adapters/http/task/relay_guard.py`
- Test: `apps/backend/tests/community/core/task/task_context/test_relay_attempt.py`

**Interfaces:**
- Produces: `verify_relay_attempt(node: TaskNode, relay_token: str) -> None`——失配/空值 `raise TaskStateError("relay execution attempt has expired")`；成功静默。后续 Task 3/4 直接 import 此函数。
- `adapters/http/task/relay_guard.py` 的 `require_relay_attempt(node, relay_token)` 签名与异常语义**逐字节不变**（既有调用方 `relay_routes.py` / `router.py` 零改动）。

- [ ] **Step 1: 写失败测试**

```python
# tests/community/core/task/task_context/test_relay_attempt.py
import pytest
from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import RuntimeInfo, TaskNode
from agentclaw.community.core.task.task_context.relay_attempt import verify_relay_attempt

def _node(token):
    return TaskNode(node_id="n", task_id="t", status=Status.PENDING,
        task_spec=<见 fixtures>, run_info=RuntimeInfo(run_mode="relay",
        extend_props={"relay_token": token} if token else {}))
```

用例三件:匹配静默通过 / token 串改一位 → `pytest.raises(TaskStateError, match="attempt has expired")` / 空串或图上无 token → 同异常。`task_spec` 构造参照 `tests/community/core/task/task_center/test_task_service_delivery.py:69` 附近的节点构造写法。

- [ ] **Step 2: 跑测试确认失败**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/task/task_context/test_relay_attempt.py -q`
Expected: FAIL（ModuleNotFoundError: relay_attempt）

- [ ] **Step 3: 实现 `relay_attempt.py`**

把 `relay_guard.py:11-19` 现有函数体（`extend_props` 取值 + `hmac.compare_digest` 三段判断 + 异常文案）搬到 core，模块 docstring 注明"领域语义归属 core;adapter 侧 require_relay_attempt 委托本函数"。然后 `relay_guard.py` 改为:

```python
def require_relay_attempt(node: TaskNode, relay_token: str) -> None:
    verify_relay_attempt(node, relay_token)
```

保留 `require_relay_attempt` 的既有 docstring（网络白名单语境），只在其中加一行"实现委托 core/task/task_context/relay_attempt.py"。

- [ ] **Step 4: 跑新旧测试确认通过**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/task/task_context/test_relay_attempt.py tests/community/repository/task/test_relay_dynamic_delivery_intent.py -q`
Expected: PASS（新用例过,relay 既有套件证明委托零行为变化）

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/task/task_context/relay_attempt.py adapters.../relay_guard.py tests/...
git commit -m "refactor(task): hoist relay attempt fence into core"
```

---

### Task 2: 路由写侧 actor 依赖（双凭证分支选择）

**Files:**
- Create: `apps/backend/src/agentclaw/community/adapters/http/task/artifact_actor.py`
- Test: 并入 Task 5 的 endpoints 用例（这个依赖的消费者就是两个路由，端点级覆盖是最合适的验法；单测框架里对 Request/AuthPlugin 打 stub 的收益低，不单独立测）

**Interfaces:**
- Consumes: `get_current_user(request, auth_plugin) -> AuthenticatedUser`（`adapters/http/auth/dependencies.py:67` 既有签名，直接 await 调用；Unauthorized/LoginRedirectRequired 原样穿透）
- Produces（Task 5/6 消费）:

```python
@dataclass(frozen=True)
class ArtifactWriteActor:
    relay_token: str | None   # header X-Relay-Token 非空白串时非 None
    user: AuthenticatedUser | None  # relay 分支恒 None

async def resolve_artifact_write_actor(
    request: Request, auth_plugin: AuthPlugin = Injected(AuthPlugin)
) -> ArtifactWriteActor
```

- [ ] **Step 1: 实现 `artifact_actor.py`**

分支逻辑（Rule 22:本模块只做协议翻译,不看图不派生 creator）: 从 HTTP header 读出本次请求的 relay 凭证并空白归一(空白→None)——非 None 即 relay 分支，**不触碰 auth_plugin**（relay 分支不发 SSO 网络调用）；None 即 `user = await get_current_user(request, auth_plugin)`（401/302 既有语义天然保留）后返回 user 分支 actor。

- [ ] **Step 2: 冷导入自检**

Run: `DEPLOY_PROFILE=test uv run python -c "from agentclaw.community.adapters.http.task.artifact_actor import resolve_artifact_write_actor; print('ok')"`
Expected: 输出 ok（无循环导入）

- [ ] **Step 3: Commit**

```bash
git add .../artifact_actor.py
git commit -m "feat(task): add dual-credential write actor dependency for artifact routes"
```

---

### Task 3: `upload_object_file` relay 分支（核心服务）

**Files:**
- Modify: `apps/backend/src/agentclaw/community/core/task/task_context/task_artifact/artifact_object_file_service.py:93-112`（Protocol）与 `:141-`（Impl）
- Test: `apps/backend/tests/community/core/task/task_artifact/test_artifact_object_file_service.py`

**Interfaces:**
- Consumes: `verify_relay_attempt`（Task 1）
- Produces（Task 5 消费）: 签名变更为

```python
def upload_object_file(self, *, task_id: str, node_id: str, attempt: int,
    file_name: str, payload: bytes,
    creator: str | None = None,          # 用户分支传 user.id
    artifact_name: str | None = None, content_type: str | None = None,
    relay_token: str | None = None,      # relay 分支传 token
) -> TaskArtifactRecord
```

入参契约（docstring 钉死）: `creator` 与 `relay_token` **二选一**,两者皆空 → `TaskArtifactContentError("上传须提供 creator(用户身份)或 relay_token(relay 尝试)之一")`(422);`relay_token` 非空时 creator 入参被忽略。

- [ ] **Step 1: 写失败测试（追加到既有 service 套件）**

三个新用例（沿用该文件既有 world/fixture 造图习惯,造一个 `extend_props={"relay_token": "gen-1"}`、`run_info.assignee="bot-x"` 的节点）:
1. relay happy: `upload_object_file(..., relay_token="gen-1")` → 返回行 `creator == "bot-x"`,`storage_type=object_file`;
2. 串改: `relay_token="gen-2"` **且 attempt 故意传错** → `pytest.raises(TaskStateError, match="attempt has expired")`（钉校验序:fence 先于 attempt 快照,409 压过 422）;
3. 无认领: 节点 `assignee=None` + 合法 token → `pytest.raises(TaskStateError, match="assignee")`;双空入参 → `pytest.raises(TaskArtifactContentError)`;
4. fence 不放宽 attempt: 合法 token + **错误 attempt** → `TaskArtifactContentError`（422,证明 relay 分支仍受快照对齐约束）。

- [ ] **Step 2: 跑新用例确认失败**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/task/task_artifact/test_artifact_object_file_service.py -q`
Expected: 新用例 FAIL（TypeError: unexpected keyword 'relay_token'），既有用例 PASS

- [ ] **Step 3: 实现**

Protocol 同步加 `relay_token: str | None = None` 十默认值。Impl 在 `artifact_object_file_service.py` 节点定位后、attempt 快照校验前插入（§14.5 校验序: token fence → assignee → attempt）:

```python
if relay_token is not None:
    verify_relay_attempt(node, relay_token)
    if not node.run_info.assignee:
        raise TaskStateError(
            f"relay upload requires claimed assignee: task_id={task_id}, node_id={node_id}"
        )
    creator = node.run_info.assignee
elif creator is None:
    raise TaskArtifactContentError("上传须提供 creator(用户身份)或 relay_token(relay 尝试)之一")
```

用户分支代码路径逐字节不变（creator 由路由传入）。

- [ ] **Step 4: 跑套件确认通过**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/task/task_artifact/ -q`
Expected: PASS（新旧全过）

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(task): relay token fence and assignee creator on object-file upload"
```

---

### Task 4: `add_artifact` relay 分支（补录口，fence-only）

**Files:**
- Modify: `apps/backend/src/agentclaw/community/core/task/task_context/task_artifact/artifact_api_service.py:72-78`（Protocol）与 `:138-`（Impl）
- Test: `apps/backend/tests/community/core/task/task_artifact/test_artifact_api_service.py`

**Interfaces:**
- Consumes: `verify_relay_attempt`（Task 1）
- Produces（Task 6 消费）: `def add_artifact(self, item: ArtifactReportItem, *, relay_token: str | None = None) -> TaskArtifactRecord`——**creator 派生零改动**（既有 `assignee or "system"`,`artifact_api_service.py:169`）；不增设 assignee 空拒绝（§14.5 勘定:补录口 creator 走 system 兜底）。

- [ ] **Step 1: 写失败测试（追加两个用例）**

合法 token + assignee 节点 → 登记成功且 creator 断言与既有用户分支一致;`relay_token` 串改 → `pytest.raises(TaskStateError, match="attempt has expired")` 且仓储无新行。

- [ ] **Step 2: 确认失败**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/task/task_artifact/test_artifact_api_service.py -q`
Expected: FAIL（unexpected keyword）

- [ ] **Step 3: 实现**

Impl 在节点定位（`NodeNotFoundError` 检查）之后插入一行半:

```python
if relay_token is not None:
    verify_relay_attempt(node, relay_token)
```

`_require_owned_task` 与其余流程零改动。

- [ ] **Step 4: 跑套件**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/task/task_artifact/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(task): accept relay token fence on artifact registration"
```

---

### Task 5: 上传路由接 actor 依赖

**Files:**
- Modify: `apps/backend/src/agentclaw/community/adapters/http/task/artifact_object_file_routes.py:41-74`
- Test: `apps/backend/tests/community/endpoints/test_task_artifact_object_file_endpoints.py`

**Interfaces:**
- Consumes: `resolve_artifact_write_actor` / `ArtifactWriteActor`（Task 2）;`upload_object_file` 新签名（Task 3）
- Produces: 路由契约变更后的 HTTP 面（endpoints 声明式用例钉死）

- [ ] **Step 1: 写失败 endpoints 用例（追加四个场景到既有文件）**

seed 扩展: 既有 `_seed_artifact_graph(world)` 之后,经 `world.get(TaskGraphService).update_task_node_info(TaskNodePatch(task_id=_TASK_ID, node_id=_TASK_ID, assignee="bot-endpoint", extend_props_patch={"relay_token": "gen-1"}))` 打上 token+认领。
1. `relay_happy`: `headers={"X-Relay-Token": "gen-1", "cookie": "staff_id=user-endpoint-1"}`（**cookie 与 token 双带,钉 token 分支胜出**）→ 200,`data.creator == "bot-endpoint"`（若走了用户分支会记成 staff id,断言即刻红）;
2. `relay_stale_token`: `headers={"X-Relay-Token": "gen-0"}` → 409,消息含 expired;
3. `relay_empty_header`: `headers={"X-Relay-Token": "  "}` → 401（按用户分支无身份拒绝）;
4. `relay_unclaimed`: seed 只打 token 不打 assignee → 409,消息含 assignee。
同时保留既有 `upload_object_file_unauthenticated`（401）与全部用户分支用例不改动——回归即双凭证不破坏用户面。

- [ ] **Step 2: 确认失败**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/endpoints/test_task_artifact_object_file_endpoints.py -q`
Expected: 新 relay 用例 FAIL（现路由无此 header 处理,409 拿不到/401 挡住）

- [ ] **Step 3: 改路由**

上传路由删 `user: AuthenticatedUser = Depends(get_current_user)`,换 `actor: ArtifactWriteActor = Depends(resolve_artifact_write_actor)`;服务调用改为两分支入参:creator 传 `actor.user.id if actor.user else None`(用户分支),relay 分支补传 actor 携带的 relay_token 入参。

docstring 改写: 删"role 为认证用户…relay 场景不经本口",改为"双凭证:X-Relay-Token(relay 尝试,fence+assignee creator)或登录用户(creator=user.id);读口仍用户面。spec §14"。下载路由 `del user` 一行保留（`GET /content` 鉴权零改动）。

- [ ] **Step 4: 跑端点套件**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/endpoints/test_task_artifact_object_file_endpoints.py -q`
Expected: PASS（新旧全部——含既有 401/404/413/422/502/503）

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(task): relay token upload branch on object-file endpoint"
```

---

### Task 6: 补录路由接 actor 依赖

**Files:**
- Modify: `apps/backend/src/agentclaw/community/adapters/http/task/artifact_routes.py:41-`（仅 POST;GET list 的 `del user` 保留零改动）
- Test: `apps/backend/tests/community/endpoints/test_task_artifact_endpoints.py`

**Interfaces:**
- Consumes: `resolve_artifact_write_actor`（Task 2）;`add_artifact(..., relay_token=)`（Task 4）

- [ ] **Step 1: 写失败 endpoints 用例（两个场景）**

`relay_register_happy`: 无 cookie + `X-Relay-Token` + text_content JSON body（沿用既有 happy 场景的 body）→ 200;`relay_register_stale` → 409 expire 且不落行。

- [ ] **Step 2: 确认失败**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/endpoints/test_task_artifact_endpoints.py -q`
Expected: 新用例 FAIL

- [ ] **Step 3: 改路由**

POST `add_task_artifact` 同法换 actor 依赖;`add_artifact` 补传 actor 携带的 relay_token 入参;`del user` 删;docstring 补一句双凭证（引 §14）。GET 路由不碰。

- [ ] **Step 4: 跑套件**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/endpoints/test_task_artifact_endpoints.py tests/community/endpoints/test_task_artifact_object_file_endpoints.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(task): relay token registration branch on artifact endpoint"
```

---

### Task 7: 全量门禁与 spec 定稿

**Files:**
- Modify: `apps/backend/specs/2026-10-20-task-artifact-domain/spec.md`（§14 标题 `DRAFT 待批` → `用户定稿`,补一行实施验证记录）
- 无新代码

- [ ] **Step 1: 产物域全量回归**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/task/task_artifact/ tests/community/endpoints/test_task_artifact_endpoints.py tests/community/endpoints/test_task_artifact_object_file_endpoints.py tests/community/core/task/task_context/test_relay_attempt.py -q`
Expected: PASS

- [ ] **Step 2: CI 门**

Run: `cd apps/backend && ./scripts/ci_test.sh`
Expected: 通过（含改动行覆盖 ≥80% 与 endpoints 覆盖 gate）

- [ ] **Step 3: spec 定稿 + commit**

§14 标题改定稿,记录验证命令与结果;`git add`spec 与全部相关文件,`git commit -m "docs(task): finalize §14 relay dual-credential amendment"`