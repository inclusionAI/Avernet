# Avernet BCS 自定义协作 YAML schema

自定义协作在 BCS 中通过 `state_machine` 实现。使用本参考编写当前 BCS 运行时和 `bcs-cli collaboration validate` 接受的 YAML。

## 目录

- [Top level](#top-level)
- [Participants](#participants)
- [State machine](#state-machine)
- [Bot task node](#bot-task-node)
- [LLM judge node](#llm-judge-node)
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
- Let BCS infer `requires`; omit it from authoring YAML.
- Keep `metadata.description` as a string, `metadata.labels` as a
  string-to-string mapping, and every `extensions` field as a mapping.

`bcs-cli collaboration validate` 通过当前 BCS 实例的 `POST /collaboration/definitions/validate` 接口执行三层校验；`bcs collaborate run` 的一次性运行接口在启动前执行同一套校验：

1. Authoring shape: enforce the 256 KiB request limit, parse one YAML document, reject duplicate or unknown keys, and enforce the group-creation top-level boundary.
2. Runtime contract: deserialize the definition, reject fields not implemented by the current runtime, and enforce graph, participant and node invariants.
3. Deployment capability: accept `judge` only when the current BCS instance has an LLM provider configured.

校验成功时返回 participant slots 和 graph summary，供后续 `bcs-cli collaboration create` 或 `bcs collaborate run` 绑定逻辑角色。

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
- Bind logical roles to real Bots through `collaboration create --binding` or
  `collaborate run --binding`; never persist those UUIDs in authoring YAML.

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
- Do not use `initial_node`, `input_schema`, `variables`, `events`, actions,
  output contracts, runtime actors, or guards; the current runtime rejects them.
- A node may use an LLM `judge` only when the current BCS instance has an LLM
  provider configured. Declare every judge outcome and give each outcome a
  transition.
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
- `node_timeout_ms: 0` disables the timeout. `max_attempts` values below 1 are
  normalized to 1 by the current runtime; prefer explicit positive values for
  readable authoring YAML.

## LLM judge node

```yaml
nodes:
  review:
    kind: bot_task
    display_name: 审核内容
    assignee:
      type: bot_binding
      binding: reviewer
    instruction: 审核上游产物。
    judge:
      type: llm
      criteria:
        - 内容是否完整且事实准确
      outcomes:
        - approved
        - revise
    transitions:
      approved:
        targets: [publish]
      revise:
        targets: [rewrite]
```

`judge.type` 只能为 `llm`，`criteria` 和 `outcomes` 不能为空。未配置 LLM provider 的 BCS 实例会返回 `UNAVAILABLE_FEATURE`。

## Parallel fan-out and join

Point one `complete.targets` list at multiple nodes to fan out. Point each parallel branch at the same downstream node to join. BCS waits for all upstream branches before running the join node.

## Runtime input and artifacts

BCS includes the original run `[Input]` in every node prompt and includes each direct parent's artifact under `[Upstream Outputs]`. Do not copy a shared parameter object through every node.

- Let the entry node emit a concise task brief when downstream roles need normalized goals and constraints.
- Let parallel nodes emit only their role-specific artifacts.
- Let a join node synthesize its direct upstream artifacts instead of reproducing the complete run input.
- Let the single final node emit the user-ready deliverable.
- Put scenario-specific defaults, formats and business rules in the caller's profile or runtime input, not in this shared Skill.

For a one-shot run in the current session, pass runtime input and every required
role binding together:

```bash
bcs collaborate run workflow.yaml \
  --session "$session_id" \
  --binding "planner=$planner_bot_uuid" \
  --binding "writer=$writer_bot_uuid" \
  --input '{"question":"..."}'
```

Call `bcs collaborate permission --session "$session_id"` before writing or
submitting the YAML. Permission is a server-owned policy and must not be
inferred from the Bot's apparent group role. The role bindings above are
transient for that run and do not modify the Group's persisted runtime binding.

## Validation errors

- `YAML_PARSE` or `DUPLICATE_KEY`: repair YAML syntax or duplicate mapping keys.
- `FORBIDDEN_AUTHORING_FIELD`: remove top-level `api_version`, `id`, or `version`; BCS owns those values during group creation.
- `UNKNOWN_KEY`: remove a misspelled or unsupported field.
- `INVALID_DEFINITION`: fix unsupported runtime fields, participant bindings, transition targets, graph reachability, entry/final counts, cycles, or invalid node settings according to the message.
- `UNAVAILABLE_FEATURE`: the definition uses `judge`, but the current BCS instance has no LLM provider configured.

Treat `bcs-cli collaboration validate` output as authoritative for the current BCS instance. Do not bypass an error because the YAML looks plausible. The command exits non-zero and returns structured `errors` when validation fails.
