# Bot 工坊改版 — 实现计划(A/B 分工)

- **日期**: 2026-08-11
- **关联系分**: 团队仓 `securitytec/otbct4/dsame52bmg6mggwq`《Bot 工坊改版 — 系分方案(OpenAPI 驱动版)》§3 + §10
- **Scope**: 仅 Bot 工坊模块后端落地。能力工坊 / 能力市场 / 管理后台 / 工作 Tab 不在本计划。
- **分工**:
  - **A = lucas** —— 个人线 + 横向治理 + 契约接口人
  - **B = joseph** —— 服务线 + 编辑页内核 + 空间
- **策略**: 工坊全走 `/openapi/v1` 新接口;老接口尽量不改、纯加法;新端点委托现有内部 service。端点寻址遵循组件在前(系分 §10.2)。

---

## 1. 分工原则(避冲突)

1. **契约接口人 = A**:A 独占 `core/bot_workshop/protocols.py`、`types.py`、`policies/combo_policy.py`、`gateway/configs/schemas/bots.openapi.json`、`gateway/.../_compat.py`、`openapi_v1/__init__.py` 的 `_SUBGROUPS` 注册块。B 改契约走 PR 让 A 合。
2. **sub-router by concern,文件隔离**:A/B 各自分守自己的 router 文件,不共文件;`WorkshopBotCard` schema 由 A 独占(B 的子 schema 由 A 的 card 组合)。
3. **内部 service 改动按文件天然隔离**:A 改 `bot_dormant`/`desktop`/`bot_collaborator`/harness adapter;B 改 `router_publish`/`skill_center`/`build`/`channel`/BaaS/新 space 模块。
4. **contracts-first 时序**:A 先出 protocols + noop 实现(P0 头 1-2 天),B 才能并行实现 prod impl,否则 B 全程阻。
5. **端点路径**:全部组件在前(系分 §10.2)。

---

## 2. A / lucas —— 个人线 + 横向治理(build sheet)

| 文件 | 端点(均 `/openapi/v1/bots` 前缀) | 委托 / 改 | 量 | 阶段 |
|---|---|---|---|---|
| `core/bot_workshop/protocols.py` + `types.py` | — | 定义 `BotWorkshopViewProtocol`/`BotHealthPort`/`SpaceScopeProtocol`/`BotLifecycleViewProtocol`/`BotDiagnosticsPort`/`SpaceMembershipDep`/`combo_policy`/`WorkshopBotCard` | S | P0 先 |
| `core/bot_workshop/adapters/{noop_health,noop_space}.py` + `di/modules/bot_workshop_module.py` | — | permissive noop + DI 注入(Rule 14/20) | S | P0 |
| `openapi_v1/__init__.py` + `gateway/bots.openapi.json` + `_compat.py` | — | 加 A/B router 注册 + 新路径(加法) | S | 全程 |
| `openapi_v1/bot_workshop/router_workshop.py` | `GET /workshop`、`GET /workshop/{bot_id}`、`GET /workshop/{bot_id}/actions` | `bot_service.search_bots` + `BotPublishRepository` + `BotHealthPort` + `DesktopBotServiceProtocol`,聚合在 `core/bot_workshop/services/workshop_view_service.py` | M | P0 |
| `openapi_v1/bot_workshop/router_personal.py` | `POST /local`(创建本地 Bot,带 machine_id/mount_path) | `DesktopBotServiceProtocol` | S | P0 |
| `openapi_v1/bot_workshop/router_diag.py` | `GET /diagnostics/{bot_id}/runtime-logs`、`POST /diagnostics/{bot_id}/engine-restart`、`GET /diagnostics/{bot_id}/health`、`POST /diagnostics/{bot_id}/health-check` | runtime-logs 封装 `BaasService.exec_command_on_bot`(只读/限路径/level/tail,按型分流 BaaS exec vs desktop tunnel);engine-restart 桥接 `engine /api/engine/restart`;health 委托 harness | M | P0(runtime-logs/engine-restart)、P2(health prod) |
| `openapi_v1/bot_workshop/router_dormant.py` | `POST /dormant/{bot_id}/activate` | `DormantBotServiceProtocol.activate` | S | P0 |
| `openapi_v1/bot_workshop/router_edit_lock.py` | `POST/DELETE /edit-lock/{bot_id}`、`POST /edit-lock/{bot_id}/steal`、`GET /edit-lock/{bot_id}` | `bot_collaborator` lock service;锁-状态联动(服务类仅草稿态可锁)落 policy | M | P1 |
| `openapi_v1/bot_workshop/router_editors.py` | `GET/POST/PATCH/DELETE /editors/{bot_id}`(+`/{member_id}`) | 改 `bot_collaborator`:空间成员先验集合 + 唯一管理员 | M | P1 |
| `openapi_v1/bot_workshop/router_space.py`(只读 list) | `GET /spaces`(列当前用户空间,给切换器) | `SpaceScopeProtocol`(本期 permissive) | S | P0 |
| 老面加法:`openapi_v1/bots/schemas.py` + `bots/router.py` | `BotCreate` += `init_config: bool = False`;create_bot 透传 →命中 `POST /api/bots/{id}/data-init` | 加法,不动 required | S | P0 |
| 横切:`SpaceMembershipDep` | 注入到所有 per-bot handler | `SpaceScopeProtocol.assert_member(bot_id, caller)` | S | P0 |
| `combo_policy` | 5 合法组合 + 引擎兼容矩阵(系分 §10.1) | A 作者,B 消费 | S | P0 |

**A 量 ≈ 10**(偏集成/契约,不确定性低)。**A 是契约接口人,先行 1-2 天出 protocols+noop**。

---

## 3. B / joseph —— 服务线 + 编辑页内核 + 空间(build sheet)

| 文件 | 端点(均 `/openapi/v1/bots` 前缀) | 委托 / 改 | 量 | 阶段 |
|---|---|---|---|---|
| `openapi_v1/bot_workshop/router_lifecycle.py` | `POST /lifecycle/{bot_id}/advance`、`/offline`、`/restart`、`/upgrade`、`GET/PUT /lifecycle/{bot_id}/approval` | `publish_flow_service`(委托);`upgrade` 改内部 `update_bot_type` 去反向(service→personal 不可逆);`approval` 委托 approvals | L | P1(advance/offline/restart/upgrade)、P2(approval) |
| `core/bot_workshop/services/lifecycle_service.py`(service 分支) | 实现 `BotLifecycleViewProtocol` 服务分支(publish 状态→`DisplayState`) | A 定义协议,B 插 service impl | M | P1(impl)、P2(接入 A 的聚合 card) |
| `openapi_v1/bot_workshop/router_containers.py` | `GET /containers/{bot_id}`(+`/?summary` 含 total/healthy/abnormal + instances[id,node,cpu,mem,status])、`POST /containers/{bot_id}/{instance_id}/restart`、`GET /containers/{bot_id}/{instance_id}/logs` | BaaS 容器实例 metrics(数据源待核);仅运行态服务 oc | M-L | P0(容器 list)、P1(单实例 ops) |
| `openapi_v1/bot_workshop/router_evaluation.py` | `POST /evaluation/{bot_id}` | `quality /tasks/create`(biz_type=service_bot_single),返回评测页 URL/token;仅服务预发/运行态 | S | P1 |
| `openapi_v1/bot_workshop/router_editpage.py`(或拆 skill-sets/flow/channels/nodes/render-screens 分文件) | `GET/POST/PUT/DELETE /skill-sets/{bot_id}`(+`/{set_id}/{skills,mcps}`)、`GET/PUT /flow/{bot_id}`、`GET /flow/{bot_id}/runs`、`GET/PUT /channels/{bot_id}`、`GET /nodes/{bot_id}`、`GET /render-screens/{bot_id}` | skill_center(skill-sets+引用型 skill 版本同步/不可改/血缘)+ per-bot MCP+caller;**files 已并入 `/resources`,不再单建**(`2026-08-18` 复核,见 `2026-08-18-bot-workshop-integration-matrix.md` §3.1.1 / §6.7);`flow` 依赖 engine(待对齐);`channels` 委托 `/api/channels`;`nodes` 委托 engine `/api/nodes`;`render-screens` 委托 `/api/bot-render-screens` | L | P2 |
| `openapi_v1/bot_workshop/router_space.py`(写) | `POST /migrate/{bot_id}` body `{target_space}`(校验编辑者是否目标空间成员,非成员移除);`SpaceScopeProtocol` prod impl | 新 space 独立表;上游空间中台就绪后换 impl,协议不变 | L | P3 |

**B 量 ≈ 9**(点数略轻但扛 3 个高风险:服务容器 BaaS 数据源 / 编辑页 engine 依赖 / 空间绿地)。**B 依赖 A 的 protocols,不可先于 A 的 contracts**。

> 工坊只 own `GET /spaces`(list)+ `POST /migrate`+ `editors`(bot 级编辑授权);**空间成员 CRUD = 管理后台 owner,B 经 `SpaceScopeProtocol` 消费**(系分 §10.4)。
>
> **§8 决议订正(以 §8 为准)**:`/containers` 本期 cpu/mem 留空(BaaS 无 metrics);`/flow` 属**任务护航独立模块**(跨所有云端 Bot,不区分服务/个人云端),B 只做封装,flow 引擎由该独立模块 owner 建;`license` 不新建端点,复用 `GET /passport`;引擎枚举含 `teclaw`(6 种),`SUPPORTED_ENGINE_TYPES` 需补 `teclaw`。

---

## 4. 契约缝(A/B 依赖关系)

- **A 定义,B 实现或消费**:
  - `BotLifecycleViewProtocol` —— A 实现 personal/desktop 分支;**B 实现 service 分支**。A 的聚合 card 调协议,B 插 service impl(P2 接入)。
  - `BotHealthPort` —— A 一份 harness adapter;B 的服务 card 直接消费,不另建。
  - `combo_policy` —— A 作者;B 的 `/lifecycle/upgrade` 调它校验(本地/Hermes 不可服务化)。
  - `SpaceScopeProtocol` —— A 作者 + permissive 兜底;B 的 `/migrate` + `SpaceScopeProtocol` prod impl(P3)。
  - `SpaceMembershipDep` —— A 作者;A/B 的 per-bot handler 都注入(横切)。
- **WorkshopBotCard** —— A 独占 schema;B 的 health/container/skill-set 子 schema 由 A 的 card 组合(B 不改 card)。

---

## 5. 时序(对齐 8/31 灰度;加法先行,语义/空间后置)

```
P0(Week1)
  A 先行(头 1-2 天):protocols + types + combo_policy + noop + DI + 契约面注册
  A 并行:router_workshop(聚合先用 noop health/space)+ router_personal(/local)
       + router_diag(runtime-logs/engine-restart)+ router_dormant(activate)
       + router_space(/spaces 只读)+ 老面 init_config + SpaceMembershipDep
  B    :router_containers(容器 list,node/summary)         ← A 出 protocols 后即起

P1(Week2)
  A    :router_edit_lock + router_editors(协作者改造,改 bot_collaborator)
  B    :router_lifecycle(advance/offline/restart/upgrade,委托 publish_flow + 改 update_bot_type)
       + router_evaluation + lifecycle service 分支 impl + containers 单实例 ops

P2(Week3)
  A    :router_diag health 接 harness prod;把 B 的 service-lifecycle impl 接入聚合 card(display_state 服务分支)
  B    :router_editpage(skill-sets+引用skill+MCP+caller /flow /channels /nodes /render-screens;files 已并入 /resources,见 matrix §3.1.1)
       + lifecycle /approval

P3(Week4)
  B    :router_space 写(/migrate)+ SpaceScopeProtocol prod impl + 空间独立表
  A    :singlebox 覆盖 + 灰度集成 + 契约面收口(openapi.json/_compat)
```

---

## 6. 验证策略

- **每 phase 末**:
  - `pytest`:A 聚合映射 / 动作路由 / combo_policy(5 合法组合+引擎兼容)/ 锁-状态联动;B 生命周期状态机 / 容器 summary / skill-set 引用语义。
  - singlebox E2E:列表(三型合一)/ 创建(本地+云端)/ 状态推进 / 激活 / 编辑页四 Tab。
- **全程不动**:47 条现存公开路径签名 + 内部 `/api` + BCN 协作面 8 条存量调用 + 两套契约签名。
- 改完跑 `scripts/ci/singlebox_coverage.sh` 相关 gate;新端点加入 singlebox 编排。
- 覆盖率按 `AGENTS.md` 改动行 ≥80%(pre-push lint-only,本地 `ci_test.sh` 复现)。

---

## 7. 风险 / 闸门

| # | 风险 | 闸门 |
|---|---|---|
| 1 | A contracts 延迟 → B 全程阻 | P0 头 1-2 天 A 必须合 protocols+noop+契约注册 |
| 2 | B 编辑页 engine 依赖(flow/nodes/render-screens)未对齐 | P2 前与 engine owner 确认;任务护航 scope 未定(系分 §10.8)则该 Tab 占位 |
| 3 | B 容器 BaaS metrics 数据源未核 | P0 容器开工前核 BaaS 是否有容器实例 metrics 接口;若无 → 降级或新建 |
| 4 | 引擎名映射(系分 §10.1) | P0 落代码映射 `claudecode-native/-ai/-app`;`moltis` 工坊不展示 |
| 5 | 空间中台 P3 排期 | 若上游不就绪,B 的 space 降 permissive 兜底(系分 §10.4) |
| 6 | 服务化单向改 `update_bot_type` 去反向 | 回归 `upgrade_bot_type_for_others` / 回滚路径不冲突 |
| 7 | 路径形状(组件在前)与 BCS `collaboration` 冲突 | 协作用 `edit-lock`/`editors`,不撞(系分 §10.2) |

---

## 8. 决议(2026-08-11 讨论后最终)

| # | 决议 | 代码/前端依据 |
|---|---|---|
| 1 | **任务护航 flow 优先级 P2**(不阻塞 P0/P1);属独立模块,跨所有云端 Bot(不区分服务/个人云端,本地 Bot 待定);工坊 B 只在 P2 做 `/flow`+`/flow/runs` 封装。**flow 引擎已找到 = BCS State Machine**(`src/bcs/crates/adapters/http/bcs-http/src/router.rs`:`/state-machine-runs/{run_id}`+`/graph`+`/nodes/{node_id}`+`/pending-human-nodes`+`/respond`+`/cancel`,Rust;`human-node-implementation.md` 2026-07-21 已实现),**但未上 openapi**(`bcs/api-contracts/v1/openapi.yaml` 仅 `collaboration/*`)。owner = BCS;P2 封装时委托 BCS state machine,需 BCS 先把 state machine 加进 openapi 或允许工坊直调内部 | 全分支扫:flow 引擎在 BCS,非 backend/engine |
| 2 | BaaS **无实例 metrics 接口**(仅 stop/scale/restart/execute-command/open-folder/ws-info/publishes/devices);`/containers` 本期返回 `summary{total,healthy,abnormal}`+`instances[id,node,status]`,**cpu/mem 留空**待 BaaS 补 | baas_service URL 枚举 |
| 3 | **license = Agent Passport**,复用 `GET /openapi/v1/bots/{bot_id}/passport`;核对返回含 `expire_at`/`certificate_url`/`agent_code`,缺则加法补字段。卡片 `idcard` = 已授权+到期态,**无新端点** | 前端 `useBot.ts:queryAgentPassport` 已返回这些字段;后端 `bot_service.py:3355` agent 许可证(Passport) |
| 4 | "当前空间":前端发 `X-Space-Id` 头 → 后端 `SpaceContextDep` 解析进 principal/context;list + `SpaceMembershipDep` 共读 | 现有 `PrincipalDep`+`AvernetTenantMiddleware`(租户,非空间) |
| 5 | 本地 Bot:「重启」委托 desktop `POST /api/desktop/bots/{bot_id}/restart`;「重启引擎」+「运行日志」**本期灰掉**(desktop 无现成能力),后续桌面端补 | desktop router:有 `/{bot_id}/restart`,无日志读/引擎重启/文件内容读 |
| 6 | 引擎枚举**按代码 + teclaw**(老模块下 teclaw 是引擎,并排 openclaw/aicoding/hermes):公开面 engine = `moltis/openclaw/hermes/aicoding/claude_code/teclaw`(6 种);**不引入 PRD `claudecode-native/-ai/-app` 映射**(文案名前端自映射)。`combo_policy`:健康检查仅 `openclaw`;服务引擎 & 开启服务化 = `openclaw`/`claude_code`/`teclaw`;`aicoding`/`hermes`/`moltis` 不可服务化、无健康检查。`SUPPORTED_ENGINE_TYPES` 常量需补 `teclaw`(小改) | `core/workspace/constants.py:9`(5 种)+ `EngineProvisioningRegistry`(teclaw 作引擎)+ 集群双射 `ANDC↔teclaw` |

**遗留 1 个外部确认**(P2 闸门,不阻塞 P0/P1):任务护航 flow 引擎 = BCS State Machine(owner=BCS),需 BCS 把 `/state-machine-runs/*` 加进 openapi 或允许工坊直调内部;P2 开工前与 BCS owner 对齐。其余 6 项已定。

---

> 本计划为执行视图;设计依据与审计见系分(团队仓 `securitytec/otbct4/dsame52bmg6mggwq`)。A/B 按 contracts-first 并行,每周 phase 末对齐 singlebox 验证。