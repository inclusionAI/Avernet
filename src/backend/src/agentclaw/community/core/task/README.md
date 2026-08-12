# core/task — 任务目标驱动执行框架 v4

基于任务目标(Goal + Acceptance)驱动的任务动态规划执行框架;跨 单 Bot / 协作群 / BBS 三模态自驱跑完「理解 → 规划 → 派发 → 执行 → 验收 → 重规划」闭环。

> 权威源(冲突时以此为准):
> - 设计三件套:`src/backend/specs/2026-08-09-task-goal-driven-execution-framework/{plan,spec,tasks}.md`
> - 5 模块设计文档(语雀):任务中心 `yugg6dorsxo8sgmp` / 任务图谱 `lunk1txfuv6gtwk2` / 任务规划 `uuq2tlue91q4lkal` / 任务派发 `ue1ie0g3supwo2uf` / 任务执行 `lxg2mwgmtfqg6d95`
> - 流程架构图 `apoi9lcedw9u8ivq`、case 剧本 `gwqie46v7hzr1w6h`

## 四层目录结构(对齐 `docs/arch/arch.rules.md §8`)

| 层 | 路径 | 职责 |
|---|---|---|
| api/ | `community/api/task/` | 对外 Service API Protocols(transport-agnostic) |
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
├── task_plan/                     # TaskPlanner 规划编排壳 + DecomposerPort seam(可插拔)
│   └── planner.py                 #   plan(graph) → 委托 decompose(零 case 知识);protocols.py 延后
├── task_dispatch/                 # TaskDispatcher 搜推分发 + BotDiscoverPort seam(可插拔)
│   └── dispatcher.py              #   dispatch(toDoList) → 填 run_mode/assignee 返 list[TaskNode];protocols.py 延后
├── task_runner/                   # TaskRunner 三模态执行 + 回投适配
│   ├── runner.py                  #   start_run(批量)三模态自适应 + query_status/detail/result/bot_tasks
│   └── callback_adapter.py        #   TaskCallbackData → TaskNodePatch → engine.on_report
├── task_harness/                  # TaskHarness 旁路常驻巡检
│   └── harness.py                 #   周期巡检超时/崩溃 → 复位 PENDING 重投(不抢正向)
└── task_mining/                   # 占位(另一位同学的任务挖掘模块,本框架不实现)
```

> seam Protocol(`task_plan/protocols.py` `DecomposerPort` / `task_dispatch/protocols.py` `BotDiscoverPort` + `SearchResult`/`GroupFormation`)首批延后,待 stub/真实实现就位时落;`__init__.py` 各目录均有,树中省略。

## 内部层惯例(本模块约定)

- **扁平化**:`task_xxx/` 下直接放 `.py`,不建 `services/` 子目录
- **errors 统一**:全框架 errors 收口到 `domain/errors.py`,各子目录不放 `errors.py`
- **seam Protocol 就地**:可插拔契约(`DecomposerPort`/`BotDiscoverPort`)放各模块 `protocols.py`,不进 `api/`(非对外)
- **依赖方向**:`domain` ← {各模块} 单向;模块间不互依实现,经 Protocol + engine 协调

## 红线

- **零 case 知识**:任何节点名(`N_overview`/`N_market` 等)只能出现在 case stub 产出或测试,绝不写死框架代码
- **单一实现**:全仓只此一套任务执行实现,不并存旧模型并行包(规范位置 `core/task`)
- **transport-agnostic**:`core/` 禁 transport 框架 import(CI gate,`docs/arch/arch.rules.md §7`)
- **开源边界**:Avernet 只发 seam + stub/singlebox;真实 corp 实现(规划 LLM/搜推/执行)在 ocb 仓

## 首批 PR 范围

目录骨架 + `domain/models.py` + `domain/errors.py` + 各模块核心 API 壳(Protocol + 类签名)。
不含(后续 milestone):stub 实现类、`task_plan/protocols.py`、`task_dispatch/protocols.py`、adapters/http router、di 接线、task_mining 实现。
