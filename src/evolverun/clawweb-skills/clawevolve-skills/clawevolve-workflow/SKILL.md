---
name: clawevolve-workflow
description: ClawEvolve 运行时工作流与阶段执行器。用于在 BaaS Runner 已完成阶段路由后执行 Optimize 等进化阶段，管理阶段输入、Bench、调优、Spec、产物和 ClawWeb 状态上报。
---

# ClawEvolve Workflow

将本 Skill 作为 ClawEvolve 运行态实现，不直接作为远端 BaaS execute-command 入口。

统一入口 `clawevolve_async_runner.sh` 负责同步 Skill、运行控制和 stage 路由。本 Skill 的阶段执行器位于 `scripts/handlers/`，负责阶段业务与结果上报。

当前 Handler 只依赖 Python 标准库和已发布 Skill，不在任务启动时动态安装依赖。

## Message 与阶段执行

- `/clawevolve-workflow --stage bench-plan`：调用 `scripts/run.py`，转入 `scripts/handlers/clawevolve_bench_plan_run.py`。
- `/clawevolve-workflow --stage optimize`：调用 `scripts/run.py`，转入 `scripts/handlers/clawevolve_optimize_run.py --action run-round`。
- `clawevolve-bench`：由 Runner 转交 `scripts/handlers/clawevolve_bench_run.py`；handler 从 `${SKILL_BASE_DIR}/clawevolve-bench/scripts/clawbench-workflow.py` 调用 Bench。
- `clawevolve-pack --mode pack|restore`：Message 通道统一调用 `scripts/handlers/clawevolve_pack_run.py`，并原样透传 Task/Step 与结构化来源参数。
- 新增阶段时，在 `scripts/handlers/` 增加对应执行器，并同步更新统一 Runner 的受控 stage 路由。
- 阶段执行器接收 Runner 透传的参数，不改写参数名。
- 阶段执行器将可变状态写入 `/home/admin/.openclaw/workspace/clawevolve_results/<task-id>/`，不得写入只读发布目录。

## 约束

- 不直接处理 BaaS 鉴权或 execute-command 请求。
- 不使用 `eval` 或 `sh -c` 执行用户参数。
- 使用 `task-id` 和 `step-id` 隔离运行与上报，避免重试结果互相覆盖。
- 任一阶段失败时必须向 ClawWeb 上报对应 Step 的失败状态。
