# Bot 工坊 OpenAPI 全量清单（TC 改版）

- **日期**: 2026-08-12
- **整理人**: lucas（A 线 · 个人/本地 Bot + 壳层 + 诊断/协作，契约接口人）
- **权威来源**:
  1. 语雀系分《Bot工坊 改版 — 系分方案(OpenAPI 驱动版)》2026-08-07 + §10 订正 2026-08-11（`yuque.antfin.com/securitytec/otbct4/dsame52bmg6mggwq`）
  2. 本机技术设计 `docs/superpowers/specs/2026-08-11-bot-inventory-personal-local-tech-design.md`
  3. PRD/DEMO `TC改版方案-0807.html`
  4. 代码现状核对：`src/backend/src/agentclaw/community/adapters/http/openapi_v1/` 全部 router 源码 + `__init__.py` 注册顺序
- **本文性质**: 工坊改版涉及的全部 OpenAPI 模块与端点的统一清单（含已有、待升级、待创建）。

---

## 0. 约定与图例

### 0.1 寻址约定（系分 §10.2 + `openapi_v1/__init__.py`）
- 下表「全路径」列含完整前缀 `/openapi/v1/bots`，路径参数用 `{...}` 表示（`{bot_id}` / `{session_id}` / `{resource_id}` / `{routine_id}` / `{skill_id}` / `{file_type}` / `{model_id}` / `{machine_id}` / `{instance_id}` / `{member_id}` / `{set_id}` / `{server_code}` / `{trace_id}` / `{group_id}` / `{biz_scene}` / `{biz_task_id}` / `{session_key}`）。
- **组件字面量在前、`{bot_id}` 在后**：如 `/openapi/v1/bots/local/{bot_id}`、`/openapi/v1/bots/diagnostics/{bot_id}/runtime-logs`，不允许 `/{bot_id}/xxx` 形态（`bots` 组件自身是唯一例外，它拥有根 `/openapi/v1/bots/{bot_id}` 及其子资源 `/openapi/v1/bots/{bot_id}/restart` 等）。
- literal 子组件必须挂在 `bots` 通配 `/openapi/v1/bots/{bot_id}` 之前（`_SUBGROUPS` 先于 `bots_router`）。

### 0.2 鉴权与作用域约定
- 用户维度统一 `?user_id=`（`UserIdDep`），必须与已验证 principal 用户一致，403 由公共面统一处理。
- 空间维度按接口类型区分（系分 §10.3/§10.4）：
  - **工坊卡片列表**：`GET /bots/all` 读取 `X-Space-Id`，由 `BusinessSpaceContextProtocol` 解析当前空间并过滤；不使用 `?space=`。
  - **创建类**：body 使用 `space_id`；当前 #1 已接收并写入，基础 `Bot` 响应不回显富空间对象。
  - **后续 space-sensitive per-bot 类**：不增加 space query，按设计通过成员关系横切校验；具体依赖随 prod business-space adapter 落地。
  - 市场/目录类：豁免。
- BCN 边界：工坊只做编辑授权（空间成员）+ 唯一管理员；可见范围/协作权限归 BCN。

### 0.3 三条刚性口径
1. 工坊交互全走 `/openapi/v1` 新接口，不再用老 `/api`。
2. 老接口（内部 `/api` + 现存公开路径）尽量纯加法，特殊情况例外。
3. 新端点委托现有内部 service，不重塑存量 required 字段（`Bot.status` / `bot_type` / `cluster_name`）。

### 0.4 列说明
| 列 | 取值 |
|---|---|
| **归类** | `已有` = 现网存在、工坊直接复用；`升级` = 现网存在、需纯加法增强（不动 required）；`新增` = 现网不存在、新建端点 |
| **完成状态** | `已完成` = 存量原有端点，现网在跑（非本次迭代产出）；`已开发` = 本次本地迭代新建并已注册 router；`待升级` = 现网在但加法未做；`已设计未建` = 本机技术设计已定形但未写 router；`未开工` = 仅系分有端点计划 |
| **阶段** | 系分 §7 排期：P0(8/07–8/15) / P1(8/15–8/22) / P2(8/22–8/29) / P3(8/29–8/31)；`—` = 现网已上线、无排期 |

### 0.5 命名已决定（2026-08-12）
- 系分 §3-A/§10.2 原称 `/openapi/v1/bots/workshop`（list）+ `/workshop/{bot_id}`（card）+ `/workshop/{bot_id}/actions`，作跨 personal+local+service 富卡片面。
- **已决定**采用 `/openapi/v1/bots/all`（更符合领域模型），不再使用 `/workshop`。当前已实现范围 = 个人云端 + 本地 Bot，**尚不返回 service Bot**；`ServiceLifecyclePort` 目前只预留展示态派生能力，不等于 service 数据源已接入。完整工坊是否由 B 线扩展 `/all` 为统一 read model，或由前端合并独立 service 列表，仍需在 B 线契约落地前明确。
- 溯源：本决定由契约接口人（lucas）2026-08-12 给出，覆盖系分。系分语雀文档侧尚未同步改名；后续更新系分时统一改成 `/all`。
- 影响 #67–69；其「委托/备注」列保留「系分原称 `/workshop`」仅作交叉溯源，不代表待决。

---

## 1. 全量端点清单

| # | 模块 | 接口功能 | 全路径（Method 在首） | 归类 | 完成状态 | 阶段 | 委托 / 备注 |
|---|---|---|---|---|---|---|---|
| 1 | bots | 创建 Bot（personal\|service，拒 desktop） | `POST /openapi/v1/bots` | 升级 | 已开发 | P0 | **`space_id` 入参已落地**(可选可空)：`BotCreate.space_id` → `BotCreateSpec.space_id` → `BotService.create_bot(space_id=)` → `bot_data["space_id"]` → `BotRepository.insert BotModel(space_id=)`；异步 202 路径经 `get_bot_auth_status?space_id=` echo 透传 → `complete_bot_authorization spec.space_id`。`init_config` 仍不加(§5 决议"跟老 /api data-init 独立端点") |
| 2 | bots | 列表 | `GET /openapi/v1/bots` | 已有 | 已完成 | — | 纯 legacy:`keyword`/`engine`/`status` 直传 `BotService`;工坊富卡片列表走 `GET /bots/all`。曾加 `deploy_mode`/`service`/`space` 三筛选器(ABC 清理已回退,走 `/all` 替代) |
| 3 | bots | 重名校验 | `GET /openapi/v1/bots/check-name` | 已有 | 已完成 | — | 租户级，无 `user_id` |
| 4 | bots | 配额上限 | `GET /openapi/v1/bots/ceiling` | 已有 | 已完成 | — | — |
| 5 | bots | Bot 详情 | `GET /openapi/v1/bots/{bot_id}` | 已有 | 已完成 | — | 见 #14「Bot 响应体」行 |
| 6 | bots | 改名 / 描述 | `PUT /openapi/v1/bots/{bot_id}` | 已有 | 已完成 | — | engine 不可改 |
| 7 | bots | 删除 Bot | `DELETE /openapi/v1/bots/{bot_id}` | 已有 | 已完成 | — | **拒 desktop + service** |
| 8 | bots | 重启进程 | `POST /openapi/v1/bots/{bot_id}/restart` | 已有 | 已完成 | — | **拒 desktop**；≠重启引擎 |
| 9 | bots | 授权轮询 / 完成创建 | `GET /openapi/v1/bots/{bot_id}/auth-status` | 已有 | 已完成 | — | 202 流在此 ISSUED 时真正落库 |
| 10 | bots | 就绪布尔 | `GET /openapi/v1/bots/{bot_id}/status` | 已有 | 已完成 | — | **≠健康分** |
| 11 | bots | Agent Passport / 许可证 | `GET /openapi/v1/bots/{bot_id}/passport` | 升级 | 已开发 | P0 | 加 `expire_at`/`certificate_url` 两可选可空字段;从 `info.get()` 透传,与老 `/api` passport 直返 plugin dict 等价;`passport_id` 仍是存在性信号,license 字段仅展示。两 Passport 实现当前返 None,字段契约先行,数据上游补值后自动透传 |
| 12 | bots | 读引擎配置 | `GET /openapi/v1/bots/{bot_id}/engine-config` | 已有 | 已完成 | — | — |
| 13 | bots | 写引擎配置 | `PUT /openapi/v1/bots/{bot_id}/engine-config` | 已有 | 已完成 | — | — |
| 14 | bots | _（schema）Bot 响应体_ | 影响 #1/#2/#5/#6/#8 等所有返回 `Bot` 的端点 | 已有 | 已完成 | — | **Bot 回归 8 基础字段**(`bot_id`/`bot_name`/`bot_desc`/`engine`/`cluster_name`/`bot_type`/`status`/`owner_entity_id`)。曾加 `deploy_mode`+`space`+`BotSpaceRef`(ABC 清理已删,富字段归 `BotInventoryItem` 在 `/all`)。`ac_bots.space_id` 列仍在(`plugin_api/models.py:87`),`create_bot` `space_id` 入参仍在(#1),但 Bot 响应不派生富字段——`/all` 的 `BotInventoryItem` 独立承担 |
| 15 | engine | 引擎运行态 | `GET /openapi/v1/bots/engine/{bot_id}/status` | 已有 | 已完成 | — | engine_runtime/engine |
| 16 | engine | 引擎能力 | `GET /openapi/v1/bots/engine/{bot_id}/capabilities` | 已有 | 已完成 | — | — |
| 17 | engine | 可用引擎列表 | `GET /openapi/v1/bots/engine/{bot_id}/available` | 已有 | 已完成 | — | 创建表单引擎候选 |
| 18 | sessions | 会话列表 | `GET /openapi/v1/bots/sessions/{bot_id}` | 已有 | 已完成 | — | 编辑页调试对话 |
| 19 | sessions | 新建会话 | `POST /openapi/v1/bots/sessions/{bot_id}` | 已有 | 已完成 | — | — |
| 20 | sessions | 会话详情 | `GET /openapi/v1/bots/sessions/{bot_id}/{session_id}` | 已有 | 已完成 | — | — |
| 21 | sessions | 更新会话 | `PATCH /openapi/v1/bots/sessions/{bot_id}/{session_id}` | 已有 | 已完成 | — | — |
| 22 | sessions | 删除会话 | `DELETE /openapi/v1/bots/sessions/{bot_id}/{session_id}` | 已有 | 已完成 | — | — |
| 23 | sessions | 消息列表 | `GET /openapi/v1/bots/sessions/{bot_id}/{session_id}/messages` | 已有 | 已完成 | — | — |
| 24 | sessions | 清空消息 | `DELETE /openapi/v1/bots/sessions/{bot_id}/{session_id}/messages` | 已有 | 已完成 | — | — |
| 25 | routines | 定时任务列表 | `GET /openapi/v1/bots/routines` | 已有 | 已完成 | — | 工作 Tab「定时任务」 |
| 26 | routines | 新建任务 | `POST /openapi/v1/bots/routines` | 已有 | 已完成 | — | — |
| 27 | routines | 任务详情 | `GET /openapi/v1/bots/routines/{routine_id}` | 已有 | 已完成 | — | — |
| 28 | routines | 更新任务 | `PATCH /openapi/v1/bots/routines/{routine_id}` | 已有 | 已完成 | — | — |
| 29 | routines | 删除任务 | `DELETE /openapi/v1/bots/routines/{routine_id}` | 已有 | 已完成 | — | — |
| 30 | routines | 手动触发 | `POST /openapi/v1/bots/routines/{routine_id}/run` | 已有 | 已完成 | — | — |
| 31 | routines | 执行历史 | `GET /openapi/v1/bots/routines/{routine_id}/runs` | 已有 | 已完成 | — | — |
| 32 | models | 模型列表 | `GET /openapi/v1/bots/models/{bot_id}` | 已有 | 已完成 | — | — |
| 33 | models | 模型详情 | `GET /openapi/v1/bots/models/{bot_id}/{model_id}` | 已有 | 已完成 | — | `{model_id}` 实为 path 参数 |
| 34 | identity | MD / 身份文件列表 | `GET /openapi/v1/bots/identity/{bot_id}` | 已有 | 已完成 | — | 「MD 管理」入口 |
| 35 | identity | 读单个 MD | `GET /openapi/v1/bots/identity/{bot_id}/{file_type}` | 升级 | 待升级 | P2 | `file_type` 是否覆盖 13 个 MD 待核；未覆盖加法补枚举 |
| 36 | identity | 写单个 MD | `PUT /openapi/v1/bots/identity/{bot_id}/{file_type}` | 升级 | 待升级 | P2 | 同上 |
| 37 | bot_logs | 对话 trace 检索 | `GET /openapi/v1/bots/logs/traces` | 已有 | 已完成 | — | **≠运行日志抽屉**；`user_id` 此处是「被查人」非「调用者」 |
| 38 | bot_logs | trace 详情 | `GET /openapi/v1/bots/logs/traces/{trace_id}` | 已有 | 已完成 | — | — |
| 39 | bot_logs | 会话 trace | `GET /openapi/v1/bots/logs/sessions/{session_key}/traces` | 已有 | 已完成 | — | — |
| 40 | bot_logs | 群 trace | `GET /openapi/v1/bots/logs/groups/{group_id}/traces` | 已有 | 已完成 | — | — |
| 41 | bot_logs | 任务 trace | `GET /openapi/v1/bots/logs/tasks/{biz_scene}/{biz_task_id}/traces` | 已有 | 已完成 | — | — |
| 42 | resources | 资源列表 | `GET /openapi/v1/bots/resources` | 已有 | 已完成 | — | **≠容器文件目录树** |
| 43 | resources | 资源重名校验 | `GET /openapi/v1/bots/resources/check-name` | 已有 | 已完成 | — | — |
| 44 | resources | 新建资源 | `POST /openapi/v1/bots/resources` | 已有 | 已完成 | — | — |
| 45 | resources | 上传资源 | `POST /openapi/v1/bots/resources/upload` | 已有 | 已完成 | — | — |
| 46 | resources | 资源详情 | `GET /openapi/v1/bots/resources/{resource_id}` | 已有 | 已完成 | — | — |
| 47 | resources | 更新资源 | `PUT /openapi/v1/bots/resources/{resource_id}` | 已有 | 已完成 | — | — |
| 48 | resources | 删除资源 | `DELETE /openapi/v1/bots/resources/{resource_id}` | 已有 | 已完成 | — | — |
| 49 | resources | 下载 | `GET /openapi/v1/bots/resources/{resource_id}/download` | 已有 | 已完成 | — | — |
| 50 | resources | 预览 | `GET /openapi/v1/bots/resources/{resource_id}/preview` | 已有 | 已完成 | — | — |
| 51 | approvals | 审批模式 | `GET /openapi/v1/bots/approvals/{bot_id}/mode` | 已有 | 已完成 | — | 关联「发布配置」 |
| 52 | approvals | 设置审批模式 | `PUT /openapi/v1/bots/approvals/{bot_id}/mode` | 已有 | 已完成 | — | — |
| 53 | approvals | 可用模式列表 | `GET /openapi/v1/bots/approvals/{bot_id}/modes` | 已有 | 已完成 | — | — |
| 54 | connection | 引擎连接诊断 | `GET /openapi/v1/bots/connection/{bot_id}` | 已有 | 已完成 | — | — |
| 55 | skills | 本地 Skill 列表 | `GET /openapi/v1/bots/skills` | 已有 | 已完成 | — | 仅本地 Skill |
| 56 | skills | Skill 详情 | `GET /openapi/v1/bots/skills/{skill_id}` | 已有 | 已完成 | — | — |
| 57 | skills | 上传本地 Skill | `POST /openapi/v1/bots/skills/upload` | 已有 | 已完成 | — | — |
| 58 | skills | 激活 Skill | `POST /openapi/v1/bots/skills/{skill_id}/activate` | 已有 | 已完成 | — | — |
| 59 | skills | 停用 Skill | `POST /openapi/v1/bots/skills/{skill_id}/deactivate` | 已有 | 已完成 | — | — |
| 60 | skills | 删除 Skill | `DELETE /openapi/v1/bots/skills/{skill_id}` | 已有 | 已完成 | — | 引用型 Skill（市场/工坊）在 #99 `skill-sets` |
| 61 | mcp | MCP 服务目录 | `GET /openapi/v1/bots/mcp/servers` | 已有 | 已完成 | — | 仅服务级目录 |
| 62 | mcp | 租户列表 | `GET /openapi/v1/bots/mcp/tenants` | 已有 | 已完成 | — | — |
| 63 | mcp | 服务详情 | `GET /openapi/v1/bots/mcp/servers/{server_code}` | 已有 | 已完成 | — | — |
| 64 | mcp | 权限 | `GET /openapi/v1/bots/mcp/servers/{server_code}/permissions` | 已有 | 已完成 | — | — |
| 65 | mcp | 读配置 | `GET /openapi/v1/bots/mcp/servers/{server_code}/config` | 已有 | 已完成 | — | — |
| 66 | mcp | 写配置 | `PUT /openapi/v1/bots/mcp/servers/{server_code}/config` | 已有 | 已完成 | — | per-bot 绑定 + caller 在 #99 `skill-sets/mcps` |
| 67 | bots(all) | 个人云端 + 本地卡片列表 | `GET /openapi/v1/bots/all` | 新增 | 已开发 | P0 | **并回 bots/router.py**(A 方案,§5)，`/all` literal 块(声明早于 `/{bot_id}`)。委托 `BotInventoryServiceProtocol` 聚合 personal cloud+local；当前不含 service。现有 card 字段覆盖基础信息、`kind/deploy_mode/display_state/space/passport/actions`；`health/version/deploy_stage/container_summary/lock_info/last_active_at/current_user_role` 等如需首页展示，须在对应 B 线/后续能力落地时明确是聚合进 `/all` 还是详情按需获取，避免前端 N+1。系分原称 `/workshop`；曾用名 `/inventory` |
| ~~68~~ | ~~bots(all)~~ | ~~单卡片~~ | ~~`GET /openapi/v1/bots/all/{bot_id}`~~ | ~~新增~~ | **已删除** | ~~P0~~ | **已删**：前端从 `GET /bots/all` 当前页结果取卡片；`GET /bots/{bot_id}` 只提供基础 Bot，不能替代富卡片刷新。所有会改变 `display_state/actions/disabled_actions` 的 mutation 成功后，前端必须重新请求当前页 `/bots/all`；若详情页没有当前页上下文，则回到列表或显式刷新列表。若后续该策略造成明显额外请求，再恢复富单卡 endpoint |
| ~~69~~ | ~~bots~~ | ~~可用动作集~~ | ~~`GET /openapi/v1/bots/{bot_id}/actions`~~ | ~~新增~~ | **已删除** | ~~P0~~ | **已删**:`actions`/`disabled_actions`/`display_state` 已在 `BotInventoryItem` 里返回;前端用 `GET /bots/all` 列表结果里的 `actions` 字段直接渲染按钮,无需单独调 |
| 70 | local | 设备列表 | `GET /openapi/v1/bots/local/devices` | 新增 | 已开发 | P0 | 创建选 machine；`DesktopBotServiceProtocol.list_devices` |
| 71 | local | 设备目录树 | `GET /openapi/v1/bots/local/devices/{machine_id}/files` | 新增 | 已开发 | P0 | 选挂载目录；`list_directory` |
| 72 | local | 创建本地 Bot（201 / 202） | `POST /openapi/v1/bots/local` | 新增 | 已开发 | P0 | 委托 `DesktopBotService`；含 `machine_id`/`mount_path`/`init_config` |
| 73 | local | 本地 Bot 列表 | `GET /openapi/v1/bots/local` | 新增 | 已开发 | P0 | inventory 已覆盖，可选 |
| 74 | local | 本地 Bot 详情 | `GET /openapi/v1/bots/local/{bot_id}` | 新增 | 已开发 | P0 | — |
| 75 | local | 授权轮询 + 完成创建 | `GET /openapi/v1/bots/local/{bot_id}/auth-status` | 新增 | 已开发 | P0 | `create_after_authorization` |
| 76 | local | 重启本地 Bot | `POST /openapi/v1/bots/local/{bot_id}/restart` | 新增 | 已开发 | P0 | 委托 desktop restart |
| 77 | local | 删除本地 Bot | `DELETE /openapi/v1/bots/local/{bot_id}` | 新增 | 已开发 | P0 | 委托 desktop delete |
| 78 | local | 打开目录 | `POST /openapi/v1/bots/local/{bot_id}/open-folder` | 新增 | 已开发 | P0 | `open_folder` |
| 79 | diagnostics | 运行日志流 | `GET /openapi/v1/bots/diagnostics/{bot_id}/runtime-logs` | 新增 | 已设计未建 | P0 | ≠`/logs` trace；BaaS 白名单路径，`tail`/`level` 限制 |
| 80 | engine | 重启引擎 | `POST /openapi/v1/bots/engine/{bot_id}/restart` | 新增 | 已开发 | P0 | **走 `engine_runtime/engine` 既有 relay 范式**(非新建 diagnostics)：复用 `EngineRuntimeRelayProtocol.call(method="POST", path="/api/engine/restart")` 转发到设备 adapter daemon (`<binding>:20003/api/engine/restart`,老前端 `agentclawproxy` 直调的那个),零新 core adapter。**≠** `POST /openapi/v1/bots/{bot_id}/restart`(#8 BaaS restart_bot,re-provision container、断 session);引擎重启只重启 engine 进程、不重置容器/session。router docstring 已订正"故意不暴露 restart"的过时论据 |
| 81 | diagnostics | 健康分 + 等级 | `GET /openapi/v1/bots/diagnostics/{bot_id}/health` | 新增 | 已设计未建 | P2 | 聚合 harness；仅 oc + 云端 |
| 82 | diagnostics | 触发健康检查 | `POST /openapi/v1/bots/diagnostics/{bot_id}/health-check` | 新增 | 已设计未建 | P2 | 仅 oc + 云端，policy 拦 |
| 83 | bots | 激活沉寂 Bot | `POST /openapi/v1/bots/{bot_id}/activate` | 新增 | 已开发 | P0 | **并回 bots/router.py**(A 方案,§5)，`/{bot_id}` 子资源(像 `/restart`)。helper `_require_personal_cloud_bot` + 委托 `BotDormantActivateServiceProtocol.activate`(`ActivateBotService`)。`bot_type==personal`+cloud 裁决(desktop/service→409) + owner guard(`get_bot`→404);`InvalidBotStateError`→409。30 天·仅非服务·本地豁免·蒙层非状态 |
| 84 | bots | 初始化配置 | `POST /openapi/v1/bots/{bot_id}/data-init` | 新增 | 已开发 | P0 | 已委托 `DataInitServiceProtocol.trigger_init`（async, fire-and-forget），仅 personal+cloud（desktop/service→409）。**当前仍有联调阻塞，不能视为 legacy 1:1**：OpenAPI handler 尚未像老 `/api` 一样把 Cookie `IAM_TOKEN` 写入 `bot.ext`；同时公开 `Bot` 与 `BotInventoryItem` 均不返回 `ext.data_init_status`，前端没有可执行的轮询契约。联调前须补齐凭证传递，并确定独立 status endpoint（优先）或受控状态字段；创建 checkbox 仍由前端在 Bot 真正存在后单独触发本端点 |
| 85 | lifecycle | 开启服务化（personal→service） | `POST /openapi/v1/bots/lifecycle/{bot_id}/upgrade` | 新增 | 未开工 | P1 | 委托 `upgrade_bot_type`；改 service 去反向，不动契约 |
| 86 | lifecycle | 发布态 / 版本 / 阶段 | `GET /openapi/v1/bots/lifecycle/{bot_id}` | 新增 | 未开工 | P1 | 委托 `publish_flow_service` |
| 87 | lifecycle | 草稿→预发 / 预发→上线 | `POST /openapi/v1/bots/lifecycle/{bot_id}/advance` | 新增 | 未开工 | P1 | body `{stage: staging\|online}` |
| 88 | lifecycle | 下线 | `POST /openapi/v1/bots/lifecycle/{bot_id}/offline` | 新增 | 未开工 | P1 | 不可逆 |
| 89 | lifecycle | 重启发布 | `POST /openapi/v1/bots/lifecycle/{bot_id}/restart` | 新增 | 未开工 | P1 | — |
| 90 | lifecycle | 发布 / 下线审批开关 | `GET /PUT /openapi/v1/bots/lifecycle/{bot_id}/approval` | 新增 | 未开工 | P2 | 系分原称 `/publish-approval`；关联 approvals |
| 91 | containers | 实例列表 | `GET /openapi/v1/bots/containers/{bot_id}` | 新增 | 未开工 | P0 | `summary{total,healthy,abnormal}` + `instances[id,node,status]`；cpu/mem 留空待 BaaS |
| 92 | containers | 单实例重启 | `POST /openapi/v1/bots/containers/{bot_id}/{instance_id}/restart` | 新增 | 未开工 | P0 | 仅异常态 |
| 93 | containers | 单实例日志 | `GET /openapi/v1/bots/containers/{bot_id}/{instance_id}/logs` | 新增 | 未开工 | P0 | — |
| 94 | evaluation | 创建评测任务 | `POST /openapi/v1/bots/evaluation/{bot_id}` | 新增 | 未开工 | P1 | 返回评测页 URL/token；仅服务预发/运行态；委托 `quality` |
| 95 | edit-lock | 获取 / 抢占编辑锁 | `POST /openapi/v1/bots/edit-lock/{bot_id}`(+`/steal`) | 新增 | 已设计未建 | P1 | **归 B 线/joseph(2026-08-12 lucas移交,见 §5)**：`CollaboratorLockService.acquire/steal` 的下游协作本质属服务 bot 链路(`service_bot/router_publish.py` 15 处 + `bot_collaborator` `BotNotServiceTypeError`);个人云端 Bot 无协作者(`can_manage_collaborators=False`),本期 P0 不用锁。A 线不做;B 线做公开契约+内部改造 |
| 96 | edit-lock | 释放编辑锁 | `DELETE /openapi/v1/bots/edit-lock/{bot_id}` | 新增 | 已设计未建 | P1 | 归 B 线(同 #95),`CollaboratorLockService.release_lock` |
| 97 | edit-lock | 编辑锁信息 | `GET /openapi/v1/bots/edit-lock/{bot_id}` | 新增 | 已设计未建 | P1 | 归 B 线(同 #95),`CollaboratorLockService.get_lock_info`(系分原称 `/lock/info`) |
| 98 | editors | 协作者管理 | `GET/POST/PATCH/DELETE /openapi/v1/bots/editors/{bot_id}`(+`/{member_id}`) | 新增 | 已设计未建 | P1 | **归 B 线/joseph**:协作本质属服务 bot(`CollaboratorService` 的 `BotNotServiceTypeError` 对非 service 默认拒);"空间成员先验集合 + 唯一管理员"是 service 改造(碰红线,`collaborator_service` 内部逻辑)。A 线不做 |
| 99 | skill-sets | 能力集分组 + 引用型 Skill + per-bot MCP | `GET/POST/PUT/DELETE /openapi/v1/bots/skill-sets/{bot_id}`(+`/{set_id}/skills`、`/mcps`) | 新增 | 未开工 | P2 | 引用型 Skill（市场/工坊，引用后只读）+ per-bot MCP 绑定（带 caller 字段） |
| 100 | files | 容器目录树 | `GET /openapi/v1/bots/files/{bot_id}` | 新增 | 未开工 | P2 | 委托 `service_bot/router_build.py:read-only/tree`；本地 Bot 只读；≠`/resources` |
| 101 | flow | 任务护航 DAG/YAML 编排 | `GET/PUT /openapi/v1/bots/flow/{bot_id}` | 新增 | 未开工 | P2 | 引擎 = BCS State Machine；P2 前需 BCS owner 进 openapi 或允许直调 |
| 102 | flow | 工作流执行历史 | `GET /openapi/v1/bots/flow/{bot_id}/runs` | 新增 | 未开工 | P2 | = DEMO §0.4 日志分析 |
| 103 | channels | 钉钉机器人配置 CRUD | `GET/POST/PUT/DELETE /openapi/v1/bots/channels/{bot_id}` | 新增 | 未开工 | P2 | 现有 `channels/` 目录零路由 |
| 104 | nodes | 节点 | `GET /openapi/v1/bots/nodes/{bot_id}` | 新增 | 未开工 | P2 | 委托 engine `/api/nodes`；字段待产品 |
| 105 | render-screens | 副屏 | `GET /openapi/v1/bots/render-screens/{bot_id}` | 新增 | 未开工 | P2 | 委托 `/api/bot-render-screens`；字段待产品 |
| 106 | spaces | 当前用户空间列表（切换器） | `GET /openapi/v1/spaces` | 新增 | 未开工 | P3 | `SpaceScopeProtocol` prod；工坊只 own list + 迁移；**唯一不带 `/bots` 中段** |
| 107 | migrate | 个人↔团队迁移 | `POST /openapi/v1/bots/migrate/{bot_id}` | 新增 | 未开工 | P3 | body `{target_space}`；校验编辑者是否目标空间成员，非成员移除 |

> 不归工坊：`authorized_apps`、`loadtest`、空间实体 CRUD（建团队/改成员/角色，归管理后台 owner，工坊经 `SpaceScopeProtocol` 消费）、通知/工单（归管理后台）。

---

## 2. 统计汇总

| 维度 | 数量 | 说明 |
|---|---|---|
| **表格条目数** | 107 | #1–#107；包含 #14 schema 和已删除的 #68/#69 |
| **有效条目数** | 105 | 排除已删除 #68/#69；仍包含 #14 schema |
| **有效 endpoint rows** | 104 | 有效条目再排除 #14 schema；多 Method/子路径分组仍按一行计，本文不混算 HTTP operation 数 |
| 按归类 · 已有 | 62 | 包含 #14 schema；若只算 endpoint rows 则为 61 |
| 按归类 · 升级 | 4 | #1 create、#11 passport、#35/#36 identity |
| 按归类 · 新增 | 39 | #67 + #70–107；已删除 #68/#69 不计 |
| 按完成 · 已完成 | 62 | 存量 60 + #2/#14 ABC 回退；包含 #14 schema |
| 按完成 · 已开发 | 15 | `/all` 1(#67) + local 9(#70–78) + dormant 1(#83) + data-init trigger 1(#84，端到端契约仍待补) + passport 1(#11) + engine restart 1(#80) + create space_id 1(#1) |
| 按完成 · 已删除 | 2 | ~~#68~~ 单卡片 + ~~#69~~ actions |
| 按完成 · 待升级 | 2 | #35/#36 identity `file_type` 13 MD 待核 P2 |
| 按完成 · 已设计未建 | 7 | diagnostics 3(#79/#81/#82) + edit-lock 3(B线) + editors 1(B线) |
| 按完成 · 未开工 | 19 | lifecycle/containers/evaluation/skill-sets/files/flow/channels/nodes/render-screens/spaces/migrate 等 |
| 按阶段 · P0 | 19 | #1/#11/#67/#70–80/#83–84/#91–93；已删除 #68/#69 不计 |
| 按阶段 · P1 | 10 | #85–89/#94–98 |
| 按阶段 · P2 | 12 | #35–36/#81–82/#90/#99–105 |
| 按阶段 · P3 | 2 | #106–107 |

**A/B 线归属口径（2026-08-13 修订）**：
- **A 线（lucas）** = 个人云端 Bot + 本地 Bot + 壳层(`/all`) + 跨型诊断 + 空间消费。**已开发 15 项**（ABC 回退后 #2/#14 为存量已完成，#68/#69 已删）；待办 = diagnostics 3 项(#79/#81/#82) + identity `file_type` 13 MD 待核 2 项(P2)。#84 trigger 虽已开发，但 IAM 凭证与状态查询契约仍是联调阻塞。
- **B 线（joseph）** = service Bot 生命周期 + 容器/评测 + 编辑页内核(skill-sets/files/flow/channels/nodes/render-screens) + 空间/迁移 + 协作(edit-lock/editors #95–98)。B 线还需明确 service Bot 如何进入工坊首页：优先扩展 `/bots/all` 为统一 read model；若选择独立 service 列表，则必须给出前端合并、分页、筛选与排序契约。

---

## 3. 易踩坑对照（系分 §2.4）

| 易混淆（全路径） | 正确认知 | 对应新端点（全路径） |
|---|---|---|
| `GET /openapi/v1/bots/{bot_id}/status` | 是就绪**布尔**（PENDING/ACTIVE/FAILED），**非健康分** | `GET /openapi/v1/bots/diagnostics/{bot_id}/health` |
| `POST /openapi/v1/bots/{bot_id}/restart` | 重启**进程**且拒 desktop | `POST /openapi/v1/bots/engine/{bot_id}/restart`（≠switch-engine） |
| `GET /openapi/v1/bots/logs/*` | 对话 **trace** | `GET /openapi/v1/bots/diagnostics/{bot_id}/runtime-logs`（运行日志抽屉） |
| `GET /openapi/v1/bots/resources/*` | **资源库** CRUD | `GET /openapi/v1/bots/files/{bot_id}`（容器目录树） |
| `GET /openapi/v1/bots/identity/{bot_id}/{file_type}` | `file_type` 覆盖 13 个 MD **待核** | 加法补枚举（同路径） |
| `DELETE /openapi/v1/bots/{bot_id}` | **拒 desktop + service** | 本地走 `DELETE /openapi/v1/bots/local/{bot_id}`；服务走 `POST /openapi/v1/bots/lifecycle/{bot_id}/offline` |

---

## 4. P2 闸门（外部确认，不阻塞 P0/P1）

- **任务护航 flow**（#101/#102）：引擎 = BCS State Machine（owner = BCS），P2 开工前需 BCS 把 `/state-machine-runs/*` 加进 openapi 或允许工坊直调内部（系分 §10.11-1）。

## 5. 已关闭决议（系分 §10.11，落 `combo_policy`）

- 引擎枚举按代码 + `teclaw`：公开面 engine = `moltis/openclaw/hermes/aicoding/claude_code/teclaw`（6 种）；`SUPPORTED_ENGINE_TYPES` 常量已补 `teclaw`（本 PR）。
- 兼容矩阵：健康检查仅 `openclaw`；服务引擎 & 开启服务化 = `openclaw`/`claude_code`/`teclaw`；`aicoding`/`hermes`/`moltis` 不可服务化、无健康检查。
- **teclaw 引擎归属（2026-08-12 lucas确认，产品权威口径）**：teclaw **仅支持云端 Bot**，**本地 Bot 不支持 teclaw**。`LOCAL_CAPABLE_ENGINES = {openclaw, claude_code}`（不含 teclaw），`PERSONAL_CLOUD_CAPABLE_ENGINES = SUPPORTED_ENGINE_TYPES`（含 teclaw）。本地路径 `POST /openapi/v1/bots/local` 携 `engine=teclaw` 在 combo policy 前门即拒（409 `local bot does not support engine: teclaw`），不进入 desktop/BaaS provisioning。云端 `POST /openapi/v1/bots` 创建 personal teclaw bot 受支持。
- license = Agent Passport，复用 #11 `GET /openapi/v1/bots/{bot_id}/passport`，加 `expire_at`/`certificate_url`，**无新端点**。
- 本地 Bot「重启引擎」+「运行日志」本期灰掉（desktop 无能力），后续桌面端补。
- 回收：30 天无对话→回收，仅非服务，本地豁免，蒙层非状态（改 `bot_dormant` service 逻辑，不动契约）。
- 容器：BaaS 无实例 metrics 接口，#91 返回 `summary`+`instances[id,node,status]`，cpu/mem 留空。
- **data-init 触发时机与待补契约（2026-08-13 复核）**：保留 `POST /openapi/v1/bots/{bot_id}/data-init`(#84) 为独立触发端点；创建弹窗勾选后，前端只在同步创建成功或 202 授权完成、Bot 真正落库后再调用，**不给 #1 `POST /bots` 增加 `init_config`，创建链路本身无 init 副作用**。但当前实现尚不能声明与老 `/api` 1:1：老 handler 会把 Cookie `IAM_TOKEN` 暂存到 `bot.ext` 供 `DataInitService` 使用，新 OpenAPI handler 尚未做等价凭证传递；公开 `Bot`/`BotInventoryItem` 又不暴露 `ext.data_init_status`，因此前端没有合法轮询入口。联调前必须补齐 IAM 凭证方案，并优先增加显式 data-init status 查询契约（避免重新公开整个 `ext`）；完成前 #84 仅代表 trigger router 已开发，不代表端到端可用。
- **业务空间最终口径（2026-08-13 复核）**：`ac_bots.space_id` 是 Bot 归属空间的结构化存储列，不使用 `bot.ext.space_id`。当前已保留的公开能力只有：① #1 创建时接收并写入 `space_id`；② #67 `/bots/all` 通过 `X-Space-Id` + `BusinessSpaceContextProtocol` 解析当前空间，并按 card 的 `space_id` 过滤。ABC 清理已经回退 #2 `/bots` 的 `space` query 和 #14 基础 `Bot.space`，不得再描述为已落地。当前 noop business-space adapter 仅提供 `personal:{owner_id}` fallback；团队空间的 owner/name/kind、成员校验和可见性依赖后续 prod adapter。仓库当前只核到 ORM `plugin_api/models.py:87` 的 nullable 列，未发现 migration/DDL 文件；若生产 DDL 由外部流程执行，需在交付记录中补 owner、目标环境和完成状态，不能仅以 ORM 列认定数据库已升级。
- **协作能力(edit-lock/editors #95-98)归 B 线/joseph,A 线移交流程（2026-08-12 lucas移交,会话对账后定）**：协作能力本质属服务 bot,非个人云端 bot 核心场景。证据三条:① `collaborator_service.py` 异常名 `BotNotServiceTypeError`——协作者 CRUD 默认拒绝非 service Bot;② `MemberManagementCapabilityService.can_manage_collaborators` 对 non-service 返 `False`,个人云端 Bot 默认无协作者;③ 协作消费端 18 处全在 `core/service_bot/*` 与 `adapters/http/service_bot/*`,`service_bot/router_publish.py` 挂 `CollaboratorPermissionInterceptor` 15 次 ——服务 bot 发布链路才重度依赖协作。老 `/api/bot/collaborator/add` 在非 service 时拒并日志 `"Bot not service type"`。**系分 §10.10 原把 edit-lock/editors 划 A 线**,但代码证据显示其真正"深入"工作在 `service_bot` 模块内(joseph B 线)。决定:**#95-98 整组移交 B 线**——A 线不作公开契约、不动 service;B 线负责公开面+内部改造(协作者空间成员先验 + 唯一 admin,碰红线但属 B 线范围)。**A 线 P1 协作范围清空**,个人云端 Bot 协作能力随团队空间 Bot 开协作后由 B 线统一对外接入。
- **inventory/dormant 并回 `bots/router.py` + 最终路径 `/all`（C 方案，2026-08-13 复核）**：inventory 的 personal cloud+desktop 数据最终都来自 `ac_bots` 相关 service，职责是“两 service 聚合 + 富字段派生 + 动作矩阵 + 空间横切”；dormant 只有 bot-level 激活动作。最终决定为：删除 `openapi_v1/inventory/` 与 `openapi_v1/dormant/` 独立子包，schema/handler 并入 `bots`，列表路径从早期 `/inventory`、中间 `/cards` 最终定为 `GET /bots/all`，激活保留 `POST /bots/{bot_id}/activate`。单卡 `/all/{bot_id}` 与独立 `/{bot_id}/actions` 已删除；仅保留 `_to_inventory_item` 转换，不再存在 `_to_inventory_actions`。core service `BotInventoryServiceProtocol`/`ActivateBotService` 保持独立；`__init__.py._SUBGROUPS` 不再注册 inventory/dormant router，reserved literal 使用 `all`。
- **#80 引擎重启 走 openapi relay 转发、不新 core adapter（2026-08-12 lucas定,老前端 URL 印证）**：老前端"重启引擎"实际直调网关 `agentclawproxy-pre/proxypass/<binding>:20003/api/engine/restart?ctoken=...`——即**设备侧 engine adapter daemon** 暴露的 HTTP 端点,**不在 backend**。改版后公开面 = `POST /openapi/v1/bots/engine/{bot_id}/restart`(归既有 `openapi_v1/engine_runtime/engine` 组件,与 status/capabilities/available 同组四端点),**复用 `EngineRuntimeRelayProtocol.call(method="POST", path="/api/engine/restart")`** 把请求转发到同一设备侧端点——零新 core adapter、零 `supervisorctl` exec、零 BaaS exec,纯范式复用。语义 ≠ #8 `POST /openapi/v1/bots/{bot_id}/restart`(后者委托 `BaasService.restart_bot` re-provision 整个 container、断 session);引擎重启只重启 engine 进程、不重置容器/session。`engine_runtime/engine` router 顶 docstring 原说"restart deliberately not wrapped(已被 #8 覆盖)",已订正——老前端 URL 证明 #8 覆盖论错,二语义分离。#81/#82 health/check 与 #79 runtime-logs 待日志 URL 给定后同评估是否走 relay。
