# 待办模板、阶段执行与生命周期重设计 Spec

> 状态：Confirmed
> 日期：2026-11-20

## 概述

本变更把 Todo 从“单 Bot 一次执行”升级为“固定模板版本 + 创建时参与角色绑定 + Bot/Human 线性阶段实例”。Todo 产品状态由领域状态机投影，不直接镜像 Runtime attempt，也不允许任意修改。

## 需求列表

### REQ-1：六状态 Todo 生命周期

- **AC-001**：Todo 资源只使用 `not_started | in_progress | interrupted | pending_action | completed | cancelled`；`not_started` 仅表示从未启动，Todo 一旦启动不得回退，Bot->Bot 阶段切换保持 `in_progress`。
- 场景：当已启动的 Bot 阶段完成且下一阶段也是 Bot 时，Todo 仍为 `in_progress`。
- 场景：当最终阶段完成时，Todo 为 `completed`；当用户显式取消时，Todo 为 `cancelled`。

### REQ-2：Bot/Human 阶段与待处理原因

- **AC-002**：模板阶段支持 `stage_kind=bot|human`；Human stage 激活或 Bot blocked 时 Todo 为 `pending_action`，并分别记录 `planned_human_stage | blocked`；Human->Bot 推进后为 `in_progress`。
- 场景：当模板下一阶段为 Human 时，系统通知绑定的 Human executor 并等待显式完成。
- 场景：当 Bot 上报 blocked 时，系统保留 blocker reason/requested_action，不推进阶段。

### REQ-3：版本化模板和参与角色绑定

- **AC-003**：模板拥有不可变 published version、参与角色 schema、有序阶段和可选门禁；Todo 创建时固定 `template_version_id`、补齐所有 required binding，并快照解析后的 executor/owner；后续模板修改不得影响已创建 Todo。
- 场景：当已发布模板被编辑时，系统创建或修改 draft version，拒绝原地修改 published definition。

### REQ-4：Human 阶段授权、显式完成与幂等

- **AC-004**：只有当前 Human stage 绑定的 executor 能通过阶段 complete 命令推进；请求必须具备幂等键，重复请求不得重复推进，错误 actor、旧 stage 或迟到请求必须拒绝。
- 场景：当 Human executor 重试相同 complete 请求时，系统返回同一结果且只推进一次。

### REQ-5：Bot outcome、失败、中断、恢复和取消

- **AC-005**：Bot outcome 支持 `completed|blocked|failed`；`failed` 仅是 attempt 结果，自动恢复期间 Todo 保持 `in_progress`，恢复耗尽后才为 `interrupted`；当前阶段负责人或 Space Admin 可带原因 resume/cancel，completed/cancelled 默认不可恢复。
- 场景：当 blocked interaction 已提交但未执行 resume 时，Todo 仍保持 `pending_action(blocked)`。
- 场景：当 authorized actor resume interrupted/blocked Todo 时，系统创建新 execution attempt 并回到 `in_progress`。

### REQ-6：确定性硬产物门禁

- **AC-006**：阶段可不配置门禁；若配置，只有 capability registry 支持的 `artifact_type + artifact_state` 可发布，并在阶段完成事务中校验。V1 支持 `pull_request:created`，要求当前阶段存在可信上报的规范化 PR 产物事实；未知门禁或缺失产物不得推进。
- 场景：当代码开发阶段要求 PR 但没有匹配产物时，complete 返回结构化缺失项，阶段和 Todo 状态不推进。

### REQ-7：隔离、事务和并发安全

- **AC-007**：模板、版本、Todo、阶段、execution 和 artifact 均按 tenant/env/space 隔离；创建 Todo、展开阶段、门禁校验和状态推进使用事务、行锁或 expected-status CAS；数据库写失败必须传播错误。
- 场景：当 cancel、outcome 和 resume 并发时，只允许一个合法迁移成功，迟到 outcome 不得恢复 cancelled Todo。

### REQ-8：前端模板与六状态工作台

- **AC-008**：Todo 模块提供模板管理、模板版本编辑/发布、模板化创建、动态角色绑定、六状态列表筛选和阶段化详情；planned Human、blocked、interrupted 和 cancel 显示不同动作；UI 使用现有语义 design token。
- 场景：用户可以发布三阶段模板，创建 Todo，绑定 Bot/Human，并观察 Bot->Human->Bot 的状态变化。

### REQ-9：兼容契约和文档一致性

- **AC-009**：新增阶段化 contract 的同时保留现有 `/complete`、`/report`、`artifacts/object-file` 兼容适配，固定调用方清单；OpenAPI/Plugin contract、conformance tests、Todo README 和前序规格的替代说明使用同一状态和术语。
- 场景：旧 Bot 调用兼容接口时结果被绑定到当前 stage，不能绕过 actor、execution freshness 或门禁校验。

### REQ-10：正式 schema 变更但不迁移历史数据

- **AC-010**：提供正式 DDL/版本变更，新增模板、版本、阶段实例和语义产物字段；不编写历史 Todo 语义迁移，发布说明明确旧数据清理、前滚和回滚顺序。

## 约束

- Core 保持 transport agnostic；HTTP adapter 只做 DTO/错误转换。
- 不新增硬编码私有 URL、凭据或环境路径。
- Contract 变更必须同步文档和 conformance/compatibility tests。
- 所有新增或修改源文件不得超过 1,000 行。
- 不复用 Bot Template 表表达 Todo Template。

## 需求就绪度

- 评分：`9/10`。
- 目标、角色、状态语义、模板边界、非范围、兼容方向、数据策略和验收标准均已确认。
- 非阻断后续项：远程 SCM 真实性校验、独立模板发布权限、兼容接口移除版本。

## 验收对齐检查

| AC | Goal alignment | Verification |
| --- | --- | --- |
| AC-001 | 六状态与不回退未开始 | 领域单测、API/前端状态测试、Singlebox Bot->Bot |
| AC-002 | Bot/Human 产品阶段 | 模板校验、阶段推进集成测试、Singlebox Bot->Human->Bot |
| AC-003 | 可复用且可追溯模板 | 版本不可变、绑定快照、Repository/Service tests |
| AC-004 | 可信 Human 完成 | 授权、旧阶段、重复请求 contract tests |
| AC-005 | attempt 与产品状态分离 | failure/recovery/resume/cancel 并发测试 |
| AC-006 | 确定性门禁 | capability/schema/缺失和满足门禁测试 |
| AC-007 | 数据安全与一致性 | tenant/env/space、事务、CAS、写失败测试 |
| AC-008 | 用户可配置和观察流程 | frontend tests、typecheck、lint、用户故事 |
| AC-009 | 新旧调用可平滑切换 | compatibility/conformance tests、文档检查 |
| AC-010 | 可发布 schema | DDL review、repository tests、runbook review |

## 变更记录

- 2026-11-20：从用户讨论和两条 Dima 对齐内容生成正式 Spec。
