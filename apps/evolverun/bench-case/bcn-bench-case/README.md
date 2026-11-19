# BCN 群协作评测案例

本目录收录可在 Avernet Singlebox 环境中运行的多 Agent 协作任务。每个案例由任务定义（`case.md`）和成员配置（`profiles/`）组成。`clawbench-bcn` 根据配置启动评测成员、创建协作群，并在任务完成或超时后归档群消息和成员会话。

## 案例列表

| 案例 | 成员数 | 评测目标 | 评分方式 |
| --- | ---: | --- | --- |
| [协作连通性](task_00_connectivity/case.md) | 2 | 验证协调员派发任务、成员回复、协调员正式提交结果的完整链路 | 代码评分 |
| [用户反馈功能方案](task_01_singlebox_feedback_plan/case.md) | 5 | 综合产品、研发、验证和客服意见，形成可执行的功能方案 | 代码评分 10%，模型评分 90% |
| [疏散路线决策](task_02_evacuation_routes/README.md) | 4 | 成员交换各自掌握的路线信息，按讨论后各自的选择计算正确率 | 代码评分 |

## 本地启动

以下命令均在 **Avernet 仓库根目录** 执行。首次运行前，请安装 Python 依赖，准备 Node.js 22.19 或更高版本、OpenClaw 及其模型配置，并完成 BCN 插件构建和 Singlebox 环境准备。详见 [clawbench-bcn 使用说明](../../clawweb-skills/clawevolve-skills/clawbench-bcn/README.md)。

**1. 启动基础服务。** BAAS 为 Backend 提供本地凭据，BCS 负责建群和任务协作。已有健康服务时直接复用；重新启动 Backend 可能重新初始化本地 SQLite 数据库。

```bash
./singlebox/singlebox.sh start baas backend bcs
```

**2. 运行连通性案例。** 评测程序从案例目录读取配套成员配置，自动启动协调员和执行成员。

```bash
BENCH=apps/evolverun/clawweb-skills/clawevolve-skills/clawbench-bcn/scripts/bench.py
CASES=apps/evolverun/bench-case/bcn-bench-case
python3 "$BENCH" run --mode local --bench-dir "$CASES/task_00_connectivity"
```

**3. 运行其他案例。** 在同一终端更换 `--bench-dir` 即可。例如，以下命令运行四成员疏散路线案例。仅需检查案例结构时，在命令末尾添加 `--check`。

```bash
python3 "$BENCH" run --mode local --bench-dir "$CASES/task_02_evacuation_routes"
```

## 输出与结果

以下节选自一次两成员连通性测试的实际日志。该次协作执行耗时 46 秒，代码评分为 1.0。

```text
[17:14:54] 被测团队已启动；建群并一次性提交协作目标
[17:14:54] BCN状态: running; 已执行 0s / 600s
[17:15:26] BCN状态: running; 已执行 32s / 600s
[17:15:40] BCN状态: completed; 已执行 46s / 600s
[17:15:40] 执行结果: completed
[17:15:43] 本次Bot已停止，端口退出检查通过
[17:15:43] 回收群消息、成员会话和模型用量
[17:15:43] 成员 manager: 日志完整=True, 模型用量=94207
[17:15:43] 成员 worker: 日志完整=True, 模型用量=15145
[17:15:43] 执行代码评分
[17:15:43] 执行=completed；评分=completed；得分=1.0
[17:15:43] evaluation-request.json 已就绪；等待独立评测 Agent 生成报告
```

终端会打印本次运行目录，默认为 `~/.cache/clawbench-bcn/runs/<run_id>/`。先查看 `result.json` 中的执行状态、评分状态和得分。若执行超时且评分跳过，得分为空，可结合以下文件定位协作停在哪一步。

| 文件或目录 | 内容 |
| --- | --- |
| `runner.log` | 启动、建群、执行、清理和评分的时间记录 |
| `evidence/` | 群消息和各成员的实际会话 |
| `report.md` | 本次运行的结果摘要 |
| `evaluation-request.json` | 独立评测 Agent 用于生成详细分析报告的交接文件 |

## 编写案例

1. 在本目录下创建任务目录，将[案例模板](case.template.md)复制为 `case.md`，填写协作目标、预期行为和评分标准。案例 ID 与目录名保持一致，使用字母、数字、下划线或连字符。
2. 在 `profiles/bots.json` 中声明成员及入口协调员。`entry_member` 引用成员的 `profile` 别名，每位成员的 `source` 目录至少包含 `AGENTS.md`、`IDENTITY.md` 和 `KNOWLEDGE.md`。
3. 按任务选择代码评分（`automated`）、模型评分（`llm_judge`）或混合评分（`hybrid`），实现模板中对应的评分逻辑。混合评分默认各占 50%，可通过 `grading_weights` 调整；模型评分细则中各项权重合计为 100%。
4. 从仓库根目录用 `--check` 校验 Case 与 Profile 结构，再运行实际评测验证协作和评分逻辑。

评测程序只将 `case.md` 的 `Prompt` 段作为群协作目标。成员所需资料随各自 Profile 分发，参考答案和评分材料保留在评测端。当前 `workspace_files` 应设为 `[]`。
