# bot_config 新增 `task_nav` kind — 复制任务导航 (bot-workflow-permissions)

Dima: https://project.alipay.com/workItem?workItemId=2026111100120522034
（大安全-Teamclaw W26001113566，状态 ready-for-agent，Gate B 已过 2026-11-11）

## Summary

在 manifest `bot_config` 区段新增 `task_nav` kind，使 **bot 复制时把「任务导航」
(= bot-workflow-permissions)一并复制**到 dst。与既有 `render_screen`（副屏）同型，
落 **ON_CONTAINER** 阶段。架构缝全程已留位（`bot_config_kinds.py` /
`compiler/entries/bot_config.py:44` / `desired_state.py:465` /
`bot_config_kind_registry.py` / `bot_config_render_screen_handler.py` /
`apply_engine_module.py:548` 六处 docstring 均明写 "a future task_nav"），本需求填实。

## Motivation

bot 复制当前补齐了副屏 (`render_screen`)，但 dst bot 的任务导航 (workflow 权限矩阵)
不随复制迁移，导致 dst 缺源 bot 配置的任务导航，需手工补。task_nav 落实后，复制一步到位。
bot_config seam 的 kind 扩展点已为这类「跨服务 sync」留好，task_nav 是第一个跨服务 kind。

## Entry shape（v1）

```yaml
bot_config:
  task_nav:
    - type: "bot"
      source_bot_id: 20261010_i0nsz1lc
      source_owner_id: "165528"
```

- **`type` 鉴别字段**（v1 仅 `"bot"`）：整体克隆源 bot 全部任务导航。
  - 字段集合闭合于 `{type, source_bot_id, source_owner_id}`，多余键 → `unknown_field`，
    `type` 非 `"bot"` → `invalid_type`（非 unknown_field）。
- `target_bot_id` / `target_owner_id` = apply 上下文 (`ctx.bot_id` / `ctx.owner_id`)，**不进 entry**。
- `scope` / `dry_run` **不进 entry，handler 内写死** `scope="exact"` /
  `dry_run=false`（rudyLi 锁，决策点 3、4）。

### 前向兼容（未来，v1 不实现只留缝）

- 未来增 `type: "item"` = 指定 workflow 添加（entry 携带 `workflow_id` / `workflow_ids`）。
  设计成「加 enum 值 + 可选字段」，已有 document 不破坏：`validate_task_nav_entry` 按
  `type` 分支取 closed-set；clone Port `clone(...)` 加可选 `workflow_ids` 参后向兼容。

## Handler 语义 = fire-and-log（rudyLi 锁；与 render_screen 的刻意偏离）

```
try:
    port.clone(
        source_bot_id, source_owner_id,
        target_bot_id=ctx.bot_id, target_owner_id=ctx.owner_id,
        scope="exact", dry_run=False,
    )
    logger.info("[task_nav] clone requested: src=%s/%s -> dst=%s/%s", ...)
except Exception as exc:
    logger.exception("[task_nav] clone request failed: ...")  # 不抛
return BotConfigKindOutcome(kind="task_nav", entries=((source_bot_id, "unchanged", None),))
```

- handler **不读 clone 响应、不映射 rows**，outcome 恒 `"unchanged"`，entry identity = `source_bot_id`。
- `try/except` 兜住**不抛**：clone 失败/超时只 `logger.exception`，不影响 apply 整体，
  report 里 task_nav 仍标 `unchanged`，不 abort 兄弟 kind。
- 这是 rudyLi 明确要的偏离（render_screen 失败标 `failed` + abort 同 category）。
  理由：task_nav 是跨服务 best-effort 的 UX 态复制，dst bot 已 provision，不应因下游
  5xx 中止整个 bot 复制。独立 review 建议 option (b)（catch 后返 `status=failed` 但不 raise，
  更贴 mirror、report 诚实），**当前维持完全静默锁**，option (b) 留档后续可调点。

## Clone Port 与 clawweb 域名（config-driven，参考 bcsfuse base_url 配置）

- 不 hardcode host。复用 agentclaw 既有的 `di/modules/http_client_module.py` HttpClient DI
  （= bcsfuse `<SVC>_BASE_URL` per-env overlay 模式在 agentclaw 的等价机制）：
  - `plugin_api/http_client.py` 加 `QUALIFIER_CLAWWEB`
  - `di/config.py` 加 `ClawwebConfig` 带 `base_url`（读 neutral config，deployment overlay 已选 host）
  - `di/modules/http_client_module.py` 加 `clawweb_http_client` provider（mirror
    `bcn_http_client` / `masa_agent_eval_http_client`），返
    `Annotated[HttpClient, QUALIFIER_CLAWWEB]`
  - 每 env yaml 填 `clawweb.base_url`：
    - `application-prod.yaml` → `https://clawweb.antgroup-inc.cn`
    - `application-prepub.yaml` → `https://clawweb-pre.antgroup-inc.cn`
    - `application-sim.yaml` / sim overlay → simulator 内 clawweb svc（NodePort 31282 / in-cluster svc）
    - `application-singlebox.yaml` → 留空 / mock（singlebox 不发 HTTP 或走 LocalHttpClient 兜底）
- Port 实现单条：inject `Annotated[HttpClient, QUALIFIER_CLAWWEB]`，POST path
  `/api/internal/bot-workflow-permissions/clone`（path 拼在 base_url 后），**不带 token / 头**
  （rudyLi 线上 curl 实测确认接口存在且不收 token，推翻路由 docstring 的 service-token 设计意图）。

### 满足 Rule 14/20/21 的方式
- env 切换落在 **HttpClient seam**（不写 Port 三实现）：`test`/`corp_test` profile 装
  `TestHttpClientModule` 自动换成 no-network `LocalHttpClient`（Rule 21 local）；
  `singlebox`/sim/pre/prod 消费真 `HttpxClient` 打各 env `base_url`（Rule 20 prod）。
- Port 自身只剩 **一个真实现** + Protocol seam；Noop 仅 unit test 局部 stub。
- Rule 14 config-driven wiring：无 scattered `if env` host 分支。

### 分层（贴 render_screen）
handler 注 `BotWorkflowPermissionsClonePort` Protocol → Port Impl 持
`HttpClient[CLAWWEB]` → POST clone。两层，与 render_screen「handler 注
`RenderScreenServiceProtocol`」一致。

## 关键差异：render_screen vs task_nav

| 维度 | render_screen | task_nav |
| --- | --- | --- |
| entry | verbatim 值行 (`name`/`cdn_url`) | 跨 bot 引用 (`type`+`source_bot_id`+`source_owner_id`) |
| copy 写法 | gated（probe `list_render_screens` 非空才 emit） | **always-emit**（不探查源有无） |
| 调用 | 本地同进程 `RenderScreenService.copy_render_screens`（agentclaw 自己 DB） | 外部 clawweb `/clone` HTTP（跨服务） |
| 幂等来源 | service 自带 overwrite converge | clawweb clone upsert + `already_covered` skip |
| 失败语义 | 标 `failed` + abort 同 category | 恒 `unchanged`，不 abort（fire-and-log） |
| 跨服务 | 否（同进程，无 auth 先例） | 是（task_nav 是本 seam 第一个跨服务 kind；no-auth 靠 rudyLi 线上 curl 实证） |

## AC

- **AC1 schema**：`PUT /config-manifest` 带 `bot_config.task_nav` 合法 entry
  （`type:"bot"` + `source_bot_id` + `source_owner_id`）→ 200；缺任一必填 → 422；
  多余字段（含 `scope`/`dry_run`/`target_*`/`workflow_id`）→ 422 `unknown_field`；
  `type` 非 `bot` → 422 `invalid_type`；未注册 kind → 422 `unknown_bot_config_kind`。
- **AC2 materialiser**：ON_CONTAINER apply 调 `TaskNavSyncHandler.sync_kind` 一次
  （target=ctx.bot_id/owner_id，scope=exact/dry_run=false 写死，不带 token），
  结果映射进 apply report（task_nav 标 `unchanged`）。
- **AC3 copy**：`POST /bots/copy` 对每个源 bot 恒产 task_nav entry（`type:"bot"`），
  source 取自 copy 请求的 `source_bot_id`+owner。
- **AC4 幂等**：同一 declared set 重复 apply 不报错、dst 任务导航状态不变
  （clawweb clone upsert + `already_covered` skip，无重复行）。

## 验证

- mirror `test_bot_config_render_screen*` 套件（schema / compiler / materialiser / copy_route）。
- 契约测试 `tests/contracts/test_bot_workflow_permissions_clone_port.py`（Rule 25）：
  用 `LocalHttpClient` 注入的 Port 实现跑，第一遍就跑。
- HttpClient seam 断言：不同 env overlay 下 `ClawwebConfig.base_url` 解析到对应 host
  （`_log_binding` 已在 boot 打 `HttpClient[clawweb]: HttpxClient(base_url=...)`）。
- simulator e2e：dev-login cookie 走 openapi 触发一次真实 copy → handler 真调 simulator
  clawweb clone → 断言 dst bot 任务导航就位（验 simulator clawweb `botPermRepo` 写入）。
  无副作用（simulator clawweb 是本地实例，非 pre/prod 真库）。
  - ⚠ 软项：simulator 跑 local Avernet clawweb source，若该 source 树未挂载 `/clone`
    （`createBotWorkflowPermissionCloneRouter` 在 public-bootstrap.ts 未见挂载，pre/prod 已实测在），
    首 simulator 跑拉不到该端点时，在 simulator clawweb bootstrap 补一行
    `app.use("/api/internal/bot-workflow-permissions/clone", createBotWorkflowPermissionCloneRouter({...}))`
    即可。**不进验收硬门**。

## 不碰

- `/auth-status` 同步建 bot race（基线 REL20261110）——本需求不碰 auth-status / create_job phase，
  仅扩 manifest kind。
- clawweb 服务自身代码（除非上述 simulator bootstrap 软项需要补一行挂载，pre/prod 不动）。

## 仓库 / 分支 / 基线

- 双仓：community 源在 Avernet (`/Users/fengdian/code/avernet/apps/backend/src/agentclaw/community`)，
  ocb 同步 mirror（见 memory `openapi-update-two-repos` / `avernet-code-edits-in-local-repo`）。
- 分支：Avernet `feat/bot-config-task-nav` off `dev`（HEAD `f894c9380`）；ocb `feat/bot-config-task-nav` sync。
- 基线含 REL20261110。

## 兼容 / 回滚

- 新 kind 纯增：未声明 `task_nav` 的 manifest document 行为零变化（kind absent = untouched）。
- handler 缺失 → `NoopBotConfigSyncHandler` 默认 refusal（registry 默认），不会静默成功。
- 回滚 = 删 kind 注册 + handler + Port + qualifier + config；document 中残留 `task_nav` key
  会被 validator 拒（未知 kind），无半态。

## 未决项

无。所有决策点（entry 字段 / scope+dry_run 写死 / always-emit / type 字段 / ON_CONTAINER /
幂等 / 鉴权 / fire-and-log / clawweb config-driven）均已锁，详见 Dima 决策评论链。
软项（不阻塞）：simulator clone 挂载、target_owner_id=ctx.owner_id 一个测试断言覆盖。
