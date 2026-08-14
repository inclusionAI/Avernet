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
└── task_discovery/                # 占位(另一位同学的任务挖掘模块,本框架不实现)
```

## 协程化约束(任务执行是耗时任务;对齐 backend lifecycle async 模式)

任务执行(投递单 bot workflow / BCS 拉群 / BBS 广场)是网络 IO 耗时操作,编排核全链路 **`async def`**,耗时 IO 不阻塞事件循环、不阻塞编排核。

### 全链路 async 签名

| 方法 | 所在类 | 签名 |
|---|---|---|
| `on_execute` / `on_report` / `on_miss` / `on_harness` | `ExecutionEngine` | `async def` |
| `start_run` / `form_coop_group` | `TaskRunner` | `async def` |
| `deliver` | `DeliveryPort`(Protocol) | `async def deliver(node) -> bool` |
| `report_result` / `start_run` | `TaskLoopCallback` | `async def` |
| `execute` | `TaskService` facade | `async def`(内部 `await engine.on_execute`) |
| `plan` | `TaskPlanner` | `async def`(内部 `await strategy.matches/apply`) |
| `dispatch` | `TaskDispatcher` | `async def`(内部 `await strategy.matches/apply`) |
| `matches` / `apply` | `PlanningStrategy` / `DispatchStrategy` Protocol | `async def`(corp 为 LLM/catalog 耗时 IO) |

> `plan`/`dispatch`/策略 `apply` 在 corp 是 LLM 规划 / bot catalog 搜推(耗时 IO),亦 `async`,在锁内 `await`。

### 锁内 await 边界(collect / drain 模式)

- per-task `threading.RLock` 保护锁内编排写:`TaskGraphService` 内存同步快操作(graph patch、add_task_nodes)+ `await plan/dispatch`(同 task 串行推进的耗时 IO,设计意图)。
- **锁内不 `await` 的是高并发外部投递 IO**(`start_run`/BCS 拉群 `form_coop_group`/`deliver`)——这些 `await` 在锁外。
- **collect / drain 模式**:`on_*` 锁内 `async collect`(`await plan/dispatch` + 同步 add/patch,产 side-effects list)→ 锁外 `_drain` 统一 `await` 执行 run/group/miss/finish(投递 IO),保证投递 IO 全锁外。
- 跨 task 并行;同 task_id 编排串行(锁内);投递/拉群 IO 不受锁约束(锁外 await,可并发)。
- **锁选型**:`threading.RLock` 适用本仓一次性事件循环 / 跨线程回调模型(跨线程正确串行)。若 corp 采用单持久 event loop 并发处理同 task 多回投(如 FastAPI async 端点),同 loop 协程重入会穿透 RLock,需切 `asyncio.Lock`(ocb 仓接入时定)。

### 投递并发下沉 runner

- 多节点投递并发:`asyncio.gather` + `asyncio.Semaphore(_DELIVER_CONCURRENCY=8)` **下沉 `TaskRunner.start_run` 内部**(投递是 runner 职责;engine 批量调 `start_run`,不持锁内拆单节点)。
- 对齐 backend `desktop_bot/lifecycle.py` 的 `_check_one_bot` Semaphore 限流模式,防多节点投递雪崩。

### graph 与 harness

- `TaskGraphService` **保持同步**(内存快;`to_thread` 隔离语义留 corp DB 适配,本轮不动)。
- `TaskHarness` 后台 `threading` 巡检线程无事件循环:调 async `on_harness` 时经 `asyncio.iscoroutine` 判定起短命 `asyncio.run`(兼容同步 stub,低频旁路)。

### 测试规约

- 单测经 `asyncio.new_event_loop().run_until_complete(coro)` 驱动 async 编排方法 helper,**不用 `@pytest.mark.asyncio`**(对齐本仓现有测试约定)。
- 真实投递/seam 经 `run_until_complete` 驱动;sync stub runner 仍可被 `await`(返非 coroutine 协程兼容由 `iscoroutine` 兜底,harness 用)。
