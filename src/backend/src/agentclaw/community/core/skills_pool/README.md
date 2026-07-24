# skills_pool

Skills Pool 控制面的 Bot 级布局状态、首次迁移认领和激活编排边界。
容器目录准备由镜像负责；本模块通过当前 runtime adapter 完成 probe、
受支持文件型引擎的原子数据面切换与 Pool mapping，并在最后事务性提交
locator 和 `POOL_ACTIVE`。当前已接入 OpenClaw、Claude Code 与 AICoding；完整多引擎
Engine Layout Descriptor 仍不在本期范围内。

## 核心语义

- `(env, entity_id, bot_id)` 没有状态行时，等价于非持久化
  `LEGACY_ACTIVE`，新 Backend 不会改变既有 Bot。
- 首次认领同时写入 Pool 目标、初始阶段、唯一
  `migration_generation`、lease 和白名单审计证据。
- 白名单仅控制首次认领。认领成功后状态具有粘性，移出白名单不会撤销迁移。
- rollout 配置按环境隔离；缺失、禁用、读取失败、格式异常、`enable_all`
  或通配配置均 fail closed。
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
  probe 对应引擎 marker。未知引擎不回退到 OpenClaw。
- 容器内以系统原生原子 exchange 将 Legacy local 切成指向 Pool canonical
  local 的永久单向 bridge；不支持原子 exchange 时保持 Legacy。
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
  与 `POOL_ACTIVE`，不恢复隔离副本。
- 显式回滚先持久化 `LEGACY_ROLLBACK_PREPARING` 作为 Bot 级编辑暂停状态，
  再从当前 Pool 全量重建新的 Legacy local 并原子交换。交换后即使 mapping
  或数据库失败也保持 `LEGACY_ROLLBACK_COMMITTED`，同一 generation 可由
  lease 过期后的新 worker 接管并继续提交 Legacy mapping 和 locator。
- Backend 上传、删除和显式回滚共用 Bot 级互斥锁；回滚阶段内的新 local
  编辑 fail closed。mapping 请求同时声明 `source_layout`：激活缺省为
  `pool`，显式回滚前后使用 `legacy`。
- 显式回滚不会读取迁移隔离副本，Pool 激活后产生的 local 新增和修改会被
  带入新的 Legacy；Pool 本身保留用于证据和后续恢复。
- local 后置合并采用 best-effort 原子 exchange：一般的切换前修改、切换后
  Pool 修改和新增路径竞争均可收敛；无写栅栏时，跨 exchange 的已打开文件
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
  - "QuarantineRepositoryProtocol"
  - "QuarantineRecord, QuarantineStatus, QuarantineEligibility and QuarantineOperationalView"
  - "RuntimeQuarantineCleanupResult"
  - "PoolCutoverStatus"
  - "PoolCutoverResult"
  - "BotSkillLayoutState and migration enums"
consumes:
  - "BotRepository"
  - "BotPublishRepositoryProtocol"
  - "CommonConfigService and CommonWhiteListService"
  - "DatabasePlugin (through plugins/skills_pool_layout_repository.py)"
  - "SkillsPoolRuntimeProtocol"
  - "SkillsPoolSkillRepositoryProtocol"
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
repository 实现及相应测试。
