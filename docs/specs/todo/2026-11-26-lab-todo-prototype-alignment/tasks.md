# 实验室待办第一批实施计划

> 状态：Implementation Ready
> 依赖：`proposal.md`、`spec.md`、`design.md`

## 实施原则

- TDD：每个切片先写失败测试，再做最小实现和重构。
- 纵向切片，不先铺新表或新协议。
- 不修改数据库 schema，不回填历史数据。
- 保留旧 Template API；新产品层使用 BotTeam 命名。
- 不在 Progress/Interaction 页面构造假 Runtime 数据。

## Slice 1：Frontend 六状态

- Covers：AC-006。
- 修改 Todo domain、status mapping、API mapper、列表/详情状态判断。
- 未知状态必须 fail-visible。
- 验证：focused Jest、typecheck、open-core check。

## Slice 2：BotTeam facade

- Covers：AC-001、AC-002、AC-003。
- 新增 BotTeam domain/protocol/service API/HTTP adapter。
- 复用 Template management/creation services 和现有 repository/table。
- 实现创建首 revision、编辑新 revision、归档、线性 node 与 approval stage 映射。
- 保留旧 Template route；不新增 SQL。
- 验证：domain/service/HTTP/DI/inventory/architecture tests。

## Slice 3：BotTeam 创建 Todo

- Covers：AC-004。
- 统一 executor DTO；BotTeam 映射到 template create flow。
- 保存 `todo_owner` participant binding，固定 revision/name/stage snapshot。
- 老数据读取回退 `created_by`，不回填。
- 验证：service/repository/API integration tests。

## Slice 4：Dima 批量导入

- Covers：AC-005。
- 新增最多 20 条同步批处理 service 和 HTTP contract。
- 每条独立创建，duplicate 返回 existing Todo ID，失败不影响成功项。
- 支持 Bot/BotTeam executor 和 owner；不回写 Dima。
- 验证：mixed result、duplicate、authorization、identity forwarding tests。

## Slice 5：Frontend BotTeam、Dima 和 Todo 页面

- Covers：AC-007、AC-008。
- 实现 BotTeam 列表/编辑、统一 executor selector、Dima 多选导入和结果展示。
- Todo 列表/创建按原型信息架构调整。
- Progress/Interaction 只接当前真实协议或显示明确空态。
- 验证：focused component/service tests、typecheck、open-core check。

## 收口

- 更新 Todo README、API/authorization/admission inventories 和协议测试。
- 运行 Backend focused + architecture tests。
- 运行 Frontend focused tests + typecheck + `check:open-core`。
- `git diff --check`；修改源文件均少于 1000 行。
- 未经用户明确要求不 commit、不 push。

## AC 映射

| AC | Slice | 状态 |
| --- | --- | --- |
| AC-001 | 2 | planned |
| AC-002 | 2 | planned |
| AC-003 | 2 | planned |
| AC-004 | 3 | planned |
| AC-005 | 4 | planned |
| AC-006 | 1 | planned |
| AC-007 | 5 | planned |
| AC-008 | 5 | planned |
