# skills_pool

Skills Pool 控制面的 Bot 级布局状态、首次迁移认领和激活编排边界。
容器目录准备由镜像负责；本模块通过当前 runtime adapter 完成 probe、
受支持文件型引擎的原子数据面切换与 Pool mapping，并在最后事务性提交
locator 和 `POOL_ACTIVE`。当前已接入 OpenClaw、Claude Code、AICoding 与
Hermes；物理路径投影由 Engine Layout Descriptor 统一持有。

## 核心语义

- `(env, entity_id, bot_id)` 没有状态行时，等价于非持久化
  `LEGACY_ACTIVE`，新 Backend 不会改变既有 Bot。
- 首次认领同时写入 Pool 目标、初始阶段、唯一
  `migration_generation`、lease 和白名单审计证据。
- 白名单仅控制首次认领。认领成功后状态具有粘性，移出白名单不会撤销迁移。
- rollout 配置按环境隔离；缺失、禁用、读取失败、格式异常或通配配置均
  fail closed。`full_rollout_owners` 可在一个已晋级且已有验收批次的引擎内，
  按 owner 放开其未来新建和后续重启的全部 Bot；`full_rollout_engines`
  可逐引擎放开全环境，`enable_all=true` 则覆盖当前环境中全部已经人工晋级
  并验收的引擎。精确负对照优先于所有扩大规则，始终保持不认领。
  未晋级引擎始终拒绝，环境全量期间也禁止直接晋级新引擎。
- owner 和 engine 来自当前 Bot 记录；服务草稿还必须由当前
  `ac_bot_publish` DRAFT 记录证明。认领入口不接受调用方自报运行形态，
  ONLINE 服务与 Teclaw 不产生认领。
- lease 的认领、接管和续租都由数据库时钟与 generation fencing 保护。
- ARCA 存活和 BaaS 发布完成事件只做当前 binding 的基础身份校验并写入
  `skills_pool.reconcile` 持久化任务；事件处理器不读取 marker、不推送
  mapping，也不提交 locator。
- 这两个持久化交接是 required delivery：ARCA 在提交 ACTIVE 前入队，失败
  时保持 PENDING 并由下一次存活回调重投；BaaS 则由发布任务退避重试。
  ACTIVE 心跳不重复插入任务。
- 持久化任务以 `(env, entity_id, bot_id)` 为唯一 Bot 身份。事件中的
  sandbox、device 或 publish 标识只作为审计证据；每次执行都会重新读取
  当前 Bot，并由 runtime adapter 重新解析当前 provider binding。
- 未认领 Bot 每次唤醒都重新经过 rollout gate；一旦 generation 已持久化，
  后续任务只续租或接管同一 generation，不再因白名单移除而停止。
- `NOT_CAPABLE` 正常完成并保持 Legacy，暂时性错误交给任务队列指数退避，
  无效结构持久化失败证据并阻断；重复任务通过 generation/lease 收敛。
- 激活只处理当前仍可编辑、已经认领、引擎已显式接入且由当前 worker 持有
  lease 的 Bot；每次执行都重新读取 Bot 和当前 provider binding，并重新
  probe 对应引擎 marker。只有 probe evidence 明确声明
  `skills-pool-mapping-v2` 后才发送 logical mapping；旧或缺失 capability
  作为 `NOT_CAPABLE` 释放认领并保持 Legacy。OpenClaw、Claude Code 内置
  consumer 直接广告 v2；AICoding、Hermes 只有在具体 composition root
  已接入同一 resolver 后才显式广告，旧 consumer 继续保持
  `NOT_CAPABLE`/Legacy。未知引擎不回退到 OpenClaw。
- Backend 发送的 v2 mapping 只包含
  `(corpus, relative_path, link_name)`；Engine 使用本地 runtime context 和
  descriptor 投影 source/active target，并返回 locator evidence。Backend
  只校验并持久化 evidence，不按引擎重建 activation/publish/verify/reconcile
  的物理路径。
- 容器内先把 Legacy local 以 generation-scoped rename 移入隔离区，再执行
  best-effort 后置合并；随后发布并验证直接指向 canonical Pool 的 active
  mapping。持久化 `.pool-active` 的 `finalizing → active` 后，active root
  中的 local corpus bridge 必须退役；OpenClaw、Claude Code 位于 active
  root 内的 repo bridge 同时退役，AICoding、Hermes 位于 active root 外的
  稳定 repo namespace 则保留并继续只读指向 canonical Pool。
- 激活前同时核对已登记 local，并从文件系统枚举未登记 local、受管 active
  entry 与外部 entry；完整 local 内容进入 Pool，但不会为未登记内容创建
  数据库记录，外部 entry 保持原目标。
- 已登记源缺失/不可读或受管入口冲突时，在数据面切换前阻断并持久化失败码、
  阶段和独立证据；普通 probe 重试不会覆盖这份失败证据。
- 数据面切换后先全量发布并验证 Pool mapping，再在一个 CAS 事务中更新该
  Bot 全部 local locator 和 `POOL_ACTIVE`。mapping 或事务失败只前滚重试。
- bridge 结果未知时进入 `NEEDS_MANUAL_REPAIR`，停止普通自动重试；运维必须
  附带操作者、备注和已核验的数据面事实，之后才会重新入队同一 generation。
- bridge 已提交后的 `POST_CUTOVER_SYNC_PENDING`、mapping 与数据库失败均
  持久化阶段、错误码、可重试性、证据和时间；重试只补齐 mapping、locator
  与 `POOL_ACTIVE`，不恢复隔离副本。已提交状态的 runtime 重入只补齐
  quarantine/marker 证据，不重复执行数据面 commit CAS；transport 或校验失败
  只记录 forward-only failure。
- runtime probe 通过 `cutover_evidence_contract_version=quarantine-v1`
  声明激活响应能够返回 generation-scoped quarantine 证据。新 Backend 遇到
  未声明该能力的旧 runtime 时，只释放 `POOL_PREPARING/POOL_READY` claim
  并保持 Legacy；`POOL_ACTIVATING_PRE_CUTOVER` 已属于结果不确定区，必须继续
  幂等调用 cutover 探明事实。对于已跨界重试，允许旧响应复用 DB 中既有
  quarantine 身份。两边都缺少身份时记录可重试的 runtime 升级要求，不由
  Backend 推导引擎物理路径。
- 在递归版 OpenClaw 发布前的 Pool 独立 rollout 窗口，显式回滚先持久化
  `LEGACY_ROLLBACK_PREPARING` 作为 Bot 级编辑暂停状态，
  再从当前 Pool 全量重建新的 Legacy local 并提交切换。切换后即使 mapping
  或数据库失败也保持 `LEGACY_ROLLBACK_COMMITTED`，同一 generation 可由
  lease 过期后的新 worker 接管并继续提交 Legacy mapping 和 locator。
- Backend 上传、删除和显式回滚共用 Bot 级互斥锁；回滚阶段内的新 local
  编辑 fail closed。mapping 请求同时声明 `source_layout`：激活缺省为
  `pool`，显式回滚前后使用 `legacy`。显式回滚也必须在第一次 logical
  mapping 请求前重新 probe 当前 binding；若运行时已降级或缺少 v2
  capability，则不发送 v2、不执行文件系统切换，并记录可重试失败。
- 显式回滚不会读取迁移隔离副本，Pool 激活后产生的 local 新增和修改会被
  带入新的 Legacy；Pool 本身保留用于证据和后续恢复。
- local 后置合并采用 best-effort、无覆盖的文件级收敛：一般的切换前修改、
  切换后 Pool 修改和新增路径竞争均可收敛；无写栅栏时，跨 rename 的已打开文件
  描述符或连续同文件写入仍存在极窄竞态，作为 #370 的显式接受限制。
- 原子切换留下的旧 local 以 Bot 与 migration generation 独立登记为
  Migration Quarantine，只用于审计和人工取证，不参与 locator、mapping、
  日常读写或自动回滚。
- 隔离内容至少保留到 `POOL_ACTIVE` 七天后，并且必须观察到激活后的 ARCA
  新 sandbox 存活交接或 BaaS 成功重启/重发及 reconcile；失败、人工修复和
  显式回滚阶段都阻断清理。
- 七天任务由持久化任务队列延迟调度；重复执行、任务接管和目录已不存在均
  幂等。容器只接受 generation，由固定 engine Pool 根推导删除目标，不能
  删除其他 Bot 或 generation。数据库保留清理时间与证据。
- 灰度运维入口接受当前环境中的精确 `(owner_id, bot_id)`；单个已晋级引擎
  完成批次验收后，可引用最近一次验收通过
  `POST /rollout/owners` 按 `(owner_id, engine)` 放开该员工全部未来认领；
  完成批次验收且无负对照后，可写入 `full_rollout_engines` 单独全量；
  所有已晋级引擎均满足条件后，可显式打开或关闭环境级 `enable_all`。
  引擎按 OpenClaw、Claude Code、AICoding、Hermes 的固定
  顺序人工晋级；每次扩大同引擎批次必须引用最近一次已冻结验收，除首个
  引擎外，晋级还必须引用上一引擎已冻结的验收批次。配置写入使用完整旧值
  加逻辑 revision CAS，冲突 fail closed；缺少可选默认字段的旧配置按规范化
  语义参与 CAS，并在首次成功写入时原子升级为完整形状；配置和包含前后
  revision、原因及验收快照的独立审计事件在同一事务提交。
- 单 Bot 运维视图合成 engine、provider、runtime form、rollout 决策、
  layout/generation、probe/failure 和 quarantine 证据；批次视图只有在
  全部 eligible Bot 激活、无失败且负对照与 Teclaw 对照均健康时才报告
  `promotion_ready`，但不会据此改写灰度配置。
- 递归版 OpenClaw 属于后续独立发布；该版本开始后，已经使用递归扫描器的
  Bot 只允许向 Pool 前滚，不再提供物理 Legacy 回滚组合。
- 递归版 OpenClaw 发布前还必须补齐实际 OpenClaw Gateway 的业务 readiness
  门禁：正常迁移保持异步；若 preparation/probe/activation 未收敛且 active
  root 仍不安全，则实例进入 unhealthy、告警并由既有持久化任务重试，禁止
  以“递归引擎 + Legacy corpus 入口”持续 serving。该门禁不能只加在 Backend
  adapter 的 `/readiness`，否则无法覆盖直接访问 Gateway 的流量。
- 人工 wake/retry 通过持久化任务队列交接，repair/rollback 复用既有恢复
  服务；所有写入口仅对 operator 开放。

## Context Boundary

```yaml
purpose: "Persist Bot Skills Layout state and atomically admit one Pool migration generation through a fail-closed rollout gate."
provides:
  - "SkillsPoolLayoutRepositoryProtocol"
  - "SkillsPoolRolloutGate"
  - "SkillsPoolMigrationClaimService"
  - "SkillsPoolReconcileService"
  - "SkillsPoolReconcileTaskHandler"
  - "SkillsPoolReconcileWakeupListener"
  - "SkillsPoolRecoveryService"
  - "SkillsPoolRollbackService"
  - "SkillsPoolQuarantineService"
  - "SkillsPoolQuarantineCleanupTaskHandler"
  - "SkillsPoolRolloutOperations"
  - "SkillsPoolOperationalQuery"
  - "SkillsPoolOperatorCommands"
  - "SkillsPoolRolloutRepositoryProtocol"
  - "QuarantineRepositoryProtocol"
  - "QuarantineRecord, QuarantineStatus, QuarantineEligibility and QuarantineOperationalView"
  - "RuntimeQuarantineCleanupResult"
  - "PoolCutoverStatus"
  - "PoolCutoverResult"
  - "PoolSkillMapping logical intent"
  - "BotSkillLayoutState and migration enums"
consumes:
  - "BotRepository"
  - "BotPublishRepositoryProtocol"
  - "CommonConfigService and CommonWhiteListService"
  - "DatabasePlugin (through Skills Pool layout, operational and rollout repositories)"
  - "SkillsPoolRuntimeProtocol"
  - "SkillsPoolSkillRepositoryProtocol"
  - "skills-pool-mapping-v2 capability and Engine locator evidence"
  - "DeviceBindingRepository"
  - "TaskQueueService and HandlerRegistry"
internal_dependencies:
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.common_config
  - agentclaw.community.core.devices
  - agentclaw.community.core.events
  - agentclaw.community.core.skill_center
  - agentclaw.community.core.service_bot
  - agentclaw.community.core.task_queue
  - agentclaw.community.core.base
  - agentclaw.community.kernel.lifecycle
  - agentclaw.community.log
  - agentclaw.community.plugin_api.database
  - agentclaw.community.core.skills_pool.ports
```

### Change impact

状态机或激活顺序变化会同时影响迁移 Worker、Bot locator 事务和运行时
mapping/bridge 实现；端口变化还必须同步 Backend runtime、skill
repository 实现、Engine `SkillsService` consumers 及相应 contract tests。
