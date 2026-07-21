# Avernet BCS 自定义协作 YAML schema

自定义协作在 BCS 中通过 `state_machine` 实现。使用本参考编写当前 BCS MVP runtime 和随附校验器接受的 YAML。

## 目录

- [Top level](#top-level)
- [Participants](#participants)
- [State machine](#state-machine)
- [Bot task node](#bot-task-node)
- [Parallel fan-out and join](#parallel-fan-out-and-join)
- [Runtime input and artifacts](#runtime-input-and-artifacts)
- [Validation errors](#validation-errors)

## Top level

```yaml
name: Human-readable name
metadata:
  description: Human-readable purpose
  labels:
    category: content
  extensions: {}
participants: {}
runtime:
  kind: state_machine
  state_machine: {}
```

- Require a non-empty `name`, non-empty `participants`, and `runtime`.
- Allow only `name`, `metadata`, `participants`, and `runtime` at the top level for this authoring Skill.
- Never emit top-level `api_version`, `id`, or `version`. The create-group path parses authoring YAML after rejecting top-level `id`/`version`; the domain model then supplies the API version and server-owned definition identity defaults.
- Reject any other top-level key, including spelling variants such as `apiVersion`, `verion`, or `verions`.
- Do keep the nested `runtime.state_machine.version: 1`; it is a different field with different semantics.
- Do not put runtime Bot UUIDs in the definition.
- Let BCS infer `requires`; omit it for the current MVP authoring subset.
- Keep `metadata.description` as a string, `metadata.labels` as a
  string-to-string mapping, and every `extensions` field as a mapping.

The bundled validator follows the source validation chain in this order:

1. Enforce the 256 KiB request limit and parse exactly one YAML document with unique keys.
2. Enforce the authoring top-level boundary used by group creation.
3. Validate the `CollaborationDefinition`/state-machine fields accepted by the domain types.
4. Apply the MVP checks from `bcs-collaboration-runtime::definition::validate_definition`.
5. Apply the create-group participant restrictions needed by this logical-binding workflow.

## Participants

```yaml
participants:
  planner:
    display_name: 任务规划
    description: 负责整理目标并汇总最终交付。
    required: true
```

- Keys are logical bindings referenced by node assignees.
- Allowed fields: `display_name`, `description`, `required`, `extensions`.
- Never add `bot_id` or `bcs_participant_role`.
- Bind logical roles to real Bots in the create-group UI.

## State machine

```yaml
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    projection:
      default_visibility: private
    defaults:
      node_timeout_ms: 60000
      max_attempts: 2
    nodes: {}
```

- Require `version: 1` and `graph_mode: acyclic`.
- If projection is present, use only `default_visibility: private` or
  `default_visibility: shared`.
- Use only `bot_task` nodes.
- Do not use `initial_node`, `variables`, `events`, actions, output contracts,
  runtime actors, guards, or judges in the current MVP authoring subset. Judge
  is excluded by this Skill's demo-safe authoring and validation boundary; BCS
  runtime can execute an LLM judge when a judge provider is configured.
- Use one zero-in-degree entry node and one final-output sink.
- Keep every node reachable from the entry and able to reach the final node.

## Bot task node

```yaml
nodes:
  frame_task:
    kind: bot_task
    display_name: 整理任务
    assignee:
      type: bot_binding
      binding: planner
    instruction: |
      整理用户请求，但不要输出最终答案。
    visibility: private
    transitions:
      complete:
        targets:
          - research
```

- Require a non-empty display name and instruction.
- If visibility is present, use only `private` or `shared`.
- Require `assignee.type: bot_binding` and an existing participant binding.
- Ordinary transitions may use only `complete`.
- Every non-final node needs at least one target.
- A final node has `final_output: true` and no transitions.
- Timeouts and attempt counts must be positive integers.

## Parallel fan-out and join

Point one `complete.targets` list at multiple nodes to fan out. Point each parallel branch at the same downstream node to join. BCS waits for all upstream branches before running the join node.

## Runtime input and artifacts

BCS includes the original run `[Input]` in every node prompt and includes each direct parent's artifact under `[Upstream Outputs]`. Do not copy a shared parameter object through every node.

- Let the entry node emit a concise task brief when downstream roles need normalized goals and constraints.
- Let parallel nodes emit only their role-specific artifacts.
- Let a join node synthesize its direct upstream artifacts instead of reproducing the complete run input.
- Let the single final node emit the user-ready deliverable.
- Put scenario-specific defaults, formats and business rules in the caller's profile or runtime input, not in this shared Skill.

## Validation errors

- `YAML_PARSE` or `DUPLICATE_KEY`: repair YAML syntax or duplicate mapping keys.
- `FORBIDDEN_AUTHORING_FIELD`: remove top-level `api_version`, `id`, or `version`; BCS owns those values during group creation.
- `UNKNOWN_KEY`: remove a misspelled or unsupported field.
- `UNSUPPORTED_FEATURE`: replace a non-MVP feature with ordinary bot tasks and complete transitions.
- `MISSING_BINDING`: declare the participant or fix the assignee binding.
- `UNKNOWN_TARGET`: fix the transition target node ID.
- `CYCLE`: remove the back edge; current MVP graphs must be acyclic.
- `UNREACHABLE_NODE`: connect or remove the isolated node.
- `FINAL_OUTPUT_COUNT`: leave exactly one final-output node.

Treat validator output as authoritative for the current MVP subset. Do not bypass an error because the YAML looks plausible.
