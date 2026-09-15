# clawevolve-plan

`clawevolve-plan` 是 OpenClaw self-evolution 的规划阶段：**从统一的 `plan-source/v2` 生成 ClawBench templates、评测数据集、`objective.md` 与 `spec-v0.md`**。

Diagnose、Insight Improvement 和 Direct Goal 都先产出同一份 Plan Source；Plan 不再识别来源专属的输入 schema。Diagnose Source 在容器本地生成，Insight Source 通过 ClawWeb Step Input 获取，Direct Goal Source 由 Plan 内部 Agent 生成。

## 能力概览

- **统一 Plan Source**：三种 Producer 都输出 `plan-source/v2`，Resolver 负责校验、冻结并归档为 `plan/input/source.json`。
- **Diagnose**：读取本地 `plan-source.json`；默认搜索 `/home/admin/.openclaw/workspace/clawevolve_results/{task_id}/diagnose/`，不请求 ClawWeb。
- **Insight Improvement**：本地无 Diagnose Source 且未提供 Direct Goal 时，通过当前 Step Input 获取并冻结治理项 Source。
- **Direct Goal**：找不到 Diagnose 输入且 `--goal` 非空时，复用 OpenClaw Agent，将自然语言目标展开为 prospective cases，并只读发现当前 workspace 中安全、窄范围的优化 targets；Direct Goal 可规划位于已检查父目录下的待创建文件。
- 为每个 case 生成 `clawevolve.case-contract.v1` 行为契约和评分策略，再据此生成固定结构的 ClawBench Markdown template。Contract Agent 的 Prompt、归一化、校验和模板消费使用同一 canonical 结构；兼容已在线上出现的旧扁平响应，其他无效结构最多进行一次定向纠错后明确失败。
- 分别打包并严格校验 train/test Bench ZIP，默认创建、上传、发布并验证两个独立 ClawWeb Domain。
- 生成符合仓库模板契约的 `objective.md` / `objective.json` 与 `spec-v0.md` / `spec-v0.json`。
- 显式 `--goal` 始终是本轮唯一优化目标；Diagnose 中的原始请求只作为证据采集来源。自然语言中的明确指标（如 `MCP 调用成功率 80%`）会归一化为 canonical primary metric，并贯穿 objective、spec 和最终 step report，不会被默认任务成功率覆盖。
- OSS pack/publish 当前临时关闭，仅记录 skipped 结果。

## 自动 discovery

plan 脚本会自动拉起 helper agent 做 bounded discovery。Direct Goal 模式由同一次 Agent 调用同时完成 goal 分析、prospective case 设计和 workspace discovery。结果写入：

- `plan/input/discovery.json`
- `plan/input/discovery_notes.md`
- `plan/input/direct_goal.json`（仅 Direct Goal，Agent 原始结构化输出）
- `plan/input/source.json`（三种来源共用的标准 Plan Source）

调用方不需要手写 discovery notes，也不需要传 `--target`。Plan 阶段对目标 workspace **只读**：除自己的 `plan/input/`、`plan/output/` 规划产物外，不创建或修改目标 Skill、脚本或配置。target 校验的目的不是让 Plan 立即改文件，而是把后续 patch/evolve 的权限边界建立在真实检查结果上。

Direct Goal discovery 使用三类互斥路径：

- `merged_targets`：已存在、位于 workspace 内、实际检查过的安全操作目标或范围；新建场景使用最近的现有父目录，并标记 `target_type=creation_scope`。
- `reference_files`：已存在、实际检查过的只读参考文件；不得同时作为修改 target。
- `planned_deliverables`：当前尚不存在、由后续 patch/evolve 创建的未来交付物；必须声明 `operation=create`，并位于一个已验证的 `creation_scope` 下。

因此，新 Skill 的未来 `SKILL.md` 不会因为“不存在”被误当成普通 target 拒绝，也不会为了通过 Plan 而预创建目录或文件。脚本仍会拒绝越界、路径穿越、已存在的 create 路径、生成产物、judge/scorer、secrets 或生产边界。Direct Goal 的 prospective cases 只代表未来验证场景，不得被描述为历史 session 或 Diagnose 证据。用户在 goal 中明确指定 bench template 数量或任务成功率时，Plan 必须精确保留并校验这些数值。

## 线上 Slash Command

```text
/clawevolve-plan --task-id <task-id> --step-id <step-id> --goal "<自然语言目标>"
```

只有同 task id 下不存在 Diagnose handoff 时才进入 Direct Goal。自然语言必须完整放入 `--goal`；不要写成位置参数。CLI 同时支持普通 argv 和“完整 slash command 作为单个参数”的线上运行时调用方式。

## 最小使用方式

始终从目标 Bot workspace 运行；不要切换到 Skill 目录。

```bash
cd "${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}" && \
bash skills/clawevolve-plan/scripts/run.sh \
  --task-id <task-id> \
  --step-id <step-id> \
  --goal '新增一个数据预处理 Skill，使目标任务完成率达到 90%'
```

该命令会自动选择输入模式：

1. 找到 Diagnose `plan-source.json`：校验并快照到 `plan/input/source.json`；
2. 否则 `--goal` 非空：进入 Direct Goal 并生成 `plan/input/source.json`；
3. 否则：从 ClawWeb Step Input 获取 Insight Improvement Source。

Diagnose 产物位于自定义目录时，才额外传 `--run-dir <diagnose-output-dir>`。Direct Goal 不新增参数，且在没有 Diagnose 输入时要求 `--goal` 非空。

## 常用参数

| 参数 | 说明 |
|---|---|
| `--run-dir` | diagnose 输出目录，目录下应有唯一 `plan-source.json`；可省略。省略时读取 `/home/admin/.openclaw/workspace/clawevolve_results/{task_id}/diagnose/` 并递归查找。 |

| `--task-id` | ClawWeb task ID，必填，用于 step report 上报。 |
| `--step-id` | ClawWeb step ID，必填，用于 step report 上报。 |
| `--goal` | 用户自然语言优化目标。只要非空，就覆盖 Diagnose 的证据采集意图并成为 objective/spec 的唯一业务目标；Direct Goal 模式下必须非空。 |
| `--discovery-notes` | 内部兼容参数；通常省略，由脚本自动生成。 |
| `--target` | 内部兼容参数；通常省略，由脚本自动生成。 |
| `--overwrite` | 可选。默认只在 objective/spec、contracts、templates、manifest、ZIP 和输入清单均完整且相互一致时复用，返回 `status=already_exists`；半成品或损坏产物会安全重建。显式传入则重新生成。 |

## 输出位置

默认输出到：

```text
/home/admin/.openclaw/workspace/clawevolve_results/{task_id}/
└── plan/
    ├── input/
    │   ├── source.json                 # 本轮统一 Plan Source 快照
    │   ├── source-descriptor.json      # Source 摘要与 digest
    │   ├── direct_goal.json            # Direct Goal Agent 原始输出
    │   ├── discovery.json
    │   └── discovery_notes.md
    └── output/
        ├── input_manifest.json
        ├── objective.md
        ├── objective.json
        ├── clawevolve-plan.log
        ├── templates/
        │   ├── opt/
        │   │   └── task_*.md
        │   ├── val/
        │   │   └── task_*.md
        │   └── manifest.json
        ├── clawbench_dataset.zip
        ├── clawbench_train_dataset.zip
        ├── clawbench_test_dataset.zip
        ├── clawbench_manifest.json
        ├── case_contracts.json
        ├── case_contract_audit.json
        ├── clawweb_upload_result.json
        ├── clawweb_step_report_result.json
        ├── oss_upload_result.json
        ├── spec-v0.md
        └── spec-v0.json
```

说明：

- `plan/output/templates/opt/task_*.md`：训练集/优化集（train/opt）的 ClawBench 评测模板。
- `plan/output/templates/val/task_*.md`：测试集/验证集（test/val）的 ClawBench 评测模板。
- `plan/output/clawbench_dataset.zip`：包含 train/test 的聚合模板包，保留 `opt/`、`val/` 相对路径，供本地兼容消费者使用，不直接上传 Domain。
- `plan/output/clawbench_train_dataset.zip`：仅包含 `opt/task_*.md`，用于创建 train Domain。
- `plan/output/clawbench_test_dataset.zip`：仅包含 `val/task_*.md`，用于创建 test Domain。
- `plan/output/case_contracts.json`：所有 case 经归一化和严格校验后的唯一 canonical contract；模板层不消费 Agent 原始扁平结构。
- `plan/output/case_contract_audit.json`：逐 batch 记录 canonical/legacy 归一化方式、一次受控纠错及失败原因，便于线上定位格式漂移。
- `plan/input/`：本轮实际使用的统一 Source 快照和 discovery 产物；Direct Goal 额外保留 Agent 原始输出。
- `plan/output/input_manifest.json`：记录本轮实际使用的 Source/Diagnose/Direct Goal 输入与 discovery notes。
- `plan/output/clawweb_upload_result.json`：记录 train/test 两个独立 Bench Domain 的创建、上传、发布和验证结果；仅当两个 Domain 都完整验证后 Plan 才可进入后续 patch loop。
- `plan/output/clawweb_step_report_result.json`：上报 `{clawweb_url}/api/evolve/internal/tasks/{task_id}/steps/{step_id}/report` 的结果；`clawweb_url` 由任务参数 `--clawweb-url` 冻结传入。
- `plan/output/oss_upload_result.json`：当前 OSS pack/publish 主流程临时关闭，写入 `status=skipped` 作为占位，后续恢复测试。
- `plan/output/objective.md`：本轮优化目标。
- `plan/output/spec-v0.md`：后续 patch loop 使用的策略规格；对应 JSON 同时包含 `planning_basis`（规划依据）、`plan_summary`（用户可读摘要）和 `goal_fidelity`（用户约束保真检查）。
- `objective/spec` 只把 discovery 实际确认的文件或目录列为允许更新目标或创建范围；抽象的 retry、prompt、参数校验等建议仅作为 optimization topics，不会扩大授权边界。附录只保留必要审计摘要，不展开全部 agent/session 路径。
- 如果相同 `--task-id` 已经生成过 plan，默认返回已有路径而不覆盖，防止 agent 误重跑造成产物漂移；若本地缺少 `plan/output/oss_upload_result.json`，当前只补写 skipped 结果，不执行 OSS pack。

## 使用边界

plan 可以生成评测模板、目标和策略建议，但不会实际修改目标 workspace；新文件只会记录为 `planned_deliverables`，由后续 patch/evolve 创建。plan 不应修改：

- judge/scorer 逻辑
- 已上传的 ClawWeb domain 数据
- 生产配置、密钥、权限边界
- 全局 runtime 或共享依赖

## 推荐完整流程

```text
1. Diagnose / Insight Improvement / Direct Goal 产出同一个 `plan-source/v2`
2. Diagnose/Insight：Agent 做 bounded discovery
   Direct Goal：Agent 同时生成 goal 分析、prospective cases 与 discovery
3. 为每个 case 生成个性化行为契约与评分策略
4. 严格按既有 Markdown 模板生成 templates、objective.md、spec-v0.md
5. 分别打包并发布 train/test Bench Domain，验证成功后通过最终 step report 回传两个 Domain
6. 根据 spec-v0.md 进入 patch / evolve
```


## ClawWeb Bench 双 Domain 发布

Plan 默认将模板按逻辑 split 独立发布：

- `clawbench_train_dataset.zip`：仅包含 `opt/` 下的 train templates；
- `clawbench_test_dataset.zip`：仅包含 `val/` 下的 test templates；
- 两个 ZIP 分别创建、上传并发布到不同的 ClawWeb Domain；
- 最终 step report 的 `benchDomains` 分别回传 train/test Domain ID 与完整 URL；
- 任一 Domain 缺少模板、发布失败或验证失败时，Plan 不会进入 patch loop，也不会回传部分或虚假的 Domain ID；
- `--skip-clawweb-report` 用于纯本地测试，会关闭 Step Input、Domain 和 step-report 的全部 ClawWeb 网络；因此必须已有可验证的本地 Diagnose/Direct Goal Source；
- Manifest 与 ZIP 按不可信缓存校验，拒绝路径逃逸、split 混用、嵌套/重复/加密/符号链接 entry；
- 命令级终态门禁保证远端 reporter 最多调用一次，final 后发生本地写盘错误也不会再上报矛盾的 failed。
