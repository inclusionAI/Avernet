# evolution.spec.v1

> 状态：设计草案，当前未启用。Plan 运行时继续生成并校验
> `evolution.spec.v0`；本文件不参与运行时 Builder、Renderer、Prompt 或契约校验。

首轮文件名保持 `spec-v0.md` / `spec-v0.json`，但 schema 必须为 `evolution.spec.v1`，`spec_version=v0`。JSON 为权威来源，Markdown 确定性渲染。

必需字段：

- `objective_contract`
- `accepted_baseline_snapshot`
- `experiment_questions`
- `protected_behaviors`
- `search_contract`
- `scope_contract`
- `evaluation_contract`
- `history_ref`
- `failure_registry_ref`
- `mutation_operator_library_ref`

Spec 只描述需要区分的问题、保护行为和搜索/评测边界。禁止 operator、target_file、exact_change、patch_generator_instruction、Suggested Direction、validation case/rubric 信息。


Protected behavior entries use `{behavior_id, description, metric, direction: maintain, min_value, max_drop, metric_resolution_status}`. Unknown legacy strings must remain unresolved rather than defaulting to score. Tune payload serialization is budgeted to 8192 UTF-8 bytes after deterministic field-level compression.

Binding and gating constraints (enforced by the candidate opt gate):

- Every Spec protected behavior, resolved OR unresolved, must be bound by `behavior_id` in the Tune manifest `protected_signals` before candidate bench; a behavior with no matching binding makes the candidate opt signal plan invalid.
- A binding must reuse the same `metric` as the Spec protected behavior's resolved metric; its `min_value` must not be lower than the Spec value; its `max_drop` must not be wider (greater) than the Spec value; and its `task_id` must belong to the optimization task set. A binding that relaxes the Spec is rejected.
- The protected gate checks `max_drop` and `min_value` simultaneously: `drop <= max_drop` AND `candidate >= min_value` (when `min_value` is present). Passing requires both; a candidate that violates either fails the protected gate and is reported with `minimum_passed` / `drop_passed`.
- Numeric signal fields (`min_value`, `max_drop`, `baseline`, `min_delta`, `expected_min`, `expected_value`) must be finite numbers; strings, booleans, NaN, Inf, or missing required values are invalid and fail-closed rather than raising.
- `boolean_flip` expected-fix signals require an `expected_value` (default `1`); only the flip-toward-`expected_value` direction counts as a fix (`baseline != expected_value` and `candidate == expected_value`), so any regression away from `expected_value` is rejected.
- Canonical Spec JSON keeps only the documented top-level fields; unknown top-level fields are dropped on canonicalize and on load, and the JSON itself is budgeted to the same 8192-byte limit, not only the Tune payload.

- Expected signals support `role=required|supporting` (default required). Required signals gate candidate effect; supporting signals are diagnostic only.
- Protected behaviors must be atomic: one behavior_id, one semantic behavior, one observable metric and one threshold/drop contract.
