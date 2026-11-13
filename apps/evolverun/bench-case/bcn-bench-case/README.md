# BCN 评测案例

[case.template.md](case.template.md) 用于人工编写或后续自动生成 Case，格式沿用单 Agent 模板。模板含占位内容和未实现的代码 Judge，不能直接作为正式案例运行。

一套完整评测资产：

```text
<任务目录>/
├── case.md
└── profiles/
    ├── bots.json
    ├── manager/
    └── worker/
```

生成步骤：

1. 复制模板为 `<任务目录>/case.md`，填写任务、预期行为和评分规则。`id` 使用字母、数字、下划线或连字符，并与任务目录命名保持一致。
2. 按任务选择 `automated`、`llm_judge` 或 `hybrid`。实现所需代码 Judge，填写具体 Rubric；替换全部占位内容，删除生成说明注释及不适用章节。Hybrid 默认代码/模型各50%，可用 `grading_weights` 调整；Rubric 内部权重另行定义并合计100%。
3. 配套完整 Profile：可从下方现有案例复制再调整。`profiles/bots.json` 的 `entry_member` 指向一个成员的 `profile` 别名，每个 `source` 目录至少包含 AGENTS.md、IDENTITY.md、KNOWLEDGE.md，并保留该成员实际需要的其他资源。Case 不定义入口成员，不重复声明模型评分维度。
4. 先用 `--check` 验证结构，再在基础服务已启动的环境执行。结构校验不会判断任务质量、占位内容或 Judge 业务正确性；生成器必须先完成这些内容。

在 Avernet 根目录校验：

```bash
python3 apps/evolverun/clawweb-skills/clawevolve-skills/clawbench-bcn/scripts/bench.py run --mode local \
  --bench-dir apps/evolverun/bench-case/bcn-bench-case/<任务目录> --check
```

实际评测移除 `--check`。完整启动、查看结果和报告流程见 [Skill README](../../clawweb-skills/clawevolve-skills/clawbench-bcn/README.md)。

已有案例：

| 案例 | 目标 | 评分方式 |
| --- | --- | --- |
| [task_00_connectivity](task_00_connectivity/case.md) | 两成员派发、回复与正式完成 | automated |
| [task_01_singlebox_feedback_plan](task_01_singlebox_feedback_plan/case.md) | 五成员共同制定业务方案 | hybrid |

Case/Profile 成套生成和维护。当前 `workspace_files` 仅支持空列表；评分依据只保留在评测端，只有 Prompt 下发给被测群。
