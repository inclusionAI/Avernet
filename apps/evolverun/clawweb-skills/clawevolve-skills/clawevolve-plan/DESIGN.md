# clawevolve-plan 设计文档

## 1. 模块定位

`clawevolve-plan` 是只读的自进化规划产品：根据统一的 `plan-source/v2` 生成后续进化所需的目标文档、spec 和 ClawBench 评测集。

1. Diagnose、Insight Improvement 和 Direct Goal 分别生产同一个 `plan-source/v2`；Direct Goal 在没有上游 evidence 时把用户目标展开为 prospective cases。
2. 使用 helper agent 只读检查目标 workspace，建立可信的更新边界、创建范围和只读参考依据。
3. 将所有输入归档到 `plan/input/`，所有规划产物写到 `plan/output/`。
4. 根据 evidence cases 或 prospective cases 生成并校验 canonical case contracts，再渲染 train/test ClawBench templates 和独立 ZIP。
5. 创建并发布 train/test 两个独立 ClawWeb Domain，严格验证各自模板全集。
6. 生成 `objective.md/json`、`spec-v0.md/json`，清晰说明目标、规划依据、未来交付物、允许范围和验收标准。
7. 校验用户目标中的数值硬约束，避免 template 数量、成功率等要求在规划过程中丢失。
8. 全流程不发送 running 或中间状态，只在任务终态向 ClawWeb 回传一次包含 goal、spec、bench cases 和两个 bench domains 的 JSON。

Plan 是需求/证据与后续 patch/evolve 之间的规划层。它不修改目标 workspace，不预创建未来文件，也不把 Direct Goal 的 prospective cases 冒充历史 session 事实；实际文件变更由后续进化阶段执行。

## 2. 输入参数与默认路径

关键 CLI 参数：

| 参数 | 是否必需 | 默认值 | 作用 |
| --- | --- | --- | --- |

| `--task-id` | 是 | 无 | ClawWeb task id，用于 step report；必须保持原样。 |
| `--step-id` | 是 | 无 | ClawWeb step id，用于 step report；必须保持原样。 |
| `--run-dir` | 否 | `/home/admin/.openclaw/workspace/clawevolve_results/{task-id}/diagnose/` | diagnose 输出目录或其上层目录，内部递归搜索唯一 `plan-source.json`。 |
| `--goal` | 条件必需 | 空 | 用户当前优化目标；Direct Goal 模式必须非空。非空时是当前业务目标的最高优先级，Diagnose intent 仅保留为 evidence provenance；为空时才继承 Source/Diagnose 的默认优化目标。 |
| `--discovery-notes` | 否 | 自动生成 | 内部兼容参数；通常由脚本拉起 helper agent 生成。 |
| `--target` | 否，可重复 | 自动生成 | 内部兼容参数；通常由脚本拉起 helper agent 生成。 |
| `--overwrite` | 否 | false | 是否覆盖已有 plan 结果；默认幂等复用已有产物。 |

输出布局固定为：

```text
/home/admin/.openclaw/workspace/clawevolve_results/{task-id}/plan/input
/home/admin/.openclaw/workspace/clawevolve_results/{task-id}/plan/output
```

线上触发 skill 时，agent 必须把 slash command 中收到的 id 和参数原样传给脚本，不能把 `EV-xxx` 改成 `EV_xxx`，不能改写 `STEP-xxx`。

## 3. 输入归档设计

Plan 的所有输入都必须落在 `plan/input/` 下，便于重跑、审计和归档：

| 输入 | 来源 | 归档位置 | 说明 |
| --- | --- | --- | --- |
| `source.json` / `source-descriptor.json` | Diagnose 本地 Source、ClawWeb Step Input 或 Direct Goal | `plan/input/` | 三种 Producer 共用的 canonical Source 快照与 digest descriptor；Resolver 校验 schema/digest，已有文件不匹配时拒绝覆盖。 |
| `direct_goal.json` | Direct Goal Agent | `plan/input/` | Direct Goal 的原始结构化分析；标准化结果仍写入统一 `source.json`。 |
| `discovery.json` / `discovery_notes.md` | helper agent 对目标 workspace 的只读检查结果 | `plan/input/` | 用户无感；记录修改目标、创建范围、只读参考和检查依据。 |

`plan/output/input_manifest.json` 记录每个输入的 source/path/label，用来证明本次 plan 到底使用了哪些输入。

注意：`discovery_notes.md` 是 plan 的输入，不是输出。它由脚本内部 helper agent 生成，随后脚本归档到 `plan/input/`。

## 4. 主流程

`clawevolve_plan.cli.main()` 的真实链路如下：

1. 解析 CLI 参数，校验 `task-id/step-id` 字符集；使用原样 `--task-id` 作为固定任务目录 ID。
2. 创建 `plan/input/` 和 `plan/output/`，配置 `plan/output/clawevolve-plan.log`。
3. 不发送任何 `running` 或中间状态；命令级 `FinalStepReportGate` 为整次运行提供最多一次终态上报门禁。
4. 选择 Producer：优先使用本地 Diagnose `plan-source.json`；不存在且 `--goal` 非空时进入 Direct Goal；否则从当前 Step Input 获取 Insight Improvement Source。
5. Resolver 把选中的 canonical Source 校验并冻结为 `source.json` 与 `source-descriptor.json`。Diagnose 不联网；Insight 首次从 Step Input 获取；Direct Goal 直接生成。重试优先复用本地快照，本地文件被修改或 digest 不同则拒绝覆盖。
6. 如果已有 `objective.md` 和 `spec-v0.md` 且未指定 `--overwrite`，复用已有结果，并重新执行必要的双 Domain 完整性检查后回传 final step report。
7. Source/Diagnose 模式调用 bounded discovery；完整 `plan-source/v2` 只保留在冻结的 `plan/input/source.json`，Discovery `--message` 只传 JSON quoting 后的绝对路径、固定指令和少量导航统计。Agent 按字段或分块读取 Source，初次与一次纠错 Prompt 均在启动 OpenClaw 前执行 UTF-8 32 KiB 硬限制。Direct Goal 模式由同一次 Agent 调用完成目标分析、prospective case 设计和 workspace discovery。
8. 归档 canonical Source、`discovery.json` 和 `discovery_notes.md`，写 `input_manifest.json`，并通过唯一、无来源分支的 `PlanningContext` 投影进入 Plan 内部。若是 cross-bot 输入，保留来源与执行目标上下文并要求 discovery 验证适用性。
9. 对更新目标、创建范围、只读参考和未来交付物做 preflight 校验；检查其位于 workspace 内、边界足够窄且角色不冲突。
10. Direct Goal 中的 prospective cases 只用于规划未来验收，不作为 Diagnose 历史证据。
11. 为每个 case 生成并校验 canonical `clawevolve.case-contract.v1`，再渲染 train/test ClawBench templates 和 ZIP。
12. 创建两个独立 Bench Domain，分别上传、发布 train/test templates，并严格验证模板全集与 source hash。
12. 写 `clawweb_upload_result.json` 和 `clawbench_manifest.json`。
13. 生成 objective 和 spec，并写到 `plan/output/`。
14. 暂时跳过 OSS pack/publish，写 `oss_upload_result.json` 标记 skipped。
15. 构造新格式 ClawWeb final report 并回传 `succeeded`。
16. 写 `clawweb_step_report_result.json` 和最终 CLI JSON。

异常时会回传 `failed` step report，并在 CLI JSON 中返回 `log_file`、`task-id`、`step-id`、错误信息。

## 5. 只读 Discovery 与路径角色

Discovery 的目标不是让 Plan 修改文件，而是让规划建立在真实 workspace 理解上。Plan 除自己的 `plan/input/`、`plan/output/` 外，对目标 workspace 保持只读。

Source/Diagnose 模式中，helper agent 阅读 evidence、failure mode、tool hints 和相关代码；Direct Goal 模式中，Agent 同时理解自然语言需求、设计 prospective cases，并检查现有实现与目录结构。产物使用四类角色：

Discovery 正常由脚本内部完成，不要求用户手工提供 notes 或 target。产物使用四类互斥角色：

- `allowed_update_targets`：已存在、实际检查过、后续允许修改的窄范围文件或目录。
- `allowed_creation_scopes`：已存在且经过验证的父目录，后续可以在其下创建新交付物。
- `reference_files`：已存在、只读的模式或实现参考，不得同时作为更新目标。
- `planned_deliverables`：当前可以不存在、由后续 patch/evolve 创建的文件或目录，必须声明 `operation=create` 并落在 creation scope 下。

Preflight 校验 discovery notes 非空、路径位于 workspace 内、现存路径确实存在、未来交付物当前不存在、notes 中提及检查对象，并拒绝路径穿越、过宽目录、生成产物、judge/scorer、secrets 和生产边界。对于 Diagnose 输入，还要求 notes 能关联诊断 failure mode。

Preflight 要求 discovery notes 非空，至少存在一个具体更新目标或创建范围，并拒绝过宽目录、路径穿越、生成产物、judge/scorer、secrets 和生产边界。现存目标与只读参考必须真实存在且经过检查；未来交付物必须当前不存在、位于已验证 creation scope 下且不能伪装成 inspected file。对于 Diagnose 输入，notes 还必须关联至少一个诊断 failure mode。

该模型保证 spec 的优化边界来自真实检查，同时解决新 Skill 场景中的核心偏差：未来 `SKILL.md` 是交付物，不是 Plan 当下要修改的现存 target；无需为了通过校验而预创建目录或文件。

## 6. ClawBench 模板生成

模板生成由 `template_builder.render_templates()` 完成：

- `case_contract.build_case_contracts()` 先要求 Agent 返回唯一的 `{"contracts": [...]}` envelope，并在 Prompt 中提供与校验器一致的完整 canonical schema。
- 线上历史扁平结构只允许通过确定性 adapter 转换到 canonical v1；下游模板层永远不直接消费两套结构。
- canonical contract 必须包含完整 `task_contract`、权重合计 100 的五档 `grading_strategy`、合法 `automated_checks`、`replayability` 和 `provenance`，并与输入 case 的 id、类型、split 和 session 来源一致。
- 第一次响应若可解析但无法通过 schema 校验，只允许一次携带具体校验错误的定向纠错；再次失败则 fail closed，并将尝试记录写入 `case_contract_audit.json`。
- `case_contracts.json` 只保存通过严格校验的 canonical contracts；扁平兼容和纠错不会扩散到 Template Builder。
- `template_id` 在 split 后一次性分配，并原样贯穿 case contract、Markdown 文件名、manifest 与 ZIP；任一环节漂移都会终止生成。

- 输入：Planning Context 的 `cases`、输出目录、可选 goal 文本。
- 输出目录：`plan/output/templates/`，其中 train/optimization templates 写入 `plan/output/templates/opt/`，test/validation templates 写入 `plan/output/templates/val/`。
- 聚合 zip：`plan/output/clawbench_dataset.zip`，供本地兼容消费者使用。
- train zip：`plan/output/clawbench_train_dataset.zip`，仅包含 `opt/task_*.md`。
- test zip：`plan/output/clawbench_test_dataset.zip`，仅包含 `val/task_*.md`。
- 每个 case 生成一个 `task_*.md`，按 split 分别落到 `opt/` 或 `val/`，格式遵循 ClawBench task markdown：YAML frontmatter + Prompt + Expected Behavior + Grading Criteria + Automated Checks + LLM Judge Rubric + Workspace Files + Additional Notes。
- `templates/manifest.json` 只保留本地，不放入上传 zip；上传 zip 只包含 `opt/task_*.md` 和 `val/task_*.md`。

模板 id 由 `case_template_id()` 生成：

- 如果 case_id 已以 `task_` 开头，直接 slug 后复用。
- 否则根据 `session_id + query + failure_mode` 计算稳定 hash，保证重复运行可追溯。

## 7. Train/Test 划分策略

Train/test 划分由 plan 独立完成，diagnose 中旧的 `case_split` 只作为输入参考，不作为最终回传依据。

`assign_train_test_splits(cases, train_ratio=0.8)` 的设计目标：

1. 默认 80% train、20% test。
2. 小样本也必须可用：
   - 0/1 个 case：不划 test。
   - 2-5 个 case：至少 1 个 test。
   - 6-9 个 case：目标 2 个 test（受同源 session 分组约束时可减少）。
   - 10 个及以上：至少 2 个 test。
3. 分层考虑 `case_type`、`failure_mode`、`root_cause_cluster_id`，尽量让 test 覆盖主要问题类型。
4. 优先把 bad、高严重度、高优化价值 case 放入 test，因为它们更能验证优化是否解决问题。
5. 如果 test 容量允许，放入一个 good regression case，防止优化破坏已成功能力。
6. 同一 `session_id` 的 case 尽量保持在同一个 split，减少训练集和测试集互相泄漏。
7. 所有选择使用稳定 hash 排序，保证同样输入重复运行结果一致。

每个模板 manifest item 会记录：

- `split`
- `case_split`
- `split_reason`
- `split_group_key`
- `split_stratum_key`

最终回传 ClawWeb 的 `benchCases.items` 使用 plan 计算出的 `train|test`。

## 8. Spec 与 Objective 生成

Spec 与 Objective 的机器契约由 `spec_builder.build_spec()` 生成。Fresh Plan 的 Markdown 直接由
`render_goal_markdown()` 和 `render_markdown()` 根据机器契约确定性生成，再由 Python 校验结构、
主指标和 Diagnose/Goal 边界。最终文档阶段不调用模型；模型只负责上游 Diagnose、Direct Goal 和
Discovery 中不确定的业务语义。

输入信息：

- Plan Source、Diagnose handoff，或 Direct Goal 归一化输入中的 cases、目标和约束。
- agent discovery notes，以及区分角色后的 update targets、creation scopes、references、planned deliverables。
- 用户传入的 `--goal`。
- ClawWeb bench domain 上传结果。

目标语义与产品视图采用同一 canonical contract：

1. 显式 `--goal` 是当前优化目标的最高优先级；Diagnose 的自然语言请求描述的是证据如何被采集，不能进入 Objective Summary 成为并列目标。
2. `--goal` 中明确出现的成功率/完成率百分比会被确定性解析为 `primary_metric`，保留指标对象（例如 MCP 调用成功率）、比较符和目标值。
3. objective、spec、ClawWeb final step report 都消费同一个 `primary_metric`；只有用户没有提供明确指标时才使用默认任务成功率。
4. Renderer 输出需通过章节结构、主指标和 Diagnose 意图隔离校验；完整用户输入允许等义表达，不要求 Markdown 逐字复制 `intent_text`。
5. `allowed_update_targets` 只包含 discovery 实际确认的具体路径；抽象的 retry、prompt、参数校验等建议单独存入 `optimization_topics`。
6. ClawWeb 上传状态决定文案：两个 Domain 均发布并校验通过时引用 train/test ClawWeb domains，否则明确引用本地评测集。
7. 附录对 artifacts 和 agent context 使用白名单摘要，禁止展开完整 agents/session_dirs 列表。
8. `PlanProductService` 补充 `planning_basis`、`plan_summary` 和 `goal_fidelity`：说明规划依据与只读边界，并确定性校验 template 数量、成功率等用户硬约束。

输出文件：

| 文件 | 说明 |
| --- | --- |
| `plan/output/objective.md` | 中文优化目标说明，给后续 patch loop 阅读。 |
| `plan/output/objective.json` | 机器可读 objective。 |
| `plan/output/spec-v0.md` | 中文正文 spec，遵循共享模板，突出问题、必要性、优化策略、范围、验收标准，并附带项目消费点和发现追溯。 |
| `plan/output/spec-v0.json` | 机器可读 spec。 |

`spec-v0.md` 的正文要求中文，内容应精简、突出重点：用户要实现什么、规划依据是什么、为什么必须优化、后续怎么做、哪些现存目标可更新、哪些交付物待创建、如何验收。Direct Goal 必须明确 prospective 性质，不能使用“历史 session 已证明”等措辞。

## 9. ClawWeb Bench 双 Domain 上传

当前主流程默认启用 Bench 上传：train 使用仅含 `opt/` 的 ZIP，test 使用仅含 `val/` 的 ZIP；两者分别创建 Domain、扫描上传、批量发布并查询 published 模板全集。只有两个 Domain 均通过模板名、数量和 ZIP digest 一致性验证后，整体上传状态才是 `published`。base URL 使用任务创建时冻结并通过
`--clawweb-url` 传入的 Origin：

```text
{clawweb_url}
```

上传流程：

1. 根据 bot id、task id、split 和对应 ZIP digest 生成稳定且互不相同的 Domain ID，支持安全幂等重跑。
2. 在任何网络调用前同时预检 train/test：模板集合非空且互斥、ZIP digest 正确、文件名与 manifest 一致、路径分别严格位于 `opt/` 和 `val/`。Manifest 和 ZIP 均按不可信缓存处理：拒绝绝对路径、路径逃逸、额外嵌套、重复 entry、split 混用、加密 entry、符号链接和不可完整读取的成员。
3. `POST /api/bench/domains` 分别创建 train/test Domain。
4. 如果创建返回 409，GET 已有 Domain，保持幂等性。
5. `POST /api/bench/domains/{ownerUserId}/{domainId}/uploads/scan` 分别上传 split ZIP，multipart 字段名为 `files`。
6. 校验 scan 模板全集与该 split 的 manifest 完全一致。
7. `POST /templates/batch-publish` 批量发布。
8. `GET /templates?status=published` 验证已发布模板全集与预期完全一致，不允许缺失或额外模板。
9. 写 `plan/output/clawweb_upload_result.json`；只有两个 split 均发布并验证成功时才允许最终成功上报。

为避免重复上传，plan 在非 `--overwrite` 下会优先读取已有 `clawweb_upload_result.json`。只有 train/test 两个 Domain 都已发布、验证成功，且各自 ZIP digest 与模板名集合仍和当前 manifest 一致时才复用；否则补传或失败关闭。

## 10. ClawWeb Step Report 格式

Plan 不发送 running 过程上报。只有本地文档、train/test 模板包和两个 Bench Domain 都处理完成后，才发送一次 final step report。命令级 `FinalStepReportGate` 保证 reporter 即使已成功、返回异常结果或直接抛异常，后续本地持久化失败也不会触发第二个、互相矛盾的终态上报；首次上报结果会保留在 CLI JSON 和本地结果中。在线模式下，如果双 Domain 发布或 final report 传输失败，Plan 返回失败且不得进入 patch loop。

成功时回传：

```json
{
  "status": "succeeded",
  "summary": "完成优化目标与 Bench 规划",
  "output": {
    "goal": {
      "summary": "...",
      "metrics": [
        {
          "key": "task_completion_rate",
          "name": "任务完成率",
          "operator": ">=",
          "target": 0.9,
          "unit": "ratio"
        }
      ]
    },
    "spec": {
      "version": "v0",
      "content_type": "text",
      "content": "spec-v0.md 的完整 Markdown 文本"
    },
    "benchCases": {
      "trainCount": 4,
      "testCount": 1,
      "items": [
        {
          "sourceCaseId": "diagnose case id",
          "taskId": "ClawBench task id",
          "split": "train|test"
        }
      ]
    },
    "benchDomains": {
      "trainBenchDomainId": "...",
      "testBenchDomainId": "...",
      "trainBenchDomainFullURL": "{clawweb_url}/bench/domains/{owner}/{domain}",
      "testBenchDomainFullURL": "{clawweb_url}/bench/domains/{owner}/{domain}"
    }
  }
}
```

train 和 test 必须使用两个独立 Bench Domain；最终回传的 ID 和 full URL 必须分别来自对应 split 的已发布、已验证 Domain。

失败时回传：

```json
{
  "status": "failed",
  "summary": "Plan运行失败",
  "error": "..."
}
```

Step report 上传使用不继承代理的 opener，最多 5 次 attempt。除 408/409/425/429 外的 4xx 不重试；404 会被分类为 `task_or_step_not_found`，通常意味着 task-id/step-id 不存在、不匹配，或上报环境与创建任务的环境不一致。

## 11. 输出文件

所有 plan 生成物写入 `plan/output/`：

| 文件/目录 | 是否核心 | 作用 |
| --- | --- | --- |
| `clawevolve-plan.log` | 是 | 全流程日志。 |
| `input_manifest.json` | 是 | 记录本次使用了哪些归档输入。 |
| `objective.md` | 是 | 给 agent/开发者阅读的优化目标。 |
| `objective.json` | 是 | 机器可读 objective。 |
| `spec-v0.md` | 是 | 后续 patch loop 的核心优化 spec。 |
| `spec-v0.json` | 是 | 机器可读 spec。 |
| `templates/` | 是 | ClawBench task markdown 本地展开目录。 |
| `clawbench_dataset.zip` | 是 | 包含 train/test 的聚合评测集 ZIP，供本地兼容消费者使用。 |
| `clawbench_train_dataset.zip` | 是 | 仅包含 `opt/` train 模板，上传到 train Domain。 |
| `clawbench_test_dataset.zip` | 是 | 仅包含 `val/` test 模板，上传到 test Domain。 |
| `clawbench_manifest.json` | 是 | 本地模板 manifest，含 split 和 trace 信息。 |
| `clawweb_upload_result.json` | 是 | bench domain/template 上传结果。 |
| `clawweb_step_report_result.json` | 是 | 唯一一次 final step report 的返回摘要。 |
| `oss_upload_result.json` | 暂时保留 | OSS pack/publish 当前跳过，记录 skipped。 |

当前不删除这些文件，因为它们分别服务于 patch loop、ClawWeb 对接、调试、审计和重跑幂等。

## 12. 可追溯性

从 plan 输出追溯到原始 session 的路径：

1. `plan/output/clawbench_manifest.json` 中每个 template item 记录 `case_id/source_case_id`、split、failure mode 等。
2. `plan/input/source.json` 的 `cases[]` 中记录 `session_id`、context 和 evidence artifact paths，可定位 `case_dir`、原始 session、judge result 与 analysis。
3. `diagnose/output/diagnose_cases/{case_id}/original_session*.jsonl` 保存原始 session 文件副本。

因此可以从 ClawWeb 回传的 `benchCases.items[*].sourceCaseId` 反查到冻结 Plan Source，再定位原始 session。

## 13. 日志与容错

日志文件：`plan/output/clawevolve-plan.log`。

关键日志点：

- CLI 参数、run/input/output 目录、overwrite 状态。
- ClawWeb step report URL、payload preview、HTTP 状态、错误分类、响应 preview。
- Plan Source 查找、校验与归档。
- discovery notes 读取、preview、preflight 结果。
- template 渲染数量、zip 路径。
- train/test split 的 case 归一化、group、选择原因、最终统计。
- bench domain 创建、上传、发布、验证每一步的 URL、状态和响应 preview。
- spec/objective 写出路径。
- OSS publish skip 记录。
- 最终结果摘要。

容错原则：

- 本地产物能生成时，ClawWeb 上报失败不应让产物丢失；结果以 `deferred` 记录。
- Bench upload 失败时仍尽量输出本地 spec/objective 和错误信息，方便开发修复。
- 已有成功上传结果可复用，减少重复创建 domain。
- 非 `--overwrite` 默认幂等返回已有 plan。

## 15. 已知边界与后续优化

- OSS pack/publish 当前在主流程中暂时禁用，后续稳定后再恢复。
- train/test Domain 采用完整 bot/task 身份摘要、split 和 ZIP digest 驱动的确定性 ID；即使可读前缀因 128 字符限制被截断，不同完整身份也不会仅因共同长前缀而碰撞。若未来 ClawWeb 提供服务端幂等键，可进一步改为平台原生幂等。
- Discovery notes 由 agent 生成，质量依赖 agent 是否真实检查相关代码；脚本能校验路径、角色和边界，但不能完全替代代码理解。
- Direct Goal 的 cases 是未来验收设计，不具备历史证据强度；规划文档通过 `planning_basis` 明确标注来源。
- `goal_fidelity` 对数值硬约束做确定性校验，对复杂语义要求采用保守覆盖检查并可能给出人工复核 warning。
- train/test 策略当前是确定性启发式；后续可根据历史评测统计动态调整测试集代表性。


### 双 Domain 原子门禁

- `domains.train` 与 `domains.test` 是上传结果的单一事实来源。
- 两个 split 使用包含 bot、task、split 与 ZIP digest 的不同 Domain ID。
- 扫描识别集合必须与本地 manifest 的预期模板集合完全一致，随后 published 查询必须覆盖全部预期模板。
- 任一 split 失败时最终 step report 标记失败，`benchDomains` 不回传部分 Domain，避免后续链路误用。
- 已有本地产物但旧上传状态为 skipped/failed 时，不重做分析与模板生成，而是补建 split ZIP 并重试双 Domain 发布。
- `--skip-clawweb-report` 是统一的零 ClawWeb 网络开关；只能使用已存在且校验通过的本地 Diagnose/Direct Goal Source，本地缺失时明确失败而不会拉取 Step Input。
