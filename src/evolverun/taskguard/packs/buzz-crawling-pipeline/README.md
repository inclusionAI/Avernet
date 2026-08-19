# buzz-crawling-pipeline

外部舆情策略更新端到端工作流 Pack。它只编排六个 `embedded-agent` 节点；每个节点由 TeClaw 发现并调用对应 Skill，通过同一 bot 的共享 workspace 文件协作。

## 业务链路

```text
signal-acquire
    -> data-collect: filtered_records.json
    -> risk-review: passed_records.json
    -> instruction-update: report.md
    -> keywords-update: keywords.json + hits.json
    -> summary-report: summary.md [all_done]
```

`summary-report` 同时依赖 `instruction-update` 和 `keywords-update`，以 `all_done` 汇合；没有本地 Python 执行器或本地 shell 节点。

## 文件协议

所有路径相对于 TeClaw workspace，而非 ClawMind 本机目录。一次运行由 `flowId` 隔离：不同 `flowId` 可以并发，`duplicatePolicy=allow`，不会互相读取或覆盖彼此的 `runs/<flowId>/` 产物。

```text
/home/admin/.teclaw/workspace/
└── workflows/runs/<flowId>/
    ├── signal-acquire/report.md
    ├── data-collect/filtered_records.json
    ├── risk-review/passed_records.json
    ├── instruction-update/report.md
    ├── keywords-update/keywords.json
    ├── keywords-update/hits.json
    └── summary-report/summary.md
```

TeClaw 必须先具备同一 bot 跨 Agent session 共享 workspace 的能力；否则后继 Skill 无法可靠读取前序节点文件。`write_file` 会自动创建父目录，因此 Pack 不添加 `mkdir` 节点或本地 shell。历史 `runs/` 本期不清理，保留用于回溯和排障。

所有文件工具参数使用 `workflows/...` 相对路径，不拼接 `$WORKSPACE_DIR`。TeClaw `write_file` 会把相对路径落到当前 bot 的 workspace 并自动创建父目录；`$WORKSPACE_DIR` 只用于 TeClaw shell 命令的环境变量，不属于文件工具参数。

`buzz-shared/keywords/` 中的 variant 词表属于共享业务状态，不受 `flowId` 隔离。本期不加锁；如发生并发写冲突，阻断切流并单独治理，不将其伪装为单次运行失败后重试可解决的问题。

## 部署与触发

本版本的 Pack 与六个 required Skills 使用同一 `workflows/runs/<flowId>/` 路径契约，必须同批发布。发布顺序固定为：先发布六个 Skills，再发布 Pack 0.4.0，最后由业务侧执行端到端验收。

required Skills 共六个：

- `buzz-crawling-signal`
- `buzz-data-collect`
- `buzz-risk-review`
- `buzz-instruction-update`
- `buzz-keywords-update`
- `buzz-summary-report`

用户通过 `/buzz-crawling run` 启动工作流。运行期由 TeClaw plugins 将请求派发给 ClawMind 的 `workflow_engine_dispatch`，随后进度经 `chat.inject` 回到 WS；节点的 `displayMarkdown` 是用户可见结果的一部分。

## Prompt、Skill 与输出契约

六个节点使用薄 Prompt：只传用户请求、结构化参数、上游结果、业务常量、flow 相对路径，并列出 ClawMind 消费的成功 JSON 字段。详细业务步骤、默认值、文件写入/回读、flow 一致性和失败 JSON 由对应 Skill 定义。

`signal-acquire` 同时接收 `input.message` 与 `input.params`。只有各 Skill allowlist 内的结构化非空值可以覆盖用户请求或 Skill 默认值；固定业务字段、上游结果和文件路径以 Prompt 显式值为权威，未知结构化键忽略。其余节点仍接收完整 `input.params`，但只能消费各自公开的 `run_date` / `run_hour` 等 allowlist 字段。

业务失败由 Skill 返回顶层 `status=FAILED` 与 `errorMessage`，ClawMind 在解析 JSON 后将其判定为 executor failure。YAML `outputContract` 只做成功结果的后置字段、类型和枚举校验，不会作为生成指令传给 TeClaw。

信号风险事件写入、复核追溯表写入/验证、知识表清理/写入/验证等核心 ODPS 操作失败时，Skill 必须返回顶层 `status=FAILED` 与 `errorMessage`。`instruction-update.write_status` 的普通成功值只允许 `succeeded` 或 `skipped`。

所有节点成功 JSON 都包含 `displayMarkdown`，用于在交互界面展示阶段结果。仓库本地只执行契约单测；端到端部署、真实搜索、ODPS、CCT 和聊天可见性由业务侧手动验收。

## 输出

- `report_path`：`summary-report` 写入的汇总报告路径，public。
- `final_event_count`：`signal-acquire.event_count`，public。
