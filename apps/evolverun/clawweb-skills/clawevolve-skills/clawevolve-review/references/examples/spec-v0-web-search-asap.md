---
schema_version: evolution.spec.v1
spec_version: v0
parent_spec_version: null
created_by: clawweb
objective_ref: objective.md
---

# Evolution Strategy Spec v0

## Objective Contract

- Preserve the objective and its hard constraints.

## Accepted Baseline Snapshot

- baseline_id: `bootstrap`
- round_id: `0`
- evaluation_identity_status: `pending_bootstrap_registration`

## Current Experiment Questions

### HYP-001: retrieval_failure
- Status: `suspected`
- Claim: Retrieval may fail because routing, query construction, or tool latency is unstable.
- Alternative causes: routing_boundary, query_quality, tool_latency
- Disambiguation signal: retrieval completion behavior changes in post-Tune targeted optimization.
- Expected evidence: runner-computed behavior metrics.
- Revisit condition: independent repeated evidence.

## Protected Behaviors

- `PB-001`: Preserve evidence citation and output contract. (metric=score, min_value=null, max_drop=0.10)

## Search Contract

```json
{"required_operator_diversity":3,"required_operator_family_diversity":3,"exactly_one_selected_mechanism":true,"max_changed_files":1,"max_executed_edits":1,"max_diff_hunks":1,"atomic_single_variable":true}
```

## Scope Contract

```json
{"allowed_change_areas":["target runtime skill surfaces"],"disallowed_change_areas":["benchmark","scorer","history"]}
```

## Evaluation Contract

```json
{"candidate_full_opt_policy":"after_targeted_gate","validation_policy":"after_candidate_opt_gate"}
```

## References

- history_ref: `experiment_ledger.jsonl`
- failure_registry_ref: `failure_registry.json`
- mutation_operator_library_ref: `clawevolve-workflow/references/mutation_operator_library.json`
