# core/task — 任务目标驱动执行框架

基于任务目标(Goal + Acceptance)驱动的任务动态规划执行框架;跨 单 Bot / 协作群 / BBS 三模态自驱跑完「理解 → 规划 → 派发 → 执行 → 验收 → 重规划」闭环。

> 权威源(冲突时以此为准):
> - 设计三件套:`src/backend/specs/2026-08-09-task-goal-driven-execution-framework/{plan,spec,tasks}.md`

## 四层目录结构(对齐 `docs/arch/arch.rules.md §8`)

| 层 | 路径 | 职责 |
|---|---|---|
| api/ | `community/api/task/`(`task_service.py` + `task_loop_callback.py`) | 对外 Service API Protocols(transport-agnostic,一文件一 Protocol) |
| core/ | `community/core/task/` | 业务实现(transport-agnostic,禁 transport import) |
| adapters/http/ | `community/adapters/http/task/` | HTTP transport(thin:router+schema,不持 domain policy) |
| di/modules/ | `community/di/modules/task_module.py` | composition root(DI 接线) |

## core/task/ 目录树

```text
core/task/
├── README.md                      # 本目录规范文档
├── domain/                        # shared kernel:纯领域模型 + 统一 errors(零依赖,无 services)
│   ├── models.py                  #   领域 dataclass/enum + 中间类型(patch/criteria/op_result/callback_data)
│   └── errors.py                  #   统一错误(全框架唯一 errors 收口)
├── task_center/                   # TaskService facade + ExecutionEngine 编排核(非独立模块)
│   ├── task_service.py            #   facade 2 API(execute / get_task_dashboard)
│   └── engine.py                  #   ExecutionEngine:on_* 事件驱动 + 状态条件(a/b/c)推进
├── task_graph/                    # TaskGraphService 图谱 SSOT(7+2 API,独立模块)
│   └── task_graph_service.py      #   原子变更唯一网关 + relations 分解树派生查询
├── task_plan/                     # TaskPlanner 规划编排壳(零参 + 内置策略池 PlanningStrategy)
│   └── planner.py                 #   plan(graph) first-match-wins 选策略产子(零 case 知识);strategies.py 延后
├── task_dispatch/                 # TaskDispatcher 派发编排壳(零参 + 内置策略池 DispatchStrategy,不持 runner)
│   └── dispatcher.py              #   dispatch(toDoList) first-match-wins 选策略填 run_mode/assignee;strategies.py 延后
├── task_runner/                   # TaskRunner 三模态执行 + 回投适配
│   ├── runner.py                  #   start_run(批量)三模态自适应 + query_status/detail/result/bot_tasks
│   └── callback_adapter.py        #   TaskCallbackData → TaskNodePatch → engine.on_report
├── task_harness/                  # TaskHarness 旁路常驻巡检
│   └── harness.py                 #   周期巡检超时/崩溃 → 复位 PENDING 重投(不抢正向)
└── task_mining/                   # 占位(另一位同学的任务挖掘模块,本框架不实现)
```
