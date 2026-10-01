# clawevolve-tune Protocol Reference

`clawevolve-tune` 只负责一轮中的调优执行，不负责 spec 演化、validation 判定或 rollback。

## Run Binding

每次 evolve loop 必须绑定唯一 `evolve_run_id`。不要使用全局 `round0`，不要扫描最新目录。

```text
run_dir   = /home/admin/.openclaw/workspace/clawevolve_results/<evolve-run-id>
round_dir = /home/admin/.openclaw/workspace/clawevolve_results/<evolve-run-id>/optimize/output/round-{N:03d}
```

推荐传递方式：调用命令显式参数 > `EVOLVE_RUN_DIR` / `EVOLVE_ROUND_DIR` > `run_manifest.json`。

## Spec Version Rule

Round N consumes `input/spec-v{N-1}.md`. Do not use unversioned spec aliases.

## Required Inputs

```text
{run_dir}/optimize/input/objective.md
{round_dir}/input/spec-v{N-1}.md
{round_dir}/round_state.json    ← bench.optimization 字段含 score/summary/resultPath
```

Raw benchmark report at `bench.optimization.resultPath` (in `clawbench_results/`).

## Required Outputs

```text
{round_dir}/tune/tune_report.md
{round_dir}/tune/changed_files.txt
{round_dir}/tune/diff.patch        # 如果可生成
```

## Boundary

- Do not consume `validation_result.json`, `metrics.json`, or previous accepted validation score.
- Do not modify objective, cases, bench output, scoring logic, or historical artifacts.
- MCP optimization is invocation-layer only: selection, command, parameters, sequence, fallback, and result consumption. Do not modify MCP server/tool implementation.

## Bench Data

runner v4 不再产出 `{round_dir}/bench/` 目录或 `optimization_result.json`。
bench summary inline 在 `round_state.json` 的 `bench.optimization` 字段：
- `summary.score` / `summary.pass_rate` / `summary.total` 等
- `resultPath` → 原始 `*_benchmark_report.json`（含逐任务 cases、breakdown、grading）
- `benchRunId` / `benchDir` / `logPath` → bench 运行元数据

如需逐任务 transcript，在原始 report 同目录下查找 `{run_id}_transcripts/{task_id}.jsonl`。
