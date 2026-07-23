# skills_pool

Skills Pool 控制面的 Bot 级布局状态、首次迁移认领和激活编排边界。
容器目录暗准备由镜像负责；本模块通过当前 runtime adapter 完成 probe、
OpenClaw 原子数据面切换与 Pool mapping，并在最后事务性提交 locator 和
`POOL_ACTIVE`。完整多引擎 Engine Layout Descriptor 仍不在本期范围内。

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
- 激活只处理已经认领且由当前 worker 持有 lease 的 OpenClaw Bot；每次执行
  都重新读取 Bot 和当前 provider binding，并重新 probe marker。
- 容器内以系统原生原子 exchange 将 Legacy local 切成指向 Pool canonical
  local 的永久单向 bridge；不支持原子 exchange 时保持 Legacy。
- 激活前同时核对已登记 local，并从文件系统枚举未登记 local、受管 active
  entry 与外部 entry；完整 local 内容进入 Pool，但不会为未登记内容创建
  数据库记录，外部 entry 保持原目标。
- 已登记源缺失/不可读或受管入口冲突时，在数据面切换前阻断并持久化失败码、
  阶段和独立证据；普通 probe 重试不会覆盖这份失败证据。
- 数据面切换后先全量发布并验证 Pool mapping，再在一个 CAS 事务中更新该
  Bot 全部 local locator 和 `POOL_ACTIVE`。mapping 或事务失败只前滚重试。
- 本模块当前只持久化尚未跨过 bridge 的结构性失败；bridge 已提交后的
  `POST_CUTOVER_SYNC_PENDING`、mapping 与数据库提交失败的分阶段恢复和
  审计持久化由 #376 的完整前滚恢复状态机承接，避免在 #370 重复定义状态。
- local 后置合并采用 best-effort 原子 exchange：一般的切换前修改、切换后
  Pool 修改和新增路径竞争均可收敛；无写栅栏时，跨 exchange 的已打开文件
  描述符或连续同文件写入仍存在极窄竞态，作为 #370 的显式接受限制。

## Context Boundary

```yaml
purpose: "Persist Bot Skills Layout state and atomically admit one Pool migration generation through a fail-closed rollout gate."
provides:
  - "SkillsPoolLayoutRepositoryProtocol"
  - "SkillsPoolRolloutGate"
  - "SkillsPoolMigrationClaimService"
  - "SkillsPoolReconcileService"
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
internal_dependencies:
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.common_config
  - agentclaw.community.core.skill_center
  - agentclaw.community.core.service_bot
  - agentclaw.community.core.base
  - agentclaw.community.plugin_api.database
  - agentclaw.community.core.skills_pool.ports
```

### Change impact

状态机或激活顺序变化会同时影响迁移 Worker、Bot locator 事务和运行时
mapping/bridge 实现；端口变化还必须同步 Backend runtime、skill
repository 实现及相应测试。
