# 待办工作台 A 侧能力

## 问题

实验室待办页已有产品对齐，但缺乏可持久化的 A 侧待办生命周期、B 事件投影和验收能力。前端不能绑定 A 私有的 execution/snapshot 概念，也不能绕过 B 直接执行 Bot。

## 目标

- 当前空间内的 Todo CRUD、搜索、筛选、分页和显式 Run。
- 将 Todo 与 B 生成的 `collab_context_id` 关联。
- 将 B 完整事件时序投影为公开 API，并展示产物名称/地址。
- 在 `pending_acceptance` 提交 Human 互动，由 B 作为事件持久化并推进重新执行。
- 保存不可覆盖的验收决策，并维护产品状态。
- 复用当前空间 Bot 工坊清单用于前端创建。

## 非范围

- 多 Bot、标签、团队知识库。
- 空间创建、邀请、成员管理。
- Context 组装/版本、事件排序、Bot 执行状态机，由 B 拥有。
- A 侧 Human 互动独立表。

## 已确认决策

- 创建为 `draft`，前端不自动 Run，用户显式启动。
- A 只保存 `collab_context_id`，不保存 Context 内容。
- Human 互动和验收都是 B 事件；A 互动接口是事件投影。
- 拒绝验收在 A 落状态为 `in_progress`，后续执行由 B 根据 B 事件推进。
- 当前 Bot 清单直接读取当前空间下的 Bot 工坊列表。
