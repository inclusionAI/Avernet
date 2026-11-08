# OpenAPI 个人 Bot 异步回收：最小改造方案

状态：2026-11-08 已完成实现与本地自动化回归；部署及真实容器恢复验收待完成。本文记录设计及实现差异，不代表已上线。

## 1. 需求与设计取舍

**决定：保留一个回收提交入口，新增一个专用查询入口；复用现有数据库任务队列与 Bot.ext.recycle，不建表，不修改重启/status 查询，不把恢复接口整体任务化。**

本次仅解决：长备份不占用 HTTP 请求、回收结果可查、失败不误伤运行中的 Bot、恢复不与未完成回收冲突。不是建设通用生命周期平台。

| 复用 | 本次新增/调整 |
| --- | --- |
| ac_task_queue 的持久化、活动去重、续租和重投 | 一个 bot.recycle handler 及注册 |
| Bot.ext 和现有事务行锁 | ext.recycle 当前/最近一次操作记录 |
| coding 引擎既有备份策略和 fastdisk_fail_block | 给备份传稳定 operation_id 和可恢复等待预算 |
| 现有 provider 销毁及状态查询能力 | 回收专用的“释放提交/确认”，不得误报成功 |
| 现有恢复逻辑与短锁、数据库真实事务 | 必要的回收互斥、条件更新、回收完成落库 |

不做：新表/DDL、新 MQ、outbox、通用状态机引擎、全局 Bot 新状态、历史任务列表、回收取消接口，以及 activate 的持久化队列迁移。保留将来扩展空间，不提前实现。

### 实现基线与差异

实现以 Avernet `dev@43c06b1c` 为基线。提交前已核对官方 GitHub `dev`，
该基线仍在其祖先链上；宿主与社区仓库的交付版本应在发布时配套验证。

既有 `release_device` 会吞掉物理释放异常，因此新增窄的 submit/observe hooks，未修改历史释放成功语义。
`Bot.ext.recycle` 的写入实际采用已有 `transactional_orm_session` 下的 Bot 行锁及 operation/phase 条件检查；
合并 ext、收尾和审计均在真实事务内，不额外新增 CAS/版本列。重启日志仍使用原 ext-only CAS。
备份内部记录为平铺的 `backup_started_at`、`fail_block`、`fastdisk_targets`，而非另建 backup 子对象。
恢复受理使用同一 Bot 行锁原子 claim；既有 daemon 启动模式未迁移。

## 2. HTTP 契约与数据存储

### 提交：保留原路径

```http
POST /openapi/v1/bots/{bot_id}/recycle
```

无 Body；保留 entity_id 与 deprecated owner_id 别名，二者冲突仍为 422。仅个人 managed-cloud Bot，排除 Teclaw；找不到/无权访问仍隐藏为 404。

| 场景 | HTTP 与结果 |
| --- | --- |
| 新任务已持久化，Bot 原为 ACTIVE | 202；operation_id、phase=QUEUED、changed=true |
| 同一 Bot 已有未结束回收 | 202；返回同一 operation_id，changed=false |
| 已回收且没有未结束操作 | 保持 200、status=RECYCLED、changed=false，不备份、不销毁、不重复审计 |
| 不可受理状态/其他生命周期操作占用 | 409；不入队 |
| 队列不可用或提交不能确认 | 503；不假报成功，可重试；已提交的任务按去重键认领 |

查询现存操作/冲突的优先级高于单独依据 Bot.status 返回幂等结果。202 只表示受理，不代表回收完成；changed 表示本次新建了回收操作。

受理响应 data 示例：

```json
{
  "bot_id": "bot-xxx",
  "owner_id": "owner-xxx",
  "status": "ACTIVE",
  "changed": true,
  "operation_id": "op-xxx",
  "phase": "QUEUED"
}
```

### 查询：独立入口，不复用原 /status

```http
GET /openapi/v1/bots/{bot_id}/recycle-operations/{operation_id}
```

沿用提交接口的 owner 解析和 OWNER 权限限制，不能因为已有 status 查询支持协作者，就让协作者读取回收错误。校验 tenant、env、app、owner、Bot 与 operation_id；不匹配一律 404。

查询成功 HTTP 200，即使任务失败；GET 不执行备份、销毁、补偿或状态推进。仅投影允许公开的字段，不能返回原始 ext、内部任务 payload、SDK 异常或凭据。降级继续回收时可返回安全的 warning（code/message），不能用成功覆盖备份风险。

```json
{
  "data": {
    "bot_id": "bot-xxx",
    "owner_id": "owner-xxx",
    "operation_id": "op-xxx",
    "bot_status": "ACTIVE",
    "phase": "FAILED",
    "error_code": "BACKUP_FAILED",
    "message": "备份失败，本次回收已终止，未释放资源"
  }
}
```

首期只支持当前/最近一次回收。旧 operation 被下一次有效回收替换后查询为 404，不承诺历史操作 API。完成回收后执行 activate 不清空/覆盖回收结果。

### 持久化：只用 ext.recycle

```json
{
  "recycle": {
    "operation_id": "op-xxx",
    "task_id": 123,
    "phase": "BACKING_UP",
    "target": {
      "binding_id": 456,
      "provider": "arca",
      "device_id": "device-xxx",
      "sandbox_id": "sandbox-xxx"
    },
    "backup_started_at": "<first-start-time>",
    "fail_block": true,
    "fastdisk_targets": [],
    "release": null,
    "error_code": null,
    "message": null
  }
}
```

示例省略作用域、引擎、原始请求/source、deadline 与时间戳等必要内部元数据。BaaS 的 target 应记录实际物理目标集合和 provider 业务身份；没有绑定的合法历史场景按现有策略处理，不伪造目标。

业务阶段唯一事实来源是 ext.recycle.phase。队列 payload 只保存不可变的提交输入、operation_id、目标快照和租户上下文，以支持 enqueue 后崩溃时补建日志；不在 payload 中维护第二套业务进度。不保存 credentials。

更新使用现有事务会话锁住 Bot 行，合并当前 ext 后只改变 recycle 节点，并校验 operation_id 与预期 phase；无须额外 version 列或重复一套通用锁表。

## 3. 执行、失败与恢复

### 最小状态流转

```text
QUEUED -> BACKING_UP -> RELEASING -> SUCCEEDED
             |               |
             v               v
           FAILED     NEEDS_CONFIRMATION
```

RELEASING 包括资源确认和最终收尾；不另设 FINALIZING。普通无备份引擎可直接从 QUEUED 进入 RELEASING。

| 阶段/结果 | 含义和约束 |
| --- | --- |
| QUEUED / BACKING_UP | 尚未提交资源释放；不改变 Bot 运行状态、绑定或 Passport |
| RELEASING | 已通过释放前检查并持久化释放意图；此阶段直到收尾完成都禁止恢复/重启等冲突操作 |
| SUCCEEDED | 旧目标已确认释放，冻结已尝试，Bot 状态和审计已提交 |
| FAILED | 尚未发生释放的确定失败；备份阻断时 Bot 保持原状态，通常为 ACTIVE |
| NEEDS_CONFIRMATION | 释放已发起/可能发起但最终结果未知；不能自动恢复运行状态、解锁给新操作或盲目重发销毁 |

FAILED 不是 Bot.status=FAILED。不写启动失败字段 start_status/start_message，不额外写入 Bot 启动失败状态。容器备份脚本自身的暂停/归档行为沿用重启实现，不承诺业务进程零影响。

### 受理与队列恢复

1. 只做本地权限、状态、目标快照读取；在现有同 Bot 短锁下重新检查回收/重启冲突。受理不访问容器、不等待备份。
2. 使用现有 enqueue，按 tenant+owner+Bot 身份生成活动去重键；env/app 沿用队列现有作用域。先持久化队列，再用返回的真实 task.payload 初始化 ext.recycle。
3. 模仿现有重启 ensure：提交者与 handler 均可在事务行锁下补建日志；只接受匹配的 Bot/目标；迟到提交者不得覆盖更新的 task_id/operation_id。补建失败时任务不得执行释放。
4. 返回 202 前确保任务已提交、查询可见；若提交者在入队后退出，worker 可补建。若队列记录已提交但日志落库失败，返回服务错误，重试认领原任务，不创建重复操作。
5. handler 每次重入检查 phase、operation_id 和目标。重投复用同一备份 ID 与原 deadline；利用现有 on_give_up 在队列放弃时写回：尚未发起释放为 FAILED，可能已释放为 NEEDS_CONFIRMATION。若回调落库失败，后续提交先核对关联任务并补偿原操作，不直接创建新操作；GET 始终只读。

不为“队列+ext 两次提交”引入新 outbox/通用事务框架；利用已存在的持久任务及可修复日志模式。短锁不跨越整个备份，长操作靠持久 phase、CAS 及目标检查保护，而非把 120 秒短锁当长期互斥保证。

### 备份：复用策略，不复用重启执行器

1. 继续使用现有引擎策略和 RestartBackupDependencies；其他引擎保持原来的 no-op 预检查。
2. 给策略传 operation_id 和首次备份开始时间/剩余预算；用最小可选参数/回调保存本次已确认快盘及降级 warning，不依赖只面向重启的 warning 保存上下文，也不注入有重启副作用的 current_restart/RestartExecution。
3. 已确认快盘仍用 300 秒等待预算，其他适用路径保留 1500 秒；首次执行读取 fastdisk_fail_block 并保存本次策略，重投不能重置计时或遗忘已确认快盘。配置语义、partial_success 规则与 dev 重启保持一致。
4. 开启阻断时，失败/超时写 FAILED，保留原容器、绑定、状态、Passport，不写成功回收审计；未开启时按既有策略降级并保存安全 warning。目标变化无论配置如何都不得释放新目标。
5. 释放前重新验证完整目标身份和备份凭据；不仅比较 binding_id。备份凭据可以在内存中重建验证器，不能把 callable 当作可恢复状态持久化。

300 秒是等待预算，不是强制终止容器中的 900 秒脚本。回收因备份阻断而失败后，即使 worker 晚到成功，也不能自动继续回收；新请求仍须遵守 helper 的既有互斥。

### 释放：一个明确的不可逆执行点

通过事务内条件更新从 BACKING_UP（或无备份的 QUEUED）进入 RELEASING，保存精确目标和稳定释放 request ID。只有赢得这次转换的执行者可以首次调用销毁。恢复中的其他投递先查询这次释放，不重新调用 stop_bot。

回收路径增加一个窄的 provider 释放/查询 Adapter，复用已有 ARCA/BaaS 能力，不复制容器创建或启动逻辑。不能使用“吞异常、清空绑定”的 legacy stop_bot 链路来确认成功；优先不改变所有历史 stop/restart 的释放语义。

ARCA 查询精确 sandbox，BaaS 查询本次销毁 workflow/request 及对应物理目标。逻辑绑定 RELEASED、HTTP 200、审批已提交或列表暂时为空，都不能单独证明物理销毁完成；查询异常也不能当作 not-found/已销毁。

重入 RELEASING：先对账。明确已释放则只补收尾；若不能证明未发生释放，即使 provider 报错也不回退为可重试 FAILED；仍无法判定则继续有限查询，预算耗尽后 NEEDS_CONFIRMATION。没有 provider 幂等保证，不自动重放销毁，不能根据旧租约过期认定原执行已停止。

释放确认窗口建议复用现有 provider 观察习惯取 600 秒；这是新增的回收观察预算，不冒充现有线上硬超时。任务总 deadline可沿用重启的 24 小时，二者都不是 HTTP 超时，也不会强制取消阻塞中的外部调用。使用原有 Reschedule 做查询，不新建调度器；预算结束后的 NEEDS_CONFIRMATION 交现有运维流程核实，不新增人工确认 API。

### 收尾：不引入新表也能避免重复审计

确认释放后，当前回收执行者尝试冻结 Passport；失败记录 warning，保留 best-effort 语义。用 ext 中一次性的收尾执行权和已完成检查点区分“尚未执行”与“上次可能仍在执行”；重复投递不重放冻结，尤其不能在 SUCCEEDED 或已进入恢复后再次冻结。只有持有收尾执行权的执行者提交最终事务；若旧执行结果未知且无法安全接管，保留占用并转 NEEDS_CONFIRMATION，不靠租约时间猜测它已停止。外部调用不能仅靠本地 CAS 获得 exactly-once 或强制取消语义，不能作此承诺。

在回收仓储中增加一个专用 finish 方法，使用现有 transactional_orm_session：锁定精确 Bot 行，校验 operation_id、预期阶段和目标，原子完成“绑定清理、Bot.status=RECYCLED、审计插入、ext.recycle.phase=SUCCEEDED”。审计复用现有 ac_bot_dormant_check_audit，以 operation_id 作 run_id、source=openapi；同一事务内检查和提交，以 Bot 行锁/条件更新串行化。

若事务失败，整体回滚，任务只重试收尾，不重新备份/销毁。已 SUCCEEDED 的重复投递直接结束，不重复插入审计。不要继续在 HTTP handler 根据 changed 写审计，也不要把 202 受理当作回收已发生。正常回收不 enqueue recycle 钉钉通知。

这是一个域内仓储方法，不是新通用事务框架。不能使用普通 orm_session 声称跨表原子性，也不能简单以“查过不存在”替代并发安全。

## 4. 恢复接口及其他操作的必要联动

保留 `POST /openapi/v1/bots/{bot_id}/activate` 的 URL、响应模型和现有异步启动方式；不在本次新增恢复 handler、恢复查询或 ext.activate 状态机，不再做一次备份。

在共享 ActivateBotService 中补保护，让 OpenAPI、页面、运维入口一致生效：

| 回收/Bot 情况 | 恢复行为 |
| --- | --- |
| 回收处于 QUEUED / BACKING_UP / RELEASING | 409；检查优先于 ACTIVE 幂等返回 |
| NEEDS_CONFIRMATION | 409；先核实旧资源，不能创建新资源 |
| 备份失败且 Bot 仍 ACTIVE | 原有 200、changed=false；不重新启动 |
| 回收 SUCCEEDED 且 Bot RECYCLED，或没有新日志的历史 RECYCLED | 正常恢复；不强制所有历史 Bot 都有 recycle 记录 |
| 已 REACTIVATING | 保持原有 202、changed=false；不重复分配 |

恢复受理在同 Bot 事务行锁内重新检查回收记录和 Bot 状态，使用预期状态检查和同事务更新取得本次启动权，不能“先查再无条件更新”。失败回滚只允许作用于该次恢复的预期状态/绑定；不能冻结或覆盖后续已启动的新绑定。不得把一次 CAS 写 ext 再一次无条件写 status 当作原子受理。

同样的回收占用检查接入 restart/delete 及实际会冲突的释放入口，并在外部副作用前再次校验；重启也要在回收入队前可被识别，使用已有 coding_restart 日志和短锁，不新增通用 lifecycle_operation 表/框架。同步内部/定时回收保持同步契约，但须遵守同一回收占用保护，不能绕过进行中的 OpenAPI 回收。

恢复继续使用原启动链路；核验 owner/Bot 存储身份不变、备份确实被该启动路径恢复。若发现缺失，只修该存储接入点，不建设新的恢复编排。持久化 activate 与它已有的 daemon 可靠性缺口是独立议题，不作为本需求的强制依赖。

## 5. 实施范围与验收

| 修改组 | 最小落点 |
| --- | --- |
| HTTP 契约 | dormant router/schemas/公开 Protocol；独立 GET、POST 202、权限与脱敏；原 BotStatus 不改 |
| 回收编排 | bot_dormant 内回收任务与 ext 状态辅助代码；DI/生命周期注册一个 handler，不创建新顶层平台 |
| 现有能力适配 | 备份稳定 ID/预算、窄释放/确认 Adapter、域内事务 finish；外部 SDK 细节留在既有 Adapter/Composition Root |
| 生命周期保护 | ActivateBotService 与 restart/delete/同步回收的必要检查；不重写这些操作 |
| 契约落地与验证 | OpenAPI 文档、Gateway scoped schema pin、两端 Protocol/仓储测试、竞态和恢复测试 |

验收分五组；自动化测试覆盖事务、HTTP、provider mock 和故障注入，真实容器的数据恢复验收仍待执行：

1. **接口与兼容**：备份阻塞超过 120 秒时 POST 已返回 202；同 Bot 并发提交只认领一个操作；保留 422/404/409；已回收 200 且无重复审计；独立 GET 只读且 OWNER/tenant/env/app 隔离，旧 operation 被替换后不串结果。
2. **备份策略**：成功、partial_success、阻断开启/关闭、配置读取降级、超时、helper 仍运行、重投同 ID/同预算、目标被替换；阻断失败不释放、不冻结、不改变 Bot.status、不写启动错误和成功审计。
3. **释放安全**：底层销毁抛错不假成功；BaaS 仅受理/审批不算完成；响应丢失、进程退出、重复投递、锁过期时不误删新目标；未知结果有明确 NEEDS_CONFIRMATION 且继续保护 Bot。
4. **收尾一致性**：物理释放后/冻结后/事务提交前后注入故障；重复执行只补收尾；真实事务回滚和并发最终仅一条本次审计；冻结明确失败仅 warning；无 recycle 通知。
5. **恢复与范围**：回收未结束时 activate 拒绝；历史 RECYCLED 可恢复；备份失败的 ACTIVE 仍可用；并发 activate 不双启、旧回滚不覆盖新绑定；既有重启/status/内部定时调用契约不变；验证备份数据随正常启动恢复。

实现顺序：先补接口与任务日志/恢复，再接备份和释放确认，最后补事务收尾与 activate 联动，统一跑上述契约和竞态回归。对外切换 202 必须通知调用方；发布期间在入口暂时关闭 recycle POST，部署至所有 worker 都认识 bot.recycle 后，再开放提交并同步 Gateway schema，避免旧 worker 将新任务判成未知类型；不新增业务开关或改变备份失败配置。

交付结论：**两个 HTTP 入口（改一个、加一个），一个队列 handler，一个 ext 节点；保留现有 Bot 状态和恢复模式，只补能证明回收安全完成的必要代码。**

## 6. 实现中的并发保护

- `RestartState.ensure` 的原 ext CAS 会核对 recycle 占用：回收先提交日志时，迟到重启只写本次重启失败记录，不修改 Bot 运行态。
- 旧 stop/delete 在释放前取得既有 restart 短锁，并在锁内重读 recycle；stop 以 PENDING 退出，delete 移除 live row，后来的回收无法受理。重启已持有的锁通过局部上下文借给 stop，避免二次获取。
- 回收只能回收自己持有者标识的过期短锁，不回收可能正在等待 provider 的历史释放/重启锁；历史遗留锁仍按原运维/重启流程处理。
- 恢复线程创建/启动失败时撤销未分配资源的 claim；有新绑定时旧错误回调不冻结 Passport、不覆盖 Bot。
- 不承诺取消 SDK 调用、外部副作用 exactly-once、历史任务查询，或已完成真实容器端到端恢复验收。

## 7. 本地验证记录

- 扩大回归：5,631 项通过、2 项跳过，覆盖相关 Bot 管理、设备、OpenAPI 与架构检查。
- 最终改动的定向回归：482 项通过，包含真实 SQLite 写事务、并发投递和审计去重。
- 企业宿主 ARCA 适配：26 项通过；Gateway schema/生成：25 项通过。
- 增量 Ruff 无新增问题，`git diff --check` 通过。完整既有 lint 仍有未改动行上的历史问题。
- 未执行真实容器的备份、销毁及数据恢复验收；外部 SDK 的行为仍需预发验证。
