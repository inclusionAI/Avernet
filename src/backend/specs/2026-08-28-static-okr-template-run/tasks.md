# Tasks

1. [x] 需求快照、AC 台账与 pipeline state。
2. [x] TDD：固定 Plan YAML 加载/校验。
3. [x] TDD：固定 DAG 首批并行、Join、条件推进。
4. [x] 增加 `TaskType.STATIC_PLAN` 与请求 DTO。
5. [x] 增加模板运行 service/API，复用 TaskService.execute。
6. [x] 接入 ExecutionEngine 回报后的固定推进。
7. [x] 增加固定 OKR YAML（bot_id 留空，运行时拒绝）。
8. [x] 运行 Task/endpoint/架构检查并更新验收证据。

> HTTP adapter 专项测试的收集被既有 bot_service/aicoding workspace_service 循环导入阻塞，已记录于 pipeline/state.json；未为本需求修复无关依赖。固定计划专项测试 16 passed，Task center/dispatch 回归 73 passed。
