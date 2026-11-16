# Tasks: bot_config 新增 task_nav kind

Dima: https://project.alipay.com/workItem?workItemId=2026111100120522034
Spec: [./spec.md](./spec.md) ｜ Plan: [./plan.md](./plan.md)

TDD 顺序：契约测试第一遍就跑（红），再落实现转绿。建议先 HttpClient qualifier+config+provider
骨架跑通 `_log_binding`，再补 handler/Port/schema/copy。所有改动落在 Avernet community 源
（`/Users/fengdian/code/avernet/apps/backend/src/agentclaw/community`），ocb submodule mirror。

## T1 — schema 子校验器（→ AC1）
- [ ] 新 `validate_task_nav_entry(ctx, location, entry)`：按 `type` 分支 closed-set 校验
  - v1 `type:"bot"`：闭合字段集 `{type, source_bot_id, source_owner_id}`，多余键 → `ctx.add unknown_field`
  - `type` 缺/非 `"bot"` → `invalid_type`
  - `source_bot_id` / `source_owner_id` 非空 str 检查
- [ ] 注册 `"task_nav"` 进 `BOT_CONFIG_KIND_VALIDATORS` dict（`bot_config_kinds.py:75`，紧挨 `render_screen`）
- [ ] 测：mirror `test_validate_render_screen_entry*`，断言合法 200 / 缺字段 422 / extra 422 unknown_field / type 非 bot 422 invalid_type
- **验证：** AC1（PUT /config-manifest 四种入参的 200/422 分类）

## T2 — HttpClient qualifier + config（→ AC2 env 路由 + Rule 20/21）
- [ ] `plugin_api/http_client.py` 加 `QUALIFIER_CLAWWEB`
- [ ] `di/config.py` 加 `ClawwebConfig`（带 `base_url`）
- [ ] `di/modules/http_client_module.py` 加 `clawweb_http_client` provider（mirror `bcn_http_client`）
- [ ] 每 env yaml 加 `clawweb.base_url`：prod→`https://clawweb.antgroup-inc.cn`、prepub→`https://clawweb-pre.antgroup-inc.cn`、sim→simulator clawweb svc、singlebox→留空/mock
- [ ] 测：boot `_log_binding` 打出 `HttpClient[clawweb]: HttpxClient(base_url=<对应 env host>)`；`test`/`corp_test` profile 自动是 `LocalHttpClient`
- **验证：** HttpClient seam 断言；Rule 20/21 在 seam 满足，不需独立 Port 三实现

## T3 — Clone Port + HTTP impl（→ AC2 调用契约 + Rule 25）
- [ ] **新** `core/bot_management/bot_workflow_permissions_clone_port.py`：`@runtime_checkable Protocol`，单方法 `clone(*, source_bot_id, source_owner_id, target_bot_id, target_owner_id, scope, dry_run)`，返回 `None`（handler 不读）
- [ ] **新** `core/bot_management/bot_workflow_permissions_clone_http_impl.py`：单真实现，`__init__(http: Annotated[HttpClient, QUALIFIER_CLAWWEB])`，`clone` = `http.post("/api/internal/bot-workflow-permissions/clone", json={...})`，不带 token/头
- [ ] 契约测试 `tests/contracts/test_bot_workflow_permissions_clone_port.py`（Rule 25）：用 `LocalHttpClient` 注入的 Port 实现跑，第一遍就跑（红→绿）；断言请求 body 为 `{source_bot_id, source_owner_id, target_bot_id, target_owner_id, scope:"exact", dry_run:false}`，无 token 头
- **验证：** AC2 调用契约；Rule 25 conformance green

## T4 — fire-and-log handler（→ AC2 + AC4）
- [ ] **新** `core/apply_engine/materialisers/bot_config_task_nav_handler.py`（mirror `bot_config_render_screen_handler.py` 结构）：
  - `kind = "task_nav"`
  - handler 注 `BotWorkflowPermissionsClonePort`
  - `sync_kind`：try `port.clone(target=ctx.bot_id/owner_id, scope="exact", dry_run=False)` + `logger.info("[task_nav] clone requested ...")`；except `logger.exception` 不抛；return `BotConfigKindOutcome(kind="task_nav", entries=((source_bot_id, "unchanged", None),))`
- [ ] DI 注册（`apply_engine_module.py:548` registry 加 `"task_nav": TaskNavSyncHandler(clone_port)`，Port impl 绑 `HttpClient[CLAWWEB]`）
- [ ] 测：mirror `test_bot_config_render_screen*_materialiser*`；断言 sync_kind 调 clone 一次、clone 抛异常时 outcome 仍 `unchanged` 且不 raise、不 abort 兄弟 kind
- **验证：** AC2（apply 调一次、report 标 unchanged）；AC4（重复 apply 不报错 — clone upsert + already_covered skip 在 clawweb 侧，handler 不加守卫）

## T5 — compiler/registry/desired_state 透传确认（无改动，仅验）
- [ ] 确认 `compiler/entries/bot_config.py:44`、`desired_state.py:465`、`bot_config_kind_registry.py` docstring 透传 claim（三个 cheap read）
- [ ] 测：compiler/desired_state 把 `task_nav` kind 原样透传给 handler（不改这三文件）
- **验证：** T1+T4 端到端 PUT→apply 路径无阻塞

## T6 — copy always-emit 序列化（→ AC3）
- [ ] `adapters/http/openapi_v1/bots/copy.py` `submit_copy`（`207-209` 旁、`copy_bot` `564-595` DI）：always-emit `manifest["bot_config"]["task_nav"] = [{"type":"bot", "source_bot_id": <req source>, "source_owner_id": <req owner>}]`（不 mirror render_screen 的 `list_render_screens` probe）
- [ ] 测：mirror `test_submit_copy_render_screen*`；断言对任意源 bot 产出的 manifest document **恒含** `task_nav` entry（哪怕源无任务导航）
- **验证：** AC3

## T7 — simulator e2e（→ AC1-AC4 真链路）
- [ ] dev-login cookie 走 gateway 31281 触发 `POST /bots/copy`（参考 memory `fe-nextgen-local-dev-preprod` / `simulator-openapi-cookie-curl`）
- [ ] handler 真调 simulator clawweb clone（NodePort 31282）
- [ ] 断言 dst bot 任务导航就位（simulator clawweb `botPermRepo` 写入）
- [ ] **软项**：若 simulator clawweb 拉不到 `/clone`（local source 未见 `createBotWorkflowPermissionCloneRouter` 挂载），在 simulator clawweb bootstrap 补一行 `app.use("/api/internal/bot-workflow-permissions/clone", createBotWorkflowPermissionCloneRouter({...botPermRepo, resolveBotOwner}))`；pre/prod 不受影响
- [ ] 重复 apply 两次，断言 dst 状态不变、report 不报错（AC4）
- **验证：** AC1-AC4 真链路；option 验证幂等

## T8 — ocb mirror + 提 PR
- [ ] ocb `feat/bot-config-task-nav` submodule mirror 同步 community 改动 + copy.py 改动（双仓，见 memory `openapi-update-two-repos`）
- [ ] Avernet PR + ocb PR 各一；commit/PR 尾加 Co-Authored-By Claude
- [ ] Dima 简短交付（PR 链 + 验证结论）
