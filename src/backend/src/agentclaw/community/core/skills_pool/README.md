# skills_pool

Skills Pool 控制面的 Bot 级布局状态与首次迁移认领边界。本模块只建立
durable state、rollout gate 和 CAS/lease；不负责容器目录准备、运行时
probe、mapping、locator 切换或完整 Engine Layout Descriptor。

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

## Context Boundary

```yaml
purpose: "Persist Bot Skills Layout state and atomically admit one Pool migration generation through a fail-closed rollout gate."
provides:
  - "SkillsPoolLayoutRepositoryProtocol"
  - "SkillsPoolRolloutGate"
  - "SkillsPoolMigrationClaimService"
  - "BotSkillLayoutState and migration enums"
consumes:
  - "BotRepository"
  - "BotPublishRepositoryProtocol"
  - "CommonConfigService and CommonWhiteListService"
  - "DatabasePlugin (through plugins/skills_pool_layout_repository.py)"
internal_dependencies:
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.common_config
  - agentclaw.community.core.service_bot
  - agentclaw.community.core.base
  - agentclaw.community.plugin_api.database
```
