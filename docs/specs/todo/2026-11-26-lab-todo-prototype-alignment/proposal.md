# 实验室待办第一批产品对齐 Proposal

> 状态：Confirmed
> 日期：2026-11-26

## 背景

实验室待办原型把执行方分为单 Bot 和 Bot 小队，并要求从 Dima 批量导入工作项。仓库已经存在 Todo Template/Version/Stage、Dima MCP source 和六状态 Backend 模型，因此第一批应在既有能力上形成产品闭环，而不是重新建模或迁移数据库。

## 目标

1. 提供 Bot 小队产品入口，V1 支持线性节点、revision 和归档。
2. 支持单 Bot/BotTeam 创建 Todo，并固定执行计划和负责人。
3. 支持最多 20 条 Dima 工作项单向批量导入，逐条返回结果。
4. Frontend 精确支持六状态并按原型调整 Todo、Dima、BotTeam 页面。
5. 在 Progress/Interaction 正式协议完成前只保留真实边界，不造假数据。

## 关键方案

BotTeam 作为产品 facade 复用 Template/Version/Stage 物理结构。`template_id` 等名称不符合“小队”不构成改表理由。本批无 DDL、无历史回填、无物理重命名、无旧 Template API 删除。

## 非范围

- Progress/Interaction 新协议；
- 自动化、通知、动作卡；
- Dima 回写；
- BotTeam DAG/并行/分支/循环；
- Template/Stage 物理清理；
- Todo 创建后编辑。

## 决策记录

| 日期 | 决策 |
| --- | --- |
| 2026-11-26 | V1 Todo 正文使用单一文本，复用现有 description |
| 2026-11-26 | Bot 小队只支持线性节点，发布 revision 不可变 |
| 2026-11-26 | Dima 只单向导入，一次至少满足 20 条批处理需求 |
| 2026-11-26 | 第一批只做 Dima、BotTeam 和可独立完成的页面改造 |
| 2026-11-26 | 产品使用 BotTeam 命名，物理层继续复用 Template/Stage；不因命名调整数据库 |
