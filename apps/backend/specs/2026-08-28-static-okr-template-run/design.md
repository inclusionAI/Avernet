# Design

## Existing reuse
TaskService.execute 已负责 task_id、task_info、初始化 graph；ExecutionEngine 已负责回报入口；TaskRunner 已支持批量并发与运行时建群；TaskGraphService 是状态持久化 SSOT。新增逻辑仅补固定 Plan 定义加载、模板入口和固定推进策略。

## Boundary
- `core/task/task_plan/static_plan.py`: 纯领域配置模型、YAML 加载、依赖/输入/bot 校验。
- `core/task/task_plan/static_plan_runtime.py`: 固定 DAG 状态判断和下一批节点计算，不接 HTTP、不调用具体传输实现。
- `core/task/task_center/template_run_service.py`: 模板输入转现有 TaskInfoRequest，并调用 TaskService.execute。
- HTTP router: 只做 DTO/身份/错误映射。
- 现有 Runner/BCS adapter: 负责实际 bot/group 投递。

## State model
固定 Plan 不新增 cursor。当前阶段由节点状态 + `depends_on` 推导：无依赖节点可并行；Join 节点要求全部 DONE；条件节点由前置输出判定。节点输出使用现有 `run_info.output`，群 session/group 使用现有 run_info.extend_props。

## Compatibility
新增 `TaskType.STATIC_PLAN` 与 `execution_config.static_plan_id`。既有三种 task_type 不改变。固定节点暂挂在根节点下，跨节点依赖只存于固定 Plan 元数据，避免破坏 Relation 单父分解树。

## Risks
- 外部 bot/group API 契约需通过现有 Runner/adapter；空 bot 绑定在进入执行前阻断。
- 现有 graph 状态机按树设计，固定 runtime 必须幂等地只选择 PENDING 且依赖满足的节点。
