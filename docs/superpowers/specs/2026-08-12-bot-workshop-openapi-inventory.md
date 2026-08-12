# Bot 工坊 OpenAPI 全量清单（TC 改版）

- **日期**: 2026-08-12
- **整理人**: 融志（A 线 · 个人/本地 Bot + 壳层 + 诊断/协作，契约接口人）
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
- **组件字面量在前、`{bot_id}` 在后**：如 `/openapi/v1/bots/inventory/{bot_id}`、`/openapi/v1/bots/diagnostics/{bot_id}/runtime-logs`，不允许 `/{bot_id}/xxx` 形态（`bots` 组件自身是唯一例外，它拥有根 `/openapi/v1/bots/{bot_id}` 及其子资源 `/openapi/v1/bots/{bot_id}/restart` 等）。
- literal 子组件必须挂在 `bots` 通配 `/openapi/v1/bots/{bot_id}` 之前（`_SUBGROUPS` 先于 `bots_router`）。

### 0.2 鉴权与作用域约定
- 用户维度统一 `?user_id=`（`UserIdDep`），必须与已验证 principal 用户一致，403 由公共面统一处理。
- 空间维度走 `X-Space-Id` 头 + `SpaceScopeProtocol`（系分 §10.3/§10.4）：
  - **list/创建类**：带 `?space=` 或 body `space_id`。
  - **per-bot 类**：不带 space query，走横切 `SpaceMembershipDep`。
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
| **完成状态** | `已落地` = router 代码已存在并注册；`待升级` = 现网在但加法未做；`已落地(本分支)` = 本开发分支新建并注册；`已设计未建` = 本机技术设计已定形但未写 router；`未开工` = 仅系分有端点计划 |
| **阶段** | 系分 §7 排期：P0(8/07–8/15) / P1(8/15–8/22) / P2(8/22–8/29) / P3(8/29–8/31)；`—` = 现网已上线、无排期 |

### 0.5 命名已决定（2026-08-12）
- 系分 §3-A/§10.2 原称 `/openapi/v1/bots/workshop`（list）+ `/workshop/{bot_id}`（card）+ `/workshop/{bot_id}/actions`，作跨 personal+local+service 富卡片面。
- **已决定**采用 `/openapi/v1/bots/inventory`（更符合领域模型），不再使用 `/workshop`。范围 = 个人云端 + 本地 Bot；service Bot 仅经 `ServiceLifecyclePort` seam 预留展示态，本模块不操作其生命周期。
- 溯源：本决定由契约接口人（融志）2026-08-12 给出，覆盖系分。系分语雀文档侧尚未同步改名；后续更新系分时统一改成 `/inventory`。
- 影响 #67–69；其「委托/备注」列保留「系分原称 `/workshop`」仅作交叉溯源，不代表待决。

---

## 1. 全量端点清单

| # | 模块 | 接口功能 | 全路径（Method 在首） | 归类 | 完成状态 | 阶段 | 委托 / 备注 |
|---|---|---|---|---|---|---|---|
| 1 | bots | 创建 Bot（personal\|service，拒 desktop） | `POST /openapi/v1/bots` | 升级 | 待升级 | P0 | 加可选 `init_config` 入参；委托 `BotService` |
| 2 | bots | 列表 | `GET /openapi/v1/bots` | 升级 | 待升级 | P0 | 加 `deploy_mode`/`service`/`space` 可选筛选 |
| 3 | bots | 重名校验 | `GET /openapi/v1/bots/check-name` | 已有 | 已落地 | — | 租户级，无 `user_id` |
| 4 | bots | 配额上限 | `GET /openapi/v1/bots/ceiling` | 已有 | 已落地 | — | — |
| 5 | bots | Bot 详情 | `GET /openapi/v1/bots/{bot_id}` | 已有 | 已落地 | — | 见 #14「Bot 响应体」行 |
| 6 | bots | 改名 / 描述 | `PUT /openapi/v1/bots/{bot_id}` | 已有 | 已落地 | — | engine 不可改 |
| 7 | bots | 删除 Bot | `DELETE /openapi/v1/bots/{bot_id}` | 已有 | 已落地 | — | **拒 desktop + service** |
| 8 | bots | 重启进程 | `POST /openapi/v1/bots/{bot_id}/restart` | 已有 | 已落地 | — | **拒 desktop**；≠重启引擎 |
| 9 | bots | 授权轮询 / 完成创建 | `GET /openapi/v1/bots/{bot_id}/auth-status` | 已有 | 已落地 | — | 202 流在此 ISSUED 时真正落库 |
| 10 | bots | 就绪布尔 | `GET /openapi/v1/bots/{bot_id}/status` | 已有 | 已落地 | — | **≠健康分** |
| 11 | bots | Agent Passport / 许可证 | `GET /openapi/v1/bots/{bot_id}/passport` | 升级 | 待升级 | P0 | 加 `expire_at`/`certificate_url`；卡片 `idcard`=许可证，无新端点 |
| 12 | bots | 读引擎配置 | `GET /openapi/v1/bots/{bot_id}/engine-config` | 已有 | 已落地 | — | — |
| 13 | bots | 写引擎配置 | `PUT /openapi/v1/bots/{bot_id}/engine-config` | 已有 | 已落地 | — | — |
| 14 | bots | _（schema）Bot 响应体_ | 影响 #1/#2/#5/#6/#8 等所有返回 `Bot` 的端点 | 升级 | 待升级 | P0 | 加**可选可空** `deploy_mode`/`space`/`health_score`/`display_state`/`version`/`containers`/`license`；不动三个 required |
| 15 | engine | 引擎运行态 | `GET /openapi/v1/bots/engine/{bot_id}/status` | 已有 | 已落地 | — | engine_runtime/engine |
| 16 | engine | 引擎能力 | `GET /openapi/v1/bots/engine/{bot_id}/capabilities` | 已有 | 已落地 | — | — |
| 17 | engine | 可用引擎列表 | `GET /openapi/v1/bots/engine/{bot_id}/available` | 已有 | 已落地 | — | 创建表单引擎候选 |
| 18 | sessions | 会话列表 | `GET /openapi/v1/bots/sessions/{bot_id}` | 已有 | 已落地 | — | 编辑页调试对话 |
| 19 | sessions | 新建会话 | `POST /openapi/v1/bots/sessions/{bot_id}` | 已有 | 已落地 | — | — |
| 20 | sessions | 会话详情 | `GET /openapi/v1/bots/sessions/{bot_id}/{session_id}` | 已有 | 已落地 | — | — |
| 21 | sessions | 更新会话 | `PATCH /openapi/v1/bots/sessions/{bot_id}/{session_id}` | 已有 | 已落地 | — | — |
| 22 | sessions | 删除会话 | `DELETE /openapi/v1/bots/sessions/{bot_id}/{session_id}` | 已有 | 已落地 | — | — |
| 23 | sessions | 消息列表 | `GET /openapi/v1/bots/sessions/{bot_id}/{session_id}/messages` | 已有 | 已落地 | — | — |
| 24 | sessions | 清空消息 | `DELETE /openapi/v1/bots/sessions/{bot_id}/{session_id}/messages` | 已有 | 已落地 | — | — |
| 25 | routines | 定时任务列表 | `GET /openapi/v1/bots/routines` | 已有 | 已落地 | — | 工作 Tab「定时任务」 |
| 26 | routines | 新建任务 | `POST /openapi/v1/bots/routines` | 已有 | 已落地 | — | — |
| 27 | routines | 任务详情 | `GET /openapi/v1/bots/routines/{routine_id}` | 已有 | 已落地 | — | — |
| 28 | routines | 更新任务 | `PATCH /openapi/v1/bots/routines/{routine_id}` | 已有 | 已落地 | — | — |
| 29 | routines | 删除任务 | `DELETE /openapi/v1/bots/routines/{routine_id}` | 已有 | 已落地 | — | — |
| 30 | routines | 手动触发 | `POST /openapi/v1/bots/routines/{routine_id}/run` | 已有 | 已落地 | — | — |
| 31 | routines | 执行历史 | `GET /openapi/v1/bots/routines/{routine_id}/runs` | 已有 | 已落地 | — | — |
| 32 | models | 模型列表 | `GET /openapi/v1/bots/models/{bot_id}` | 已有 | 已落地 | — | — |
| 33 | models | 模型详情 | `GET /openapi/v1/bots/models/{bot_id}/{model_id}` | 已有 | 已落地 | — | `{model_id}` 实为 path 参数 |
| 34 | identity | MD / 身份文件列表 | `GET /openapi/v1/bots/identity/{bot_id}` | 已有 | 已落地 | — | 「MD 管理」入口 |
| 35 | identity | 读单个 MD | `GET /openapi/v1/bots/identity/{bot_id}/{file_type}` | 升级 | 待升级 | P2 | `file_type` 是否覆盖 13 个 MD 待核；未覆盖加法补枚举 |
| 36 | identity | 写单个 MD | `PUT /openapi/v1/bots/identity/{bot_id}/{file_type}` | 升级 | 待升级 | P2 | 同上 |
| 37 | bot_logs | 对话 trace 检索 | `GET /openapi/v1/bots/logs/traces` | 已有 | 已落地 | — | **≠运行日志抽屉**；`user_id` 此处是「被查人」非「调用者」 |
| 38 | bot_logs | trace 详情 | `GET /openapi/v1/bots/logs/traces/{trace_id}` | 已有 | 已落地 | — | — |
| 39 | bot_logs | 会话 trace | `GET /openapi/v1/bots/logs/sessions/{session_key}/traces` | 已有 | 已落地 | — | — |
| 40 | bot_logs | 群 trace | `GET /openapi/v1/bots/logs/groups/{group_id}/traces` | 已有 | 已落地 | — | — |
| 41 | bot_logs | 任务 trace | `GET /openapi/v1/bots/logs/tasks/{biz_scene}/{biz_task_id}/traces` | 已有 | 已落地 | — | — |
| 42 | resources | 资源列表 | `GET /openapi/v1/bots/resources` | 已有 | 已落地 | — | **≠容器文件目录树** |
| 43 | resources | 资源重名校验 | `GET /openapi/v1/bots/resources/check-name` | 已有 | 已落地 | — | — |
| 44 | resources | 新建资源 | `POST /openapi/v1/bots/resources` | 已有 | 已落地 | — | — |
| 45 | resources | 上传资源 | `POST /openapi/v1/bots/resources/upload` | 已有 | 已落地 | — | — |
| 46 | resources | 资源详情 | `GET /openapi/v1/bots/resources/{resource_id}` | 已有 | 已落地 | — | — |
| 47 | resources | 更新资源 | `PUT /openapi/v1/bots/resources/{resource_id}` | 已有 | 已落地 | — | — |
| 48 | resources | 删除资源 | `DELETE /openapi/v1/bots/resources/{resource_id}` | 已有 | 已落地 | — | — |
| 49 | resources | 下载 | `GET /openapi/v1/bots/resources/{resource_id}/download` | 已有 | 已落地 | — | — |
| 50 | resources | 预览 | `GET /openapi/v1/bots/resources/{resource_id}/preview` | 已有 | 已落地 | — | — |
| 51 | approvals | 审批模式 | `GET /openapi/v1/bots/approvals/{bot_id}/mode` | 已有 | 已落地 | — | 关联「发布配置」 |
| 52 | approvals | 设置审批模式 | `PUT /openapi/v1/bots/approvals/{bot_id}/mode` | 已有 | 已落地 | — | — |
| 53 | approvals | 可用模式列表 | `GET /openapi/v1/bots/approvals/{bot_id}/modes` | 已有 | 已落地 | — | — |
| 54 | connection | 引擎连接诊断 | `GET /openapi/v1/bots/connection/{bot_id}` | 已有 | 已落地 | — | — |
| 55 | skills | 本地 Skill 列表 | `GET /openapi/v1/bots/skills` | 已有 | 已落地 | — | 仅本地 Skill |
| 56 | skills | Skill 详情 | `GET /openapi/v1/bots/skills/{skill_id}` | 已有 | 已落地 | — | — |
| 57 | skills | 上传本地 Skill | `POST /openapi/v1/bots/skills/upload` | 已有 | 已落地 | — | — |
| 58 | skills | 激活 Skill | `POST /openapi/v1/bots/skills/{skill_id}/activate` | 已有 | 已落地 | — | — |
| 59 | skills | 停用 Skill | `POST /openapi/v1/bots/skills/{skill_id}/deactivate` | 已有 | 已落地 | — | — |
| 60 | skills | 删除 Skill | `DELETE /openapi/v1/bots/skills/{skill_id}` | 已有 | 已落地 | — | 引用型 Skill（市场/工坊）在 #99 `skill-sets` |
| 61 | mcp | MCP 服务目录 | `GET /openapi/v1/bots/mcp/servers` | 已有 | 已落地 | — | 仅服务级目录 |
| 62 | mcp | 租户列表 | `GET /openapi/v1/bots/mcp/tenants` | 已有 | 已落地 | — | — |
| 63 | mcp | 服务详情 | `GET /openapi/v1/bots/mcp/servers/{server_code}` | 已有 | 已落地 | — | — |
| 64 | mcp | 权限 | `GET /openapi/v1/bots/mcp/servers/{server_code}/permissions` | 已有 | 已落地 | — | — |
| 65 | mcp | 读配置 | `GET /openapi/v1/bots/mcp/servers/{server_code}/config` | 已有 | 已落地 | — | — |
| 66 | mcp | 写配置 | `PUT /openapi/v1/bots/mcp/servers/{server_code}/config` | 已有 | 已落地 | — | per-bot 绑定 + caller 在 #99 `skill-sets/mcps` |
| 67 | inventory | 个人云端 + 本地清单分页 | `GET /openapi/v1/bots/inventory` | 新增 | 已落地(本分支) | P0 | 系分原称 `/openapi/v1/bots/workshop`；`BotInventoryService` 聚合 `BotService` + `DesktopBotService` |
| 68 | inventory | 单清单项 | `GET /openapi/v1/bots/inventory/{bot_id}` | 新增 | 已落地(本分支) | P0 | 系分原称 `/openapi/v1/bots/workshop/{bot_id}`（card） |
| 69 | inventory | 可用动作集 | `GET /openapi/v1/bots/inventory/{bot_id}/actions` | 新增 | 已落地(本分支) | P0 | 驱动 §0.2 按钮渲染；系分原称 `/openapi/v1/bots/workshop/{bot_id}/actions` |
| 70 | local | 设备列表 | `GET /openapi/v1/bots/local/devices` | 新增 | 已落地(本分支) | P0 | 创建选 machine；`DesktopBotServiceProtocol.list_devices` |
| 71 | local | 设备目录树 | `GET /openapi/v1/bots/local/devices/{machine_id}/files` | 新增 | 已落地(本分支) | P0 | 选挂载目录；`list_directory` |
| 72 | local | 创建本地 Bot（201 / 202） | `POST /openapi/v1/bots/local` | 新增 | 已落地(本分支) | P0 | 委托 `DesktopBotService`；含 `machine_id`/`mount_path`/`init_config` |
| 73 | local | 本地 Bot 列表 | `GET /openapi/v1/bots/local` | 新增 | 已落地(本分支) | P0 | inventory 已覆盖，可选 |
| 74 | local | 本地 Bot 详情 | `GET /openapi/v1/bots/local/{bot_id}` | 新增 | 已落地(本分支) | P0 | — |
| 75 | local | 授权轮询 + 完成创建 | `GET /openapi/v1/bots/local/{bot_id}/auth-status` | 新增 | 已落地(本分支) | P0 | `create_after_authorization` |
| 76 | local | 重启本地 Bot | `POST /openapi/v1/bots/local/{bot_id}/restart` | 新增 | 已落地(本分支) | P0 | 委托 desktop restart |
| 77 | local | 删除本地 Bot | `DELETE /openapi/v1/bots/local/{bot_id}` | 新增 | 已落地(本分支) | P0 | 委托 desktop delete |
| 78 | local | 打开目录 | `POST /openapi/v1/bots/local/{bot_id}/open-folder` | 新增 | 已落地(本分支) | P0 | `open_folder` |
| 79 | diagnostics | 运行日志流 | `GET /openapi/v1/bots/diagnostics/{bot_id}/runtime-logs` | 新增 | 已设计未建 | P0 | ≠`/logs` trace；BaaS 白名单路径，`tail`/`level` 限制 |
| 80 | diagnostics | 重启引擎 | `POST /openapi/v1/bots/diagnostics/{bot_id}/engine-restart` | 新增 | 已设计未建 | P0 | ≠`switch-engine`；桥接 engine `/api/engine/restart` |
| 81 | diagnostics | 健康分 + 等级 | `GET /openapi/v1/bots/diagnostics/{bot_id}/health` | 新增 | 已设计未建 | P2 | 聚合 harness；仅 oc + 云端 |
| 82 | diagnostics | 触发健康检查 | `POST /openapi/v1/bots/diagnostics/{bot_id}/health-check` | 新增 | 已设计未建 | P2 | 仅 oc + 云端，policy 拦 |
| 83 | dormant | 激活沉寂 Bot | `POST /openapi/v1/bots/dormant/{bot_id}/activate` | 新增 | 已设计未建 | P0 | 30 天·仅非服务·本地豁免·蒙层非状态；委托 `bot_dormant` |
| 84 | bots | 初始化配置 | `POST /openapi/v1/bots/{bot_id}/data-init` | 新增 | 未开工 | P0 | 或以 `init_config` 可选入参加法到 #1 `POST /openapi/v1/bots` |
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
| 95 | edit-lock | 获取 / 抢占编辑锁 | `POST /openapi/v1/bots/edit-lock/{bot_id}`(+`/steal`) | 新增 | 已设计未建 | P1 | 委托 `bot_collaborator`；服务类仅草稿态可锁（policy） |
| 96 | edit-lock | 释放编辑锁 | `DELETE /openapi/v1/bots/edit-lock/{bot_id}` | 新增 | 已设计未建 | P1 | — |
| 97 | edit-lock | 编辑锁信息 | `GET /openapi/v1/bots/edit-lock/{bot_id}` | 新增 | 已设计未建 | P1 | 系分原称 `/lock/info` |
| 98 | editors | 协作者管理 | `GET/POST/PATCH/DELETE /openapi/v1/bots/editors/{bot_id}`(+`/{member_id}`) | 新增 | 已设计未建 | P1 | 空间成员先验集合 + 唯一管理员；锁-状态联动落 policy |
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
| **总端点 / 操作数** | **107** | 含 1 个 schema 行（#14） |
| 按归类 · 已有 | 60 | 现网存在、工坊直接复用 |
| 按归类 · 升级 | 6 | #1 create、#2 list、#11 passport、#14 Bot 响应体、#35/#36 identity |
| 按归类 · 新增 | 41 | 本次改版新建 |
| 按完成 · 已落地 | 60 | 现网已有 |
| 按完成 · 已落地(本分支) | 12 | inventory 3 + local 9 |
| 按完成 · 待升级 | 6 | 现网在但加法未做 |
| 按完成 · 已设计未建 | 9 | diagnostics 4 + dormant 1 + edit-lock 3 + editors 1 |
| 按完成 · 未开工 | 20 | data-init/lifecycle/containers/evaluation/skill-sets/files/flow/channels/nodes/render-screens/spaces/migrate |
| 按阶段 · P0 | 26 | 壳层 + 本地 + 诊断(运行日志/重启引擎) + 沉寂 + 容器 + 列表/创建升级 + data-init |
| 按阶段 · P1 | 12 | lifecycle 推进 + 服务化 + 评测 + 编辑锁/协作者 |
| 按阶段 · P2 | 14 | skill-sets + files + flow + channels + 审批 + 健康检查 + MD 管理 + nodes/副屏 |
| 按阶段 · P3 | 2 | 空间列表 + 迁移 |

---

## 3. 易踩坑对照（系分 §2.4）

| 易混淆（全路径） | 正确认知 | 对应新端点（全路径） |
|---|---|---|
| `GET /openapi/v1/bots/{bot_id}/status` | 是就绪**布尔**（PENDING/ACTIVE/FAILED），**非健康分** | `GET /openapi/v1/bots/diagnostics/{bot_id}/health` |
| `POST /openapi/v1/bots/{bot_id}/restart` | 重启**进程**且拒 desktop | `POST /openapi/v1/bots/diagnostics/{bot_id}/engine-restart`（≠switch-engine） |
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
- **teclaw 引擎归属（2026-08-12 融志确认，产品权威口径）**：teclaw **仅支持云端 Bot**，**本地 Bot 不支持 teclaw**。`LOCAL_CAPABLE_ENGINES = {openclaw, claude_code}`（不含 teclaw），`PERSONAL_CLOUD_CAPABLE_ENGINES = SUPPORTED_ENGINE_TYPES`（含 teclaw）。本地路径 `POST /openapi/v1/bots/local` 携 `engine=teclaw` 在 combo policy 前门即拒（409 `local bot does not support engine: teclaw`），不进入 desktop/BaaS provisioning。云端 `POST /openapi/v1/bots` 创建 personal teclaw bot 受支持。
- license = Agent Passport，复用 #11 `GET /openapi/v1/bots/{bot_id}/passport`，加 `expire_at`/`certificate_url`，**无新端点**。
- 本地 Bot「重启引擎」+「运行日志」本期灰掉（desktop 无能力），后续桌面端补。
- 回收：30 天无对话→回收，仅非服务，本地豁免，蒙层非状态（改 `bot_dormant` service 逻辑，不动契约）。
- 容器：BaaS 无实例 metrics 接口，#91 返回 `summary`+`instances[id,node,status]`，cpu/mem 留空。
