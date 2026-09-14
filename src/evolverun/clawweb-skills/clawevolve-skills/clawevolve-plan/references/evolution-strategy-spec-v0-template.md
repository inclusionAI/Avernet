# Evolution Strategy Spec v0 Template

这是 `clawevolve-plan`、`clawevolve-tune`、`clawevolve-review` 共享的规范模板。

- `schema_version` 固定为 `evolution.spec.v0`
- `spec_version` 首版为 `v0`
- `spec` 是调优策略，不是 objective 本身
- 长期业务目标和硬约束写在 objective，spec 只负责本轮如何把 objective 做得更好

> 说明：
> - `clawevolve-plan` 负责根据 diagnose 证据生成 `plan/output/spec-v0.md`。
> - `clawevolve-tune` 读取 `input/spec-v{N-1}.md` 做当前轮小步调优。
> - `clawevolve-review` 根据验证结果产出下一版 `spec-vN.md`。
> - `clawevolve-workflow` / runner 会在轮次目录中移动与版本化这些 spec 文件。

```md
---
schema_version: evolution.spec.v0
spec_version: v0
parent_spec_version: null
created_by: clawweb|human|clawevolve-review
objective_ref: objective.md
direction_pool_ref: clawevolve-workflow/references/direction-pool.md
max_active_directions: 3
---

# Evolution Strategy Spec v0

## 1. Objective Contract

### 1.1 Objective Summary

- <只写 objective 已经定义好的业务目标，不重写 objective 全文>
- <简明说明本轮 spec 对 objective 的对齐方式>

### 1.2 Business Hard Constraints

- <objective 里明确要求的业务规则、输出契约、不可违背条件>
- <不得修改 judge/scorer/case/历史产物/密钥/生产配置>

### 1.3 Optimization Bias

- `generalization_first`：默认取向，优先稳健、泛化、少回归。
- `objective_specific`：当 objective 明确是窄业务目标时启用，优先关键路径、业务约束和输出契约；但不得发明 objective 之外的新业务规则，不得硬编码 case 答案。

### 1.4 Success Criteria

- <本轮要观察到什么现象才算成功>
- <尽量写成可观察、可检查的条件>

## 2. Current Strategy Summary

- <这一轮准备怎么做>
- <1-5 条，保留最核心的策略假设>

## 3. Active Optimization Directions

| TC Code | Module | Problem Type | Direction ID | Priority | Why This Round | Expected Effect |
|---|---|---|---|---|---|---|
| ... | ... | ... | ... | high | ... | ... |

## 4. Tuning Scope

### Allowed Change Areas

- <path-or-dir>：<为什么允许改；本轮预计怎么改>

### Disallowed Change Areas

- MCP server/tool implementation itself
- optimization/validation cases
- bench result files
- scoring/grading logic
- objective
- historical round artifacts

## 5. Failure Modes to Address

| ID | TC Code | Related Direction ID | Failure Mode | Evidence | Priority | Suggested Direction |
|---|---|---|---|---|---|---|
| ... | ... | ... | ... | ... | high | ... |

## 6. Spec Evolution Rules

- Keep spec as a strategy document, not a rewritten objective.
- Long-lived business rules belong in objective; spec only inherits and operationalizes them.
- When objective and evidence justify `objective_specific`, prioritize objective-critical paths and contracts over broad generality.
- When objective does not justify `objective_specific`, default to `generalization_first` and avoid overfitting.
- Next versions should make directions more fine-grained when validation evidence supports it.
- Select at most `max_active_directions` active directions for the following round.
- Prefer activating 1-3 directions in MVP; only use 4-5 when failures clearly require it.
- Reuse direction IDs from `direction_pool_ref` when possible.
- Add new direction IDs only when existing directions cannot express the needed optimization, and document the reason in `spec_update_report`.
- Preserve useful directions in the external direction pool even when not active this round.
- Do not encode validation-set-specific answers.
- Do not weaken objective alignment just to improve scores.
- Acceptance/rejection and rollback are orchestrator responsibilities, not spec responsibilities.

## 7. History

- Initial version.

---

## Appendix A: Project Consumption Map

| Consumer | Reads | Uses It For |
|---|---|---|
| clawevolve-plan | `plan-source/v2` + discovery notes + inspected targets + upload result | Generate `objective.md/json`, `spec-v0.md/json`, and the ClawWeb step-report payload for the next round. |
| clawevolve-tune | `objective.md` + `spec-v{N-1}.md` + `round_state.json` / `bench.optimization` | Choose a small, explainable patch plan and write `tune_report.md`, `changed_files.txt`, `diff.patch`, `change_manifest.json`. |
| clawevolve-review | `objective.md` + `spec-v{N-1}.md` + `round_state.json` / `bench.validation` + `tune_report.md` | Decide keep/strengthen/split/retire/reject and emit `spec-vN.md` + `spec_update_report.md`. |
| clawevolve-workflow / runner | `plan/output/spec-v0.md`, `input/spec-v{N-1}.md`, `spec/spec-vN.md` | Copy, version, and pass the spec between rounds and prompt stages. |

## Appendix B: Diagnose Evidence and Agentic Discovery

- `bot_id`: <bot id>
- `clawweb_domain`: <base_url / domain_id / domain_url / status / zip_path>
- `artifacts`: <input artifacts summary>
- `root_cause_clusters[]`: <diagnose 聚类>
- `cases[]`: <selected cases>
- `discovery_notes.md`: <agent 检查记录>
- `allowed_update_targets[]`: <实际检查过的候选修改目标>
- `forbidden_changes[]`: <禁止边界>
- `required_behavior[]`: <必须满足的行为>
- `acceptance_criteria[]`: <验收标准>
- `patch_generator_instruction`: <patch 生成指令>

> 这部分可作为 plan 的追溯附录；review 轮次可以保留最关键的证据摘要，避免复制长日志。
```
