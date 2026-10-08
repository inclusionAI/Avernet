# strategy spec-v0 schema

`clawevolve-plan` consumes one canonical `plan-source/v2` document from Diagnose, Insight Improvement, or Direct Goal and emits four files:

- `objective.md`
- `objective.json`
- `spec-v0.md`
- `spec-v0.json`

`spec-v0.md` now follows the shared template at `evolution-strategy-spec-v0-template.md`.

## 目标定位

SPEC 是 **strategy document**，不是 objective 本身。它负责选出少量本轮 active optimization directions，并把证据、发现和安全边界写清楚，供后续 patch / tune / review 使用。

## 输出语言

- 人类可读正文尽量使用中文
- 保留 schema key、enum、direction ID、路径、命令、工具名、指标名和证据原文
- 共享模板固定章节名保持英文；如需补充说明，可在段落里用中文

## 必需输入

- `bot_id`
- `clawweb_domain`: `base_url`, `domain_id`, `domain_url`, `status`, `zip_path`
- `artifacts`
- `default_optimization_goal`
- `case_preference`
- `case_distribution`（含 `by_original_model`）
- `agent_context`
- `root_cause_clusters[]`
- `cases[]`
- `discovery_notes.md`
- inspected update targets from `--target`

## 必需 frontmatter

```yaml
schema_version: evolution.spec.v0
spec_version: v0
parent_spec_version: null
created_by: clawweb
objective_ref: objective.md
direction_pool_ref: clawevolve-workflow/references/direction-pool.md
max_active_directions: 3
```

## 必需模板章节

1. `Objective Contract`
2. `Current Strategy Summary`
3. `Active Optimization Directions`
4. `Tuning Scope`
5. `Failure Modes to Address`
6. `Spec Evolution Rules`
7. `History`

## 推荐附录

共享模板建议额外保留：

- `Appendix A: Project Consumption Map`
- `Appendix B: Diagnose Evidence and Agentic Discovery`

## 兼容字段

`spec-v0.json` 可保留以下 traceability 字段，供后续 round 和脚本消费：

- `spec_id`
- `bot_id`
- `goal`
- `deliverables`
- `evidence.original_model_distribution`
- `problem_statement`
- `root_cause_clusters`
- `optimizable_contents`
- `preliminary_optimization_plan`
- `agentic_discovery`
- `allowed_update_targets`：已存在且允许后续修改的目标，不包含 creation scope
- `allowed_creation_scopes`：已存在、已检查且允许后续创建交付物的窄目录
- `reference_files`：只读参考文件，不得与 update target 重叠
- `planned_deliverables`：当前不存在、由后续 patch/evolve 创建的路径及其 `creation_scope`
- `forbidden_changes`
- `required_behavior`
- `acceptance_criteria`
- `patch_generator_instruction`
- `consumption_map`

## 生成约束

- discovery notes 必须非空
- 至少一个 inspected operation target/scope
- 普通 update target 必须已经存在；绿色创建场景必须使用现有目录 `creation_scope`，未来文件写入 `planned_deliverables`
- `reference_files` 必须存在且只读，不得与 `allowed_update_targets` 重叠
- Plan 本身只生成规划与评测产物，不创建或修改目标 workspace 文件
- 若缺少上述任一项，应报错而不是生成正式 spec
- 不得修改 judge/scorer/case/历史产物/密钥/生产配置
