# 待办模板、阶段执行与生命周期重设计 Proposal

> 状态：Confirmed
> 日期：2026-11-20
> 绑定 Dima：`2026111900120789942`、`2026111900120789898`

## 背景

现有 Todo 只绑定一个 Bot，并使用 `pending -> in_progress -> pending_acceptance -> completed` 表达一次 Bot 执行。该模型无法表达可复用的多阶段产品流程、计划内 Human 阶段、阶段负责人、自动恢复耗尽后的中断状态，以及确定性的硬产物门禁。

## 问题定义

产品需要把“任务本身”“可复用流程模板”“Bot/Skill 的执行方式”“Runtime 恢复策略”和“仓库/分支等环境”分开：

- Todo 表达一次具体工作；
- 待办模板表达一类工作包含哪些有序阶段、阶段类型、参与角色和确定性完成门禁；
- Bot/Skill 决定如何完成任务以及如何整理上报内容；
- Runtime 决定重试、恢复、切换 Bot 等内部策略；
- Environment 提供仓库、分支、部署目标和凭据，不进入模板定义。

## 目标

1. 引入版本化待办模板、参与角色绑定和线性阶段实例。
2. 支持 `bot | human` 两类阶段，计划内 Human 阶段进入待处理。
3. 将 Todo 产品状态调整为六状态，并保证已启动 Todo 不回退到未开始。
4. 将 Bot execution 的完成事件与 Runtime attempt 状态、Todo 产品状态分离；Bot 不直接上报阶段状态。
5. 支持可确定性检查的硬产物门禁，首批支持 `pull_request:created`。
6. 取消每次 Bot 完成后的隐式人工验收；需要人工验收时使用显式 Human 阶段。
7. 保留现有产物 `/report`、对象文件上传和 `/complete` 兼容适配；`/complete` 支持定制完成内容，不新增 Bot 阶段状态上报接口。

## 用户/角色

- 模板管理者：Space Admin，创建、编辑、发布和归档模板。
- Todo 创建者：选择已发布模板版本并绑定参与角色。
- Bot 阶段执行者：当前阶段绑定的 Bot/Runtime。
- Human 阶段执行者：当前阶段绑定的 Human。
- 阶段负责人：承接 blocked/interrupted 状态并决定恢复或取消。

## 范围

- Backend Todo Core、Repository、Scheduler、HTTP contracts 与数据库 DDL。
- Todo Template、Template Version、Stage Instance、Participant Binding、Artifact Gate。
- 六状态生命周期、Bot execution complete、Human stage complete、cancel/resume。
- Frontend Todo 列表、模板管理、创建流程和详情操作。
- Todo 领域 README、协议文档、兼容测试与 Singlebox 用户故事。

## 非范围

- 历史 Todo 数据迁移；上线前由用户清理旧数据。
- DAG、并行、条件分支、循环和 Human approve/reject 分支。
- 模板内配置 Skill、重试次数、换 Bot、知识注入或 BBS 求助策略。
- 模板内保存仓库、分支、部署凭据、私有 endpoint。
- 模型主观质量判断作为通用硬门禁。
- 远程 SCM 实时查询；V1 的 `pull_request:created` 校验基于当前阶段可信上报并持久化的规范化 PR 产物事实。
- 本次不新增或保留独立的 Bot 阶段 outcome endpoint；现有产物上报和 `/complete` 继续兼容。

## Open Questions

无阻断问题。远程 SCM 真实性校验、分支流程、独立发布权限和兼容接口移除版本留作后续独立需求。

## AI Assumptions

- `resume` 和 `cancel` 由当前阶段负责人或 Space Admin 执行，并要求审计原因。
- blocked interaction 只记录处理信息，不隐式恢复；由显式 `resume` 命令继续。
- 模板发布/归档 V1 使用现有 Space Admin 权限，不新增权限系统。
- `/report`、`artifacts/object-file` 继续负责产物上报，`/complete` 负责 execution 完成内容；阶段推进由 Backend 根据 execution 绑定内部投影。

## Decision Log

| 日期 | 决策 | 依据 |
| --- | --- | --- |
| 2026-11-20 | 移除隐式 `pending_acceptance`，人工验收建模为 Human stage | 用户讨论确认，避免产品状态与默认审批耦合 |
| 2026-11-20 | 顶层 Todo 使用六状态，Bot->Bot 阶段切换保持 `in_progress` | 用户明确指出不能重新进入 `not_started` |
| 2026-11-20 | `stage_kind` 支持 `bot | human` | 用户明确要求计划内 Human 介入 |
| 2026-11-20 | 模板只定义产品流程、角色引用、purpose 和确定性门禁 | Skill/Runtime/Environment 分别拥有执行策略和环境事实 |
| 2026-11-20 | published version 不可变，Todo 固定 template version | 保证运行中和历史 Todo 可追溯 |
| 2026-11-20 | blocked interaction 与 resume 分离 | 防止提交信息产生隐式状态变化 |
| 2026-11-20 | V1 发布权限使用 Space Admin | 复用现有权限能力并保持 fail-closed |

## 变更记录

- 2026-11-20：将讨论结论封板为正式 Proposal，进入 Propose+/实现流水线。
