# 进化控制面作为新服务 `apps/evolution` 运行

> English version: [0021-evolution-control-plane-in-apps-evolution.md](0021-evolution-control-plane-in-apps-evolution.md)

状态：accepted（2026-10-16；原为 bot-evolution 待定决策 D-1，方案 A）。负责人：@totalfrank，已签字确认。

## 决策

进化控制面（进化运行编排、Strategy Registry、经验、验证、实验记录和元进化）
放在新的 Python 服务 `apps/evolution` 中，采用
[`docs/arch/arch.rules.md`](../arch/arch.rules.md) 要求的 Backend DI 与插件模式
（`plugin_api`、组合根、一致性测试）。

基因组和晋升保留在 Backend，因为它们属于期望状态，且晋升必须紧挨着 Manifest
apply 与发布（[ADR 0019](0019-bot-genome-is-the-unit-of-evolution.zh-CN.md)、
[ADR 0020](0020-promotion-is-platform-owned.zh-CN.md)）。引擎适配器拥有物理投影、
记忆导入/导出和会话导出。迁移期间 ClawWeb 保留为 UI，并作为 ClawEvolve 进化
策略的宿主。

设计：[`apps/backend/docs/bot-evolution/design.zh-CN.md` §6](../../apps/backend/docs/bot-evolution/design.zh-CN.md#6-归属与模块放置)。

## 影响

- 新增一个可部署单元，需要 singlebox 接线、本地 profile（SQLite、进程内进化
  策略）以及自己的 CI 与 pre-push 门禁（工作项 RSI-08）。
- 长时、模型密集、突发性的工作独立于 Backend 请求服务进行扩缩容和故障隔离。
- `apps/evolution` 只通过 Backend 的契约访问基因组与晋升，从不访问 Backend
  内部实现；公开的 `/openapi/v1` 接口面把每条进化路径路由到提供它的服务。

## 备选方案

- **放在 Backend 内，作为 `core/evolution/`。** 已否决：长时模型作业会运行在
  处理请求、且已非常庞大的 Backend 中。
- **升级 ClawWeb 的 TS 控制面。** 已否决：不在宪章的 DI、插件和一致性工具范围
  内；仅限 Node；与 UI 混在一起。
