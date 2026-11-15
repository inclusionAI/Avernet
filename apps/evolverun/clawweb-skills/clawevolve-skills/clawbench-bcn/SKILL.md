---
name: clawbench-bcn
description: 使用成套 Case/Profile 执行 BCN 群协作评测，归档真实群消息与成员证据、按单 Agent 模板进行代码或模型评分并生成成功或失败报告。当前仅支持本地 POC，评测由独立 Agent 完成。
---

# BCN 群协作评测

你是**独立评测 Agent**，不是被测群的成员。不要参与被测团队作答、修改其协作 Skill，或加入被测群。远程执行属于后续规划，当前尚未实现。

## 执行评测

1. 阅读选定评测资产中的 `case.md`。同目录的 `profiles/` 定义完整被测团队。执行前检查 Python 评分代码；其执行环境不是安全沙箱。
2. 执行 `python3 scripts/bench.py run --mode local --bench-dir <评测资产目录>`。可选参数为 `--model-config <openclaw.json>` 和 `--output-dir <输出目录>`；默认模型文件为 `~/.openclaw/openclaw.json`。详见[本地使用说明](references/local.md)。
3. 脚本只检查并复用用户已启动的基础服务；缺失时提示显式 setup/start backend bcsfuse bcs 并退出，不启动默认 Demo。服务就绪后启动隔离的被测 Agent，一次性提交协作目标，随后归档并清理本次拥有的资源。只向被测群投递 Case 的 Prompt，不投递评分标准。
4. 查看终端打印的运行目录。**即使退出码为 1 或 2，只要已有归档，也需要生成失败报告。** 退出码 3 表示执行已归档、等待你完成评估，不代表整个 Bench 已成功完成。

## 评估与报告

- 阅读 `evaluation-request.json`、`run.json`、`result.json`、`evidence/session.json`、`evidence/group-messages.json`、存在时的 `artifacts/final.md`，以及各成员归档的 JSONL。文件缺失应标记为证据缺口，不得编造结果。
- 长日志分段读取，并说明未覆盖的部分。不能抽样后声称已完整审阅。不要读取或上传运行时模型配置与凭据。
- 按 `prompts/report.md` 生成报告，包含执行结果、代码检查、真实委托与回复、带证据的失败原因、成本及证据缺口。即使超时且没有正式交付，也必须生成报告。
- `automated` 模式：保留代码评分，只补分析报告。`llm_judge` / `hybrid`：仅当 `grading_status=awaiting_evaluation_agent` 时，按 Case 的 `LLM Judge Rubric` 评分；缺失时采用 `Grading Criteria`。评估 JSON 返回 `scores`（各维度 0～1）、`total`（按 Rubric 权重计算的 0～1 总分）、`notes`（证据和扣分依据）。`total` 必填，不自动改成维度平均；hybrid 的代码/模型权重由程序汇总。
- 可选 `usage` 只记录评测方自身用量；`evidence_notes` 说明证据审阅范围。执行失败或跳过评分时不提交 scores/total，只生成分析报告。
- `artifacts/transcript.json` 是原模板消息列表，`artifacts/bcn-evidence.json` 保留群身份、正式终态和原始群消息。成员工具调用来自本次精确 Session 的原始日志，不从工具结果反推。
- 执行以下命令保存评测结果与报告；方括号表示可选参数，不要原样输入：

  ```bash
  python3 scripts/bench.py finalize --run-dir <运行目录> --report <报告.md> [--assessment <评估.json>]
  ```

  该脚本校验评分并保留执行事实。不要直接覆盖执行状态、代码检查结果或被测团队成本。

以 BCS 正式收卷为准：根 Session 已完成、output 非空且无错误。看似最终答案的回复不等于正式完成；即使已有合理答案，超时仍然是超时，不进行质量评分。不能用 Manager 的转述替代 Worker 的真实回复。
