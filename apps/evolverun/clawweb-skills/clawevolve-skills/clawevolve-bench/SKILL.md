---
name: clawevolve-bench
description: 以 Python workflow 方式完整运行一次 ClawWeb Bench，不依赖 ClawMind workflow；可由 ClawEvolve Step handler 或 Optimize 调用。
---

# ClawEvolve Bench

本 Skill 提供与 Task/Step 无关的 Bench workflow 产品能力。独立 Bench Task 的 Message
模式由统一 Step handler 适配：

```text
python3 -u -B ${SKILL_BASE_DIR}/clawevolve-workflow/scripts/handlers/clawevolve_bench_run.py <原始命令参数>
```

handler 再调用 `${SKILL_BASE_DIR}/clawevolve-bench/scripts/clawbench-workflow.py`。本 Skill 不复制 Step 上报逻辑。

Optimize 等内部调用方直接使用 Python CLI，不经过 Step handler：

```bash
python3 -u -B ${SKILL_BASE_DIR}/clawevolve-bench/scripts/clawbench-workflow.py run \
  --owner-id <bench-owner-id> \
  --domain-id <domain-id> \
  --model <model> \
  --suite <suite> \
  --scene <scene> \
  --work-dir <absolute-work-dir>
```

CLI 第一次运行时自动解析 Domain 当前已发布模板，并在 `work-dir` 的父目录创建
`bench_context.json`；后续不同 `work-dir` 自动复用相同版本。高级场景可重复传入
`--template <name>@<version>` 或用 `--context-dir` 指定共享 context。CLI 在 `work-dir`
内部创建 input/state/result/output/logs，stdout 最后一行输出结果 JSON；它不读取或上报
Evolve Step。

Workflow 在评测结果上传后会调用 `clawbench-report` 生成报告。实现会创建或复用专用
`clawbench-report` OpenClaw Agent，并为每次 Bench Run 使用独立 Session；报告生成结果、原始
输出和 Session 标识最终由 `summarize` 写入对应 Bench Run。`--no-report` 仅用于明确关闭报告。

## 独立 Step 命令

```bash
python3 -u -B ${SKILL_BASE_DIR}/clawevolve-workflow/scripts/handlers/clawevolve_bench_run.py \
  --task-id <task-id> \
  --step-id <step-id> \
  --domain-id <domain-id> \
  --owner-id <owner-id> \
  --model <model> \
  --suite <suite> \
  --scene <scene>
```

命令参数统一采用 ClawEvolve 的 kebab-case 规范：`--domain-id`、`--template-name`、`--template-version`、`--owner-id`、`--model`、`--suite`、`--scene`、`--judge`。ClawWeb 会传入前七个非空参数，并在配置了 judge 时传入 `--judge`；handler 会与 Step Input 逐项校验。

原 workflow 的 `judgeBaseUrl`、`judgeApiKey` 和 `outputDir` 不进入命令行：judge 地址和密钥通过 Step Input 中的环境变量引用解析，输出目录由 task-id/step-id 固定生成。完整冻结模板列表和运行配置也只以 Step Input 为准。该 Skill 不接受 camelCase 或旧参数别名。

## 输出

本地状态写入：

```text
/home/admin/.openclaw/workspace/clawevolve_results/<task-id>/bench/<step-id>/
```

直接 Python CLI 的最终结果写入 ClawWeb Bench Run 并返回调用方；只有 Step handler 会把它
上报到当前 ClawEvolve Step。

## 约束

- 不依赖 ClawMind、workflow engine、facade binding 或 `WORKFLOW_ENGINE_*`。
- 完整保留原生产 Workflow 的版本检查、模板加载、Run 创建、Benchmark、上传、报告和汇总流程。
- 不把 judge API Key 写入参数文件、日志或 Step Output。
- 不修改 Bench 模板、评分器或真实 Bot workspace。
- 不创建下一 Step，不执行 Bench Plan 或 Optimize。
