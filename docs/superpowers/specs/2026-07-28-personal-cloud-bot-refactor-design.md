# 个人云端 Bot — 资源 / 定时任务 / Identity 重构方案

- **日期**: 2026-07-28
- **版本**: v0.1(聚焦版,待评审)
- **Scope**: 仅 `bot_type == "personal"`(个人云端 Bot)。不涉及 service bot / desktop bot。
- **覆盖三块**: 资源(resources)、定时任务(cron)、调用身份(identity / caller_identity)
- **性质**: 产品重构,**只写文档不写代码**;取向"新增非侵入",严守架构宪法
- **关联**: PRD《TC改版方案-工作台部分(7月执行版)》§资源 / §定时任务 / 6-7月增量 #10 caller 身份调用一期 / #15 CLI 工具权限校验

---

## 0. 范围与边界(架构规范 Rule 8 / Rule 22)

本方案严格收敛到一个 Bot 形态 + 三个域。以下**不在本方案范围**,要么由上游 owner 统一推、要么属于他人边界:

- ❌ service bot / desktop bot 的任何改造(`ac_bots.bot_type in {service,desktop}`)。
- ❌ cron 从 `EngineManager` 的**整体调度所有权割离**(D5)——这是**跨所有 Bot 形态的上游动作**,依赖上游 owner 决策;本方案只承接 personal bot 的 cron relay 侧改造,整体割离作为"上游依赖"标注(§6.6)。
- ❌ 空间中台、通知/工单统一、approval 收敛(他人边界)。
- ❌ 本方案不含旧接口下线、不含 `ac_bots` 主表列改动。

**边界遵从**:cron relay 是 backend 自有模块;但"对 bot 加一层空间筛选"中的**空间实体本身归上游**。本方案在 cron/resources/identity 中通过新增 hook 端口引用空间身份,**不实现空间实体**,体现 Rule 22 Context Boundary 声明依赖、不反向侵入。

---

## 1. 架构规范基线

本方案严守以下规则(见 `docs/arch/arch.rules.md` + `AGENTS.md`):

| 规则 | 要求 | 在本方案的落点 |
|---|---|---|
| Rule 3 | Service API 以 Protocol 定义,独立于 delivery | 三块新增能力各起 Protocol,不止于 HTTP router |
| Rule 5 | 公共契约与实现分离 | 契约进 `core/<d>/protocols.py` 或 `contracts.py`,实现进 `service(s).py` |
| Rule 7 | core 不可 import transport/web;领域策略不落 adapter | 删除路径、空间筛选、工具权限**策略落 core**;adapter 仅翻译 |
| Rule 8 | 目录分层暴露架构角色 | 沿用现有 `api/`·`adapters/http/`·`core/`·`plugin_api/`·`plugins/`·`di/` |
| Rule 9 | 单一职责 | 一服务一域,不把资源/工具权限混进 cron |
| Rule 12 | cross-cutting(授权)走 hook/Protocol,不散落 | 空间筛选、删除保护、工具权限**统一经 hook 端口**,不在每个 service 内 if 散布 |
| Rule 14 | 装配由配置 + composition root 驱动 | 新协议在 `di/` 注入,不在 core 写 env 判断 |
| Rule 16 | 契约变更带 propagation | 新增端口 PR 含 propagation 段与本依赖说明 |
| Rule 19 | 抽象有界 | 只为本边界真有替换/隔离需求的点起 Protocol;不为个人偏好加 interface |
| Rule 20 | local 可跑、无外网 | 新增端口提供 local/rest noop 实现,纳入 singlebox |
| Rule 22 | context boundary | 每个新增/变更模块补 `README.md` + Context Boundary YAML(草稿见 §7) |
| Rule 25 | conformance | 新增 Service API 配 conformance 套件 |
| AGENTS.md Type Contract | `T \| None` 仅当 None 是契约内合法态;禁用 None 兜底掩盖缺失 | 消除现有 `bot.get("bot_type") or "personal"` 类兜底(§6.3) |

---

## 2. 现状架构范式(三块既有的合规组织)

三块现状本就符合架构规范分层,本方案沿用此范式做"新增",而非推翻:

### 2.1 资源(`core/resources`)
```
core/resources/
  README.md                 # Context Boundary(Rule 22,arch test 强校验 internal_dependencies)
  protocols?                # 通过 repository/protocol.py + service.py 暴露契约
  service.py / services/    # ResourceService + FileService(实现,R7 transport-agnostic)
  repository/protocol.py    # 持久化端口
  models.py
  adapters/http/resources/  # 薄 HTTP 翻译(R7)
```
- **Context Boundary 摘录**:`purpose: 文件上传/列举/删除,设备文件系统(NAS 路径解析);consumes: BotRepository/BotService/DeviceService/PassportPlugin`
- **现状能力**(PRD 可对齐已有):`FileService.upload_file/upload_files`(`file_service.py:377,469`)、`ResourceService.upload_file/upload_files`(`resource_service.py:302,377`)
- **PRD 增量缺口**:① 新建文件夹 ② 删除保护(对某些目录)③ 下载组合 ④ 文件夹形式上传 整合

### 2.2 定时任务(`core/cron`)
```
core/cron/
  README.md                 # purpose: "relay scheduled actions to bots via device service"
  protocols.py             # @runtime_checkable Protocol(BotInfoProvider 等)
  services/cron_relay.py    # CronRelay(实现)
  services/cron_runtime_operations.py / cron_runtime_targets.py
  adapters/http/cron/       # HTTP relay(9 端点, CollaboratorPermissionInterceptor)
```
- **Context Boundary 摘录**:`consumes: BotService/DeviceService;internal_dependencies: bot_management/devices/events/service_bot/kernel`
- **现状张力**(值得按规范修正):`cron_relay.py` 多处 `bot.get("bot_type") or "personal"`、`bot_type=bot.get("bot_type") or "personal"`(`cron_relay.py:248,588,702,820` + `cron_runtime_operations.py:66`)——**None 兜底掩盖缺失**,违反 Type Contract。按规范重构应在 bot 取数边界把 `bot_type` 设为必填,relay 不再做 fallback。
- **PRD 增量缺口**(personal bot 边界内):① "加一层空间筛选"(对个人 bot = 个人空间归属筛选)② 协作者可写 / 空间成员非协作者**只读**(现非协作者直接 403,缺 view-only 层)

### 2.3 调用身份(`core/caller_identity`)
```
core/caller_identity/
  protocols.py             # 4 个 @runtime_checkable Protocol + CallerIdentityTokenExchangeProtocol
  contracts.py             # CallerIamTokenContext / CallerIdentityStage(契约)
  credential.py / iam_token_service.py / repository.py / models.py / service.py
```
- **现状能力**:已有 `CallerIdentityStage`、`exchange_caller_identity`、`CallerMcpSyncProtocol`(`sync_mcp_identity_to_agent_principal`)等完整端口(MCP 身份同步、token 颁发/更新)。
- **PRD 增量缺口**:① #10 caller 身份调用一期(personal bot 的工具调用走 caller 身份)② #15 CLI 方式添加工具权限校验 ③ MCP 列表"调用身份切换"。被全仓 **20 文件**引用,触点广 → **必须纯新增端口,不动 20 处现引用点**。

---

## 3. 取向:新增非侵入(沿用)

- 既有 service/repository/adapter **不重写**;新能力走 `core/<d>/protocols.py` 新增 Protocol + `services/` 新增 service + `adapters/http/` 新增路由,旧路由照常。
- 数据**只加表/加列(可空)**,绝不删改既有;新增字段回填完成才进契约为非空。
- 不动 caller_identity 现有 20 处引用;新增工具权限校验经新 hook 端口,旧 `exchange_caller_identity` 调用点零改动。
- 旧接口不下线(本方案不含)。

---

## 4. 资源子方案

### 4.1 增量映射
| PRD 要求 | 现状 | 动作(新增) |
|---|---|---|
| 上传文件/文件夹 | `upload_file/upload_files` ✅ | 复用;补 folder 整体上传(走新 `upload_folder`) |
| 新建文件夹 | 隐含在 file 操作 | 新增 `create_folder`(new service 方法 + 端口) |
| 删除:对某些目录做保护 | 仅删除 ✅ | 新增 `DeletionGuard` hook(Rule 12 cross-cutting),按目录白名单拦截;**策略在 hook,不在 FileService** |
| 下载 | 散落 | 新增 `download` 组合端点(打包 / 流式方式在实现计划阶段确定) |

### 4.2 架构合规设计
- 新增 `core/resources/protocols.py::ResourcePolicyProtocol`(若已有 repository/protocol.py 则并入)承载 **删除保护策略** —— `can_delete(path, bot_id) -> Decision`。FileService 删除前调 hook;策略实现按"个人云端 bot 的保护目录白名单"(agentclaw **个人 bot** 的 NAS 受保护目录)在 `di/` 注入 local vs prod。
- 文件夹/下载增量为纯加法,落 `services/resource_service.py` 新方法 + `adapters/http/resources/` 新路由,**core 不 import transport**(R7)。
- 个人云端 bot 范围:仅 `bot_type == personal` 路径走 `workspace/path_factory.py` 的 personal 分支(已存在),不碰 service/desktop 分支。

> **待澄清**:PRD"对某些目录做保护"的目录清单与归属(是否含个人 bot 私有保护目录)——不影响架构,落 impl 前确认。

---

## 5. Cron 子方案(personal bot 边界)

### 5.1 增量映射(personal 范围)
| PRD 要求 | 现状 | 动作(personal 内新增) |
|---|---|---|
| 对 bot 加一层空间筛选 | CronRelay 列表无 space 维度 | 列表端点经 `SpaceScopeResolver` hook 注入 personal 归属空间,过滤 |
| 协作者可编辑/启用/禁用/删除/新建 | `CollaboratorPermissionInterceptor` ✅ | 复用电报改入统一 hook(见 §6) |
| 空间成员非协作者只读 | 非协作者直接 403 | **新增 view-only 只读层**:非协作者返回列表只读(不 403),通过新 `CronAccessPolicy` 决策 view/edit/deny |

### 5.2 架构合规设计
- 新增 `core/cron/protocols.py::CronAccessPolicyProtocol` —— `decide(principal, bot_id) -> {view,edit,deny}`。现 `CollaboratorPermissionInterceptor` **改为调它**(替换判定来源,不重写拦截框架),满足 Rule 12(集中)且非侵入。
- 空间筛选同样经 `SpaceScopeResolverProtocol`(新端口,实现在上游空间域,本边界只声明 consumes),cron relay 调它解析当前 principal 的 personal 空间约束。
- **消除 None 兜底**:`bot_type` 在 `BotInfoProvider.get_bot`(`protocols.py`)契约里设为必填字段,`cron_relay.py` 的 `or "personal"` 兜底删除,改为契约保证;这是按"正确架构规范"对现状的实质修正(Type Contract)。

### 5.3 边界守则
- 不改 `EngineManager`(engine 侧 lifecycle)——CronRelay 本就只 relay 到 device service,本方案不承接"调度所有权割离"。
- 不引入 service bot 的 cron 编排逻辑(`core/cron` 当前 `internal_dependencies` 含 service_bot 是既成耦合,本次不扩大也不清理,留待整体割离 owner 处理)。

---

## 6. Identity 子方案(personal bot 边界)

### 6.1 增量映射
| PRD 要求 | 现状 | 动作(纯新增端口) |
|---|---|---|
| #10 caller 身份调用一期 | `exchange_caller_identity`✅ | personal bot 工具调用经已有端口注入 caller 身份;新增"一期"开关经 hook |
| #15 CLI 添加工具权限校验 | 无 | 新增 `ToolPermissionPolicyProtocol` + CLI 注册工具权限校验 |
| MCP 列表"调用身份切换" | `CallerMcpSyncProtocol`✅ | 复用 `sync_mcp_identity_to_agent_principal` 暴露切换入口(新 adapter 路由) |

### 6.2 架构合规设计
- 新增 `core/caller_identity/protocols.py::ToolPermissionPolicyProtocol` —— `authorize(tool, caller, bot_id) -> Decision`(Rule 12 cross-cutting)。现有 `exchange_caller_identity` 等 **20 处引用零改动**;新工具权限校验在 personal bot 工具调用入口经 hook 插入,**不在 caller_identity.service 内散布 if**。
- caller 身份"调用一期"作为 `CallerIdentityStage` 的新阶段值(加法进 `contracts.py`),不在旧 stage 语义上叠语义(Rule 4)。
- MCP 调用身份切换:新增 adapter 路由复用 `CallerMcpSyncProtocol`,core 不感知 HTTP。

### 6.3 触点处理(守 Rule 19 / 24)
- caller_identity 被 20 文件引用 = constrained 面(Rule 17)。本方案**只 additive**:新协议、新阶段值、新 adapter 路由。不动既有 service/repository/20 引用点。
- 工具权限校验是 cross-cutting(Rule 19 例外:hook 协议可零初始实现起手)——允许先建 Protocol + permissive local impl, prod impl 后补。

---

## 7. Context Boundary 草稿(Rule 22,落地时写 README)

### 新增/变更:`core/cron`(变更,新增端口)
```yaml
purpose: "Cron / scheduled-job relay — relays scheduled actions to bots via device service."
provides:
  - "CronRelay service"
  - "CronAccessPolicyProtocol"      # 新增:协作者/非协作者 查看/编辑 决策端口
consumes:
  - "BotService"
  - "DeviceService"
  - "SpaceScopeResolverProtocol"    # 新增(上游空间域提供,本边界只声明)
internal_dependencies:
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.devices
  - agentclaw.community.core.events.bus
  - agentclaw.community.core.events.types
  - agentclaw.community.core.service_bot
  - agentclaw.community.di
  - agentclaw.community.kernel
  - agentclaw.community.log
  - agentclaw.community.utils.env_utils
```
**Change impact**:`CronAccessPolicyProtocol` 引入后,非协作者从"403"变"view-only";`bot_type` 必填化影响所有依赖 `get_bot` 的调用方(需 propagation 标注)。整体调度割离不在此模块变更内(上游依赖)。

### 新增/变更:`core/resources`(变更,新增端口)
```yaml
purpose: "Resource domain — file upload, listing, deletion against the device filesystem (with NAS path resolution)."
provides:
  - "ResourceService"
  - "FileService"
  - "DeletionGuardProtocol"         # 新增:删除保护策略端口(Rule 12)
consumes:
  - "BotRepository"
  - "BotService"
  - "DeviceService"
  - "PassportPlugin"
internal_dependencies:
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.devices
  - agentclaw.community.core.workspace
  - agentclaw.community.log
  - agentclaw.community.core.devices.services.device_filesystem
  - agentclaw.community.plugin_api.passport
```

### 新增/变更:`core/caller_identity`(变更,新增端口 + 阶段值)
```yaml
purpose: "Caller identity domain — IAM token exchange, MCP identity sync, tool permission for caller-scoped runs."
provides:
  - "CallerIdentityService"
  - "ToolPermissionPolicyProtocol"  # 新增:CLI/工具权限校验端口(Rule 12)
consumes:
  - "(existing IAM / MCP principals)"
internal_dependencies:
  - agentclaw.community.log
  # 其余按现状不变(doors 20 引用点不动)
```

> ⚠️ `internal_dependencies` 是 arch test 硬白名单(`tests/architecture/test_module_boundaries.py`):落地时确保新增端口未引入未声明 import。`consumes` 为意图记录,非机器校验。

---

## 8. 合规性对照(本方案如何守规范)

| 规则 | 具体落点 |
|---|---|
| Rule 3/5 | `CronAccessPolicyProtocol`/`DeletionGuardProtocol`/`ToolPermissionPolicyProtocol` 各为独立 @runtime_checkable Protocol,非 HTTP router |
| Rule 7 | 删除保护、空间筛选、工具权限三种策略均在对应 Protocol 实现里,hadn 文件 FileService/CronRelay/caller_identity.service 仅调 hook,不含 transport/框架 |
| Rule 11 | caller_identity 新阶段值配 lifecycle 声明(本地需 stage 实现) |
| Rule 12 | 三块 cross-cutting(权限/删除保护/工具校验)统一经 hook 端口,**不在 service 内散布 if** |
| Rule 14 | 三个端口在 `di/modules/` 注入 local/permissive vs prod 实现 |
| Rule 16 | 每个新端口 PR 含 propagation(`cron` `bot_type` 必填化影响通用调用方) |
| Rule 19 | hook 协议允许 zero-impl 起手(仅 ToolPermissionPolicy 需此例外) |
| Rule 20/21 | 三个 Protocol 提供 permissive local 实现,单机可跑,纳入 singlebox |
| Rule 22 | §7 Context Boundary 草稿,落地补 README |
| Rule 25 | `CronAccessPolicyProtocol`、`DeletionGuardProtocol` 配 conformance 套件 |
| Type Contract | 消除 `bot.get("bot_type") or "personal"` 类兜底,`bot_type` 在 `BotInfoProvider.get_bot` 设必填非可选 |

---

## 9. 上游依赖与待澄清(不阻塞本方案架构定稿)

| 项 | 性质 | 归属 |
|---|---|---|
| cron 整体调度所有权割离(D5) | 上游动作 | EE EngineManager owner;本边界只承接 relay 侧 |
| 空间实体 `SpaceScopeResolver` 实现 | 上游域 | 空间中台 owner;本边界只声明 consumes Port |
| "对某些目录做保护"目录清单 | impl 细节 | 确认后填 `DeletionGuard` prod 实现 |
| `bot_type` 必填化的 propagation(全 `get_bot` 调用方) | 跨模块 | 需 propagation review(Rule 16);个人边界内调用方先合规 |

---

## 10. 范围外(再次明示)

service / desktop bot 改造、空间中台、通知/工单统一、approval 收敛、cron 整体割离、旧接口下线、`ac_bots` 主表列改动、前端实现 —— **均不在本方案**。

---

## 11. 下一步

- 评审本聚焦版(确认 Scope 与新增端口的契约形态无误,尤其 §9 上游依赖切分)。
- 三块各自进 `docs/superpowers/plans/` 详细实现计划(本轮不产出代码)。
