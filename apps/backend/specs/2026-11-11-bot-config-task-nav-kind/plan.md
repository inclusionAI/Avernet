# Plan: bot_config 新增 task_nav kind

Dima: https://project.alipay.com/workItem?workItemId=2026111100120522034
（plan @ teamclaw-plan，Gate B 已过 2026-11-11；ready-for-agent）

Spec: [./spec.md](./spec.md) ｜ 任务拆解: [./tasks.md](./tasks.md)

## Approach

填实 bot_config seam 已留位的 `task_nav` kind：一个 schema 子校验器 + 一个
fire-and-log handler + 一个跨服务 Clone Port。kind 透传，compiler / desired_state /
materialiser 编排 / registry **均不改**（docstring 已声明）。env 切换不走 Port 三实现，
落 HttpClient seam（新增 `QUALIFIER_CLAWWEB` + per-env `base_url` config overlay，
照搬 `bcn_http_client` / `masa_agent_eval_http_client`）。

## Affected Components

- `core/bot_config_manifest/schema/bot_config_kinds.py` — 新 `validate_task_nav_entry`
  （`type` 分支 closed-set 校验）+ 注册 `"task_nav"` 进 `BOT_CONFIG_KIND_VALIDATORS` dict（:75）。
- `core/bot_management/bot_workflow_permissions_clone_port.py` — **新文件**
  `@runtime_checkable Protocol`（单方法 `clone(...)`，返回值可 `None`），mirror
  `render_screen_service_protocol.py` seam。
- `core/bot_management/bot_workflow_permissions_clone_http_impl.py`（或同 Protocol 文件内）
  — **新文件**：单一真实现，`__init__(self, http: Annotated[HttpClient, QUALIFIER_CLAWWEB])`，
  `clone(...)` = `http.post("/api/internal/bot-workflow-permissions/clone", json={...})`，
  不读响应、不带 token。
- `core/apply_engine/materialisers/bot_config_task_nav_handler.py` — **新文件**
  （mirror `bot_config_render_screen_handler.py`，但 fire-and-log：handler 注
  `BotWorkflowPermissionsClonePort`，调 `clone`，整体 try/except 兜住，outcome 恒 `unchanged`）。
- `core/plugin_api/http_client.py` — 加 `QUALIFIER_CLAWWEB` 常量。
- `di/config.py` — 加 `ClawwebConfig`（带 `base_url`）。
- `di/modules/http_client_module.py` — 加 `clawweb_http_client` provider（mirror
  `bcn_http_client`，三行：`base_url = claw.base_url` → `pool.for_qualifier(QUALIFIER_CLAWWEB)`
  → `_client(base_url, policy)`）。
- `di/modules/apply_engine_module.py` — `:548` registry factory 加
  `"task_nav": TaskNavSyncHandler(clone_port)`；绑 Port 实现 → `HttpClient[CLAWWEB]`。
- `adapters/http/openapi_v1/bots/copy.py` — `submit_copy` always-emit：
  在 `:207-209` 旁写 `manifest["bot_config"]["task_nav"] = [{"type":"bot",
  "source_bot_id": ..., "source_owner_id": ...}]`（不 mirror render_screen 的 probe-gated）。
- 每 env config yaml — 加 `clawweb.base_url`（prod/prepub/sim/singlebox 各一条，见 spec.md）。

### 不改
- `core/bot_config_manifest/compiler/entries/bot_config.py:44`（docstring 已声明 task_nav 无需改）
- `core/apply_engine/desired_state.py:465`（mapping 透传）
- `core/apply_engine/materialisers/bot_config.py`（按 kind dispatch，泛型）
- `core/apply_engine/materialisers/bot_config_kind_registry.py`（keyed by handler.kind，
  自动归位；`NoopBotConfigSyncHandler` 默认 refusal 兜底）

## 执行前提

- Avernet `feat/bot-config-task-nav` off `dev`（HEAD `f894c9380`）已建。
- 基线含 REL20261110（`/auth-status` sync-bot race）—— 本需求不碰 auth-status/create_job。
- ocb submodule 同步分支已建（mirror community 改动 + copy.py 改动）。
- 无 clawweb 服务端改动硬依赖（pre/prod `/clone` 已实测可用；simulator 软项按需补一行挂载）。

## 兼容 / 回滚

- 新 kind 纯增：未声明 `task_nav` 的 manifest document 行为零变化（kind absent = untouched）。
- handler 缺失 → `NoopBotConfigSyncHandler` 默认 refusal（registry 默认），不会静默成功。
- 回滚 = 删 kind 注册 + handler + Port + qualifier + config；document 残留 `task_nav` key
  会被 validator 拒（未知 kind），无半态。

## 关键决策（rudyLi 锁，详见 Dima 评论链）

1. entry 只 `{type, source_bot_id, source_owner_id}`；target=ctx；scope=exact/dry_run=false handler 内写死。
2. **always-emit**（不 probe 源有无任务导航）。
3. **`type` 字段**：v1 `type:"bot"`（整体克隆）；未来 `type:"item"`（指定 workflow）。
4. 阶段 = **ON_CONTAINER**（正文原写 PRE_CONTAINER 是笔误）。
5. 幂等 = 是（不加 converge 守卫）；鉴权 = 不要（rudyLi 线上 curl 实证）。
6. handler = **fire-and-log**（恒 `unchanged`，不抛不 abort）；option (b) 留档。
7. clawweb 域名 = **config-driven**（复用 HttpClient DI，不 hardcode host）。

## 风险

- **clawweb 跨服务 no-auth**：task_nav 是本 seam 第一个跨服务 kind，no-auth 不能靠
  render_screen parity（render_screen 同进程）。靠 rudyLi 线上 curl 实测撑。
  若 clawweb 日后加鉴权，只改 prod Port impl（注入 head/token），不动 handler/entry。
- **simulator `/clone` 软项**：local Avernet clawweb source 未见 clone router 挂载，
  首 sim e2e 若拉不到端点需补一行 bootstrap（pre/prod 不受影响）。
