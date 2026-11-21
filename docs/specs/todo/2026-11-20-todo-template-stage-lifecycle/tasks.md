# 待办模板、阶段执行与生命周期实施计划

> 状态：Implementation In Progress（Slice 1/8 已完成）
> 依赖：`proposal.md`、`spec.md`、`design.md`

## 实施原则

- 采用 TDD：每个切片先新增失败测试，再实现最小闭环并重构。
- 每个切片必须形成用户可观察或契约可验证的纵向能力，不按“先写所有表/所有接口”水平切分。
- 保留现有 tenant/env/space、事务、行锁/CAS、调度 lease 和错误传播约束；Bot 只通过产物上报和 `/complete` 提交执行事实。
- 不做历史 Todo 数据迁移；不新增独立 Bot 阶段 outcome endpoint，保留现有 `/report`、对象文件上传和 `/complete` 兼容路径。

### Slice 1：模板定义校验与 capability 查询 ✅

- Goal：用户能定义一个包含参与角色、Bot/Human 阶段和可选 PR 门禁的合法 draft，并查询平台支持能力。
- Covers AC IDs：AC-002、AC-003、AC-006。
- Tests first：template definition、重复 key、非法 role ref、stage kind/type mismatch、空阶段、未知 gate、capability response。
- Implementation：纯领域模板 schema/validator、artifact capability registry、capability HTTP adapter。
- Validation：focused domain + HTTP tests。
- Dependencies：无。
- Human confirmation needed：no。

#### Slice 1 实施记录

- Covers AC IDs：AC-002、AC-003、AC-006。
- Red：领域测试确认模板模型/校验不存在；HTTP 测试确认 capability Service API/adapter 不存在。
- Implementation refs：
  - `apps/backend/src/agentclaw/community/core/task/todos/template_models.py`
  - `apps/backend/src/agentclaw/community/core/task/todos/template_validation.py`
  - `apps/backend/src/agentclaw/community/core/task/todos/template_capabilities.py`
  - `apps/backend/src/agentclaw/community/core/task/todos/template_capability_protocol.py`
  - `apps/backend/src/agentclaw/community/core/task/todos/services/todo_template_capability_service.py`
  - `apps/backend/src/agentclaw/community/api/task/todo_template_capability_service.py`
  - `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/task/todos/template_capability_router.py`
  - `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/task/todos/template_capability_schemas.py`
- Contract：`GET /openapi/v1/collaboration/tasks/todo-template-capabilities`，作为 `/todos` sibling，显式 `user_id`，仅 Human caller。
- Validation：355 个 focused domain/HTTP/inventory/architecture/DI/OpenAPI tests 通过；scoped Ruff lint/format 通过；diff/whitespace/file-size checks 通过。
- Residual risk：模板持久化、发布不可变性、Todo 展开和生命周期仍由 Slice 2-8 实现。

### Slice 2：发布不可变模板并读取版本

- Goal：Space Admin 能创建模板/draft、发布不可变版本、创建下一 draft，并归档/激活模板。
- Covers AC IDs：AC-003、AC-007、AC-010。
- Tests first：published immutable、definition hash、Space Admin authorization、tenant/env/space isolation、写失败传播。
- Implementation：DDL、ORM/repository、template service、management APIs。
- Validation：repository/service/HTTP tests + schema review。
- Dependencies：Slice 1。
- Human confirmation needed：no。

#### Slice 2 实施记录

- Covers AC IDs：AC-003、AC-007、AC-010。
- Green/refactor：新增模板管理 model/codec/protocol/service、repository protocol/ORM 实现、Service API seam、HTTP management adapter、DI bindings 和手工部署 DDL；发布版本不可变、definition hash、tenant/env/space 隔离、事务锁和错误传播在 repository/service 层验证。
- Implementation refs：
  - `apps/backend/src/agentclaw/community/core/task/todos/template_management_models.py`
  - `apps/backend/src/agentclaw/community/core/task/todos/template_codec.py`
  - `apps/backend/src/agentclaw/community/core/task/todos/template_management_protocol.py`
  - `apps/backend/src/agentclaw/community/core/task/todos/services/todo_template_service.py`
  - `apps/backend/src/agentclaw/community/core/repository/protocols/task/todo_templates.py`
  - `apps/backend/src/agentclaw/community/core/repository/implementations/task/todos/template_models.py`
  - `apps/backend/src/agentclaw/community/core/repository/implementations/task/todos/todo_template_repository.py`
  - `apps/backend/src/agentclaw/community/core/task/todos/sql/2026_11_20_todo_templates.sql`
  - `apps/backend/src/agentclaw/community/api/task/todo_template_service.py`
  - `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/task/todos/template_schemas.py`
  - `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/task/todos/template_router.py`
  - `apps/backend/src/agentclaw/community/di/modules/task/todo_bindings.py`
- Validation：324 个 Slice 2 focused regression tests 通过（core/service/repository/HTTP/capability/conformance/authorization/admission/OpenAPI identity inventory）；12 个 DI focused tests 通过；修复 OpenAPI enum 文档和 API layer re-export 规则后，356 个 schema/architecture/DI gate tests 通过；新增/直接修改文件的 scoped Ruff lint 通过，直接 owned 文件 Ruff format 通过；`git diff --check` 通过；所有新增/修改源文件均低于 1,000 行。
- Residual risk：模板仍是独立管理面；基于模板创建 Todo、绑定槽位解析、阶段展开和初始生命周期投影属于 Slice 3。

### Slice 3：模板化创建 Todo 并展开阶段

- Goal：用户选择 published version 并绑定角色后，原子创建 Todo 和所有 stage instances；首阶段状态正确。
- Covers AC IDs：AC-001、AC-002、AC-003、AC-007、AC-010。
- Tests first：required binding、subject type、snapshot、partial-write rollback、Bot-first/Human-first initial projection。
- Implementation：create command/DTO、binding resolver、stage expansion、Todo/stage persistence。
- Validation：service/repository/API integration tests。
- Dependencies：Slice 2。
- Human confirmation needed：no。

### Slice 4：Bot execution complete、Human complete 与硬门禁

- Goal：当前 execution 或当前 Human executor 能可信完成阶段，Runtime 内部 blocked/failed 语义正确，PR 门禁不能被绕过；Bot 不直接提交阶段 outcome。
- Covers AC IDs：AC-002、AC-004、AC-005、AC-006、AC-007、AC-009。
- Tests first：actor auth、execution freshness、完成内容幂等、Runtime blocked/failed attempt、missing/satisfied PR gate、旧 stage/迟到 request、现有兼容 endpoint。
- Implementation：execution complete command、Human complete、semantic artifact persistence、gate validator、compat adapters；阶段推进由 Backend 内部调用领域转换。
- Validation：core + contract + compatibility tests。
- Dependencies：Slice 3。
- Human confirmation needed：no。

### Slice 5：线性推进、六状态投影与恢复/取消

- Goal：完整覆盖 Bot->Bot、Bot->Human、Human->Bot、blocked、interrupted、resume、cancel 和最终完成。
- Covers AC IDs：AC-001、AC-002、AC-004、AC-005、AC-007。
- Tests first：状态转换表、并发 outcome/cancel/resume、interaction 不自动 resume、迟到 outcome、completed/cancelled 不可恢复。
- Implementation：stage transition service、scheduler projection、resume/cancel commands、audit events。
- Validation：domain/integration/scheduler tests。
- Dependencies：Slice 4。
- Human confirmation needed：no。

### Slice 6：Frontend 模板管理与模板化创建

- Goal：用户在 Todo 模块创建/发布模板，并按参与角色动态绑定后发起 Todo。
- Covers AC IDs：AC-003、AC-006、AC-008。
- Tests first：static route precedence、Bot/Human stage editor、capability-driven gate options、published readonly、dynamic bindings。
- Implementation：routes、services/types、template list/editor、create flow。
- Validation：targeted tests、typecheck、lint。
- Dependencies：Slice 2、Slice 3。
- Human confirmation needed：no。

### Slice 7：Frontend 六状态列表与阶段化详情

- Goal：用户能区分 planned Human、blocked、interrupted、cancelled，并执行允许的 complete/resume/cancel 动作。
- Covers AC IDs：AC-001、AC-002、AC-004、AC-005、AC-008。
- Tests first：status filters/labels、stage timeline、action authorization rendering、blocked interaction 与显式 resume。
- Implementation：Todo list/detail hooks/components/services。
- Validation：targeted/full frontend tests、typecheck、lint。
- Dependencies：Slice 5、Slice 6。
- Human confirmation needed：no。

### Slice 8：契约、文档、兼容与 Singlebox 收口

- Goal：新旧 Bot 调用和用户端流程在同一术语/状态契约下可验证发布。
- Covers AC IDs：AC-008、AC-009、AC-010。
- Tests first：plugin/service contract、compat routes、Singlebox Bot->Bot、Bot->Human->Bot、PR gate、Runtime blocked/resume、cancel/late completion。
- Implementation：OpenAPI/Plugin docs、Todo README、前序规格 supersession、runbook、caller inventory。
- Validation：Backend/Frontend affected gates、architecture tests、conformance tests、Singlebox coverage、`git diff --check`、file size checks。
- Dependencies：Slices 1-7。
- Human confirmation needed：no。

## 验收点实现映射

| AC ID | Slice(s) | Planned files/surfaces | Validation | Status |
| --- | --- | --- | --- | --- |
| AC-001 | 3,5,7 | Todo model/service/repository/scheduler/frontend | state + integration + UI tests | planned |
| AC-002 | 1,3,4,5,7 | template schema/stage transition/UI | domain + integration + UI tests | Slice 1 implemented |
| AC-003 | 1,2,3,6 | template/version/repository/API/frontend | immutable/binding tests | Slice 1 implemented |
| AC-004 | 4,5,7 | Human complete/service/API/UI | auth/idempotency tests | planned |
| AC-005 | 4,5,7 | outcome/scheduler/resume/cancel/UI | recovery/concurrency tests | planned |
| AC-006 | 1,4,6 | capability/artifact/gate/editor | validator + contract tests | Slice 1 implemented |
| AC-007 | 2,3,4,5 | DDL/repository/services | isolation/transaction/CAS tests | planned |
| AC-008 | 6,7,8 | frontend routes/services/components | frontend CI + Singlebox | planned |
| AC-009 | 4,8 | adapters/contracts/docs | compatibility/conformance tests | planned |
| AC-010 | 2,3,8 | SQL/runbook/repository | schema review + repository tests | planned |

## 调用方盘点要求

实施兼容 adapter 前，固定字符串搜索并记录 Todo 专用：

- `/api/v1/collaboration/tasks/todos/{todo_id}/complete`
- `/api/v1/collaboration/tasks/todos/{todo_id}/report`
- `/api/v1/collaboration/tasks/todos/{todo_id}/artifacts/object-file`

不得把通用 Task `/callback/report` 误算为 Todo endpoint 调用方。

## 提交前检查

- 每个 blocking AC 都绑定代码引用和真实验证证据。
- Backend focused/full affected tests、SAST/architecture/contract gates 通过。
- Frontend tests、typecheck、lint 通过。
- Singlebox 相关用户故事和 coverage gate 通过。
- `git diff --check` 通过；所有修改源文件少于 1,000 行。
- 无硬编码私有 URL、token、凭据、仓库/分支环境配置。
- 完成 Tier 1 风险评审、Tier 2 确定性检查、AC Acceptance 和 Delivery Audit 后才允许提交/推送。
