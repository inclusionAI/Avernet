# clawbench-bcn

由独立评测 Agent 执行的 BCN 群协作评测 Skill。

- **当前能力**：本地 POC、成套 Case/Profile、隔离的 Manager/Worker、证据回收、代码检查及评估交接。
- **后续规划**：通过远程 API 管理被测团队生命周期并回收证据；当前版本尚未实现远程模式。
- [本地使用说明](references/local.md)
- [评测 Agent 操作说明](SKILL.md)
- 评测资产位于 Avernet 仓库的 `apps/evolverun/bench-case/bcn-bench-case/`，不内置在 Skill 中。

## 用户使用流程（本地模式）

整体顺序：**准备环境 → 用户启动基础服务 → 运行 Bench → 查看结果 → 补全分析报告**。
所有命令均在 Avernet 仓库根目录执行。

### 1. 准备环境

需要 Python 3、Node.js >=22.19、可在 PATH 中调用的 OpenClaw，以及已构建的 BCN 插件。
本地 `~/.openclaw/openclaw.json` 需配置可用的主模型和凭据；Bench 默认从这里读取模型配置。

```bash
python3 -m pip install -r apps/evolverun/clawweb-skills/clawevolve-skills/clawbench-bcn/requirements.txt
```

首次准备基础服务，或更新代码涉及依赖/构建变化时，使用 Singlebox：

```bash
./singlebox/singlebox.sh setup backend bcsfuse bcs
```

已准备好环境则不必每次 setup。**当前已知 `setup bcsfuse` 可能因目标配置路径未初始化而报 `.env.example` 自复制错误**，本 Skill 未修改 Singlebox。遇到该错误需先解决环境准备问题；如果依赖已经准备好，可以尝试下一步 start，但仍须确认服务启动成功。

### 2. 启动基础服务

```bash
./singlebox/singlebox.sh start backend bcsfuse bcs
```

只指定 Backend、BCSFuse、BCS，**不要使用无参数命令或 `all`，避免启动默认 Demo Agent**。
按 Singlebox 提示选择模型配置；如需复用个人 OpenClaw 的模型配置，选择 home。
启动失败时先查看 Singlebox 输出，不继续评测。

Bench 只检查以上服务是否健康：健康则复用，未启动则提示 setup/start 命令并退出；端口被占用但不健康时提示排查，不杀进程。Bench 不自动准备、启动或停止这些基础服务。

### 3. 运行两成员连通性评测

```bash
python3 apps/evolverun/clawweb-skills/clawevolve-skills/clawbench-bcn/scripts/bench.py run --mode local \
  --bench-dir apps/evolverun/bench-case/bcn-bench-case/task_00_connectivity
```

`--bench-dir` 指向成套的 Case 和 Profile，连通性案例只包含 Manager、Worker 两个被测 Agent，无需手动启动它们。

脚本依次执行：

1. 检查基础服务。
2. 按 Profile 创建本次隔离的被测 Agent。
3. 建群并一次性将 Case Prompt 提交为协作目标。
4. 等待完成或超时，清理本次评测 Agent/群并回收证据。
5. 执行代码评分，输出结果和独立评测交接文件。

结束后基础服务继续运行，不受 Bench 清理影响。

可选参数：`--check` 仅校验 Case/Profile，不启动进程或检查服务；`--model-config <文件>` 指定模型配置；`--output-dir <目录>` 指定运行输出父目录。

### 4. 查看结果

终端会打印本次运行目录，默认为：

```text
~/.cache/clawbench-bcn/runs/<run_id>/
```

| 文件 | 用途 |
| --- | --- |
| `runner.log` | 执行进度与异常提示 |
| `result.json` | 执行状态、评分、证据完整性及清理结果 |
| `report.md` | 运行结果摘要 |
| `evidence/` | 群消息、成员会话等评测证据 |
| `evaluation-request.json` | 交给独立评测 Agent 的报告生成请求 |

连通性案例通过时，应看到执行状态 `completed`、评分完成、得分 `1.0`。超时或证据条件不满足时不会将其算作通过，也可能不评分，但仍交接分析报告。

退出码 `3` 表示运行已归档、等待独立评测报告，不等同于服务启动失败；`1` 表示被测执行未完成，`2` 表示环境、清理或参数错误，`0` 表示当前操作完成。

### 5. 补全分析报告

`run` 不会自动启动独立评测 Agent。将本次 `evaluation-request.json` 交给独立评测 Agent，按 [SKILL.md](SKILL.md) 和 [报告提示词](prompts/report.md) 阅读证据并生成报告，然后保存：

```bash
python3 apps/evolverun/clawweb-skills/clawevolve-skills/clawbench-bcn/scripts/bench.py finalize \
  --run-dir <本次运行目录> \
  --report <生成的报告文件>
```

完成后生成 `analysis-report.md`。`llm_judge` / `hybrid` 案例另需 `--assessment <评分JSON文件>`；补全报告不会把超时任务改成成功，也不会覆盖代码评分。

### 6. 不再使用时手动停止基础服务

确认没有其他任务使用这些服务后执行：

```bash
./singlebox/singlebox.sh stop bcs bcsfuse backend
```

不要上传本次运行目录内的 OpenClaw 配置或私有日志，其中可能包含凭据。对外分享前需审查评测证据和报告。

## Case 与 Profile 的写法

Case 沿用单 Agent 的 Markdown 模板：YAML 元数据和 `Prompt`、`Expected Behavior`、`Grading Criteria`、`Automated Checks`、`LLM Judge Rubric` 等章节。按评分类型提供必要章节；Case 不填写 BCN 专属入口成员或重复的评分维度声明。

```text
<bench-dir>/
├── case.md
└── profiles/
    ├── bots.json       # 成员配置和 entry_member，例如 manager
    ├── manager/        # 完整 Agent Profile
    └── worker/
```

`profiles/bots.json` 的 `entry_member` 必须指向 bots 中的一个 profile 别名。Case/Profile 成套维护，评测资产继续放在 `apps/evolverun/bench-case/bcn-bench-case/`。

| grading_type | 必需的评分内容 | 最终分 |
| --- | --- | --- |
| automated | Automated Checks 的 Python grade 函数 | 代码检查项平均分 |
| llm_judge | LLM Judge Rubric，或 Grading Criteria | Judge 返回的 total |
| hybrid | 代码检查和模型评分依据 | 代码与模型分加权，默认各 50% |

`grading_weights` 可选，例如 `{automated: 0.1, llm_judge: 0.9}`；正权重按总和归一化。LLM 内部维度权重写在 Rubric，与上述代码/模型权重区分。

代码 Judge 保持 `grade(transcript: list, workspace_path: str) -> dict`。transcript 使用原模板的 `type/message/role/content` 消息列表，附成员与 Session 信息，工具调用参数为 `params`。workspace_path 指向本次评测 artifacts，其中 final.md 为正式交付，bcn-evidence.json 提供群身份、终态和原始群消息；不是某一个成员的工作目录。工具调用的语义和可用文件仍由具体任务决定，不保证任意单 Agent 评分代码在群中都可直接使用。

当前 `workspace_files` 仅支持空列表；非空会明确报未支持，多成员材料分发后续再实现。

模型评估 JSON 示例（依据 Rubric 的实际维度和权重填写）：

```json
{
  "scores": {"quality": 1.0, "completeness": 0.5},
  "total": 0.8,
  "notes": "示例权重60%/40%；说明证据与扣分依据。",
  "evidence_notes": "说明已审阅的证据范围。"
}
```

`total` 必须为 0～1 的有限数值；缺失或非法会拒绝 finalize，保留待评状态。程序不会用维度算术平均覆盖 total。超时、证据不完整或已完成代码评分的 automated 案例只补报告，不提交模型分。

## 运行单元测试

在 Avernet 仓库根目录执行：

```bash
python3 -m pip install -r apps/evolverun/clawweb-skills/clawevolve-skills/clawbench-bcn/requirements.txt
```

```bash
python3 -m unittest discover -s apps/evolverun/clawweb-skills/clawevolve-skills/clawbench-bcn/tests -v
```

## 执行与报告的区别

执行已归档，不代表评测报告已完成。`evaluation-request.json` 交给独立评测 Agent 处理；`bench.py finalize` 保存其报告，同时保留执行状态和代码评分。超时任务仍不评分，但也必须生成报告。

## 代码边界

- `scripts/bench.py`：公共入口与装配。
- `scripts/core/`：Case/Profile、运行编排、评分及独立评测交接。
- `scripts/adapters/local/`：本地服务、Agent、BCN 调用和证据获取。
- `scripts/outputs/`：文件读写与报告保存。

Core 仅依赖 `core/contracts.py` 中的运行与输出约定，不直接导入 Local Adapter。
旧入口不再保留；Case 格式及有效评分规则继续复用。范围仅限本 Skill 和其测试资产，不修改被测 BCN/Singlebox。
