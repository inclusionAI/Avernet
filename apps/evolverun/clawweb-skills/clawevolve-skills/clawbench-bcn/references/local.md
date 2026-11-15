# 本地运行

在 Avernet 仓库根目录执行（Python 需安装 PyYAML；Node >=22.19、OpenClaw 及 BCN 插件构建需提前准备；服务依赖/构建由 Singlebox setup 处理）：

```bash
python3 apps/evolverun/clawweb-skills/clawevolve-skills/clawbench-bcn/scripts/bench.py run --mode local \
  --bench-dir apps/evolverun/bench-case/bcn-bench-case/task_00_connectivity
```

- `--check` 只验证 Case/Profile，不读取模型配置或启动服务。
- 模型默认读取 `~/.openclaw/openclaw.json`，可通过 `--model-config` 指定；只把模型字段复制到本次成员配置。
- 输出默认 `~/.cache/clawbench-bcn/runs/<run_id>/`，可用 `--output-dir` 调整父目录。
- Backend、BCSFuse、BCS 由用户通过 Singlebox 提前准备、启动；Bench 只检查健康状态。缺失时退出并提示下方命令，端口不健康时要求排查，不会启动、停止或重启基础服务。
- 首次准备：`./singlebox/singlebox.sh setup backend bcsfuse bcs`；之后启动：`./singlebox/singlebox.sh start backend bcsfuse bcs`。不要使用无参数命令或 `all`，不会要求启动默认 Demo Agent。
- 本次评测成员按 Case/Profile 在运行目录下独立启动，结束后停止；不会停止用户启动的基础服务。
- 公共入口为 `bench.py run/finalize`；Online/Simulator 尚未实现，不会自动回退本地。旧启动入口已删除。

## 报告补全

独立评测宿主按 SKILL.md 和 `prompts/report.md` 阅读证据、生成报告，再执行：

```bash
python3 apps/evolverun/clawweb-skills/clawevolve-skills/clawbench-bcn/scripts/bench.py finalize \
  --run-dir <运行目录> --report <报告文件>
```

`llm_judge` / `hybrid` 另传 `--assessment <JSON文件>`。不改变执行终态与代码分；超时仍不评分，但要出报告。

退出码：0 完成指定操作；1 被测执行未完成；2 环境/清理或参数错误；3 运行归档完成、等待独立评测报告。退出码 3 不代表整个评测已完成。

## 输出边界

`result.json`、`report.md`、`evaluation-request.json`、`evidence/` 用于评测交接；完成后增加 `analysis-report.md`。OpenClaw 配置、服务配置及私有日志可能含凭据，不属于可上传证据。

评分条件是正式终态、证据完整且已固定，不是某个本地进程必须停止。清理详情单独记录在 `cleanup` 中；Token 缺失与日志缺失分别披露。
