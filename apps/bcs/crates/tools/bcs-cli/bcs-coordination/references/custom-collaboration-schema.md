# Avernet BCS 自定义协作 YAML schema

自定义协作在 BCS 中通过 `state_machine` 实现。使用本参考编写当前 BCS 运行时和 `bcs-cli collaboration validate` 接受的 YAML。

## 目录

- [Top level](#top-level)
- [Participants](#participants)
- [State machine](#state-machine)
- [Bot task node](#bot-task-node)
- [Human input node](#human-input-node)
- [LLM judge node](#llm-judge-node)
- [Fixed Loop](#fixed-loop)
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
- Use nested `runtime.state_machine.version: 1` for an ordinary DAG, or `2` for fixed Loops. V2 execution requires the deployment capability described below. This differs from the server-owned top-level version.
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

- Ordinary definitions use `version: 1` and `graph_mode: acyclic`; fixed Loops use `version: 2` and `graph_mode: hierarchical`.
- If projection is present, use only `default_visibility: private` or
  `default_visibility: shared`.
- Executable nodes use `bot_task` or `human_input`. V2 also allows outer `loop` control nodes, whose bodies contain executable nodes.
- Do not use `initial_node`, `input_schema`, `variables`, `events`, actions,
  output contracts, or guards; the current runtime rejects them. A `bot_task`
  must not use a runtime actor. Runtime actors are reserved for the separately
  configured IM-targeted HumanInput mode described below.
- A node may use an LLM `judge` only when the current BCS instance has an LLM
  provider configured. Ordinary nodes route every Judge outcome through a
  transition. A Loop result instead partitions outcomes into continue/break lists.
- Use one zero-in-degree entry node and one final-output sink.
- Keep every node reachable from the entry and able to reach the final node.

## Fixed Loop

`collaboration validate` also accepts `version: 2 + graph_mode: hierarchical` for fixed Loop compilation and preview.
It returns `VALIDATION_ONLY_FEATURE` when this deployment has v2 execution disabled (the default). For testing, set `collaboration.loop_execution_enabled = true` and restart BCS: validation then omits this warning and create/run/rerun are enabled together with Loop recovery. Ordinary workflow recovery starts with the service without a separate switch. The local config template opts in; full failover and production release gates remain pending.
Do not use a successful preview as evidence that a deployment can execute Loop definitions.

```yaml
name: Repeated discussion
participants:
  writer:
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 2
    graph_mode: hierarchical
    nodes:
      rounds:
        kind: loop
        display_name: Discussion Loop
        loop:
          mode: fixed
          max_iterations: 3
          entry_node: discussion
          result_node: discussion
          continue_outcomes: [complete]
          break_outcomes: []
          exhausted_outcome: exhausted
          nodes:
            discussion:
              kind: bot_task
              display_name: Discuss
              assignee: {type: bot_binding, binding: writer}
              instruction: Use the previous iteration result.
        transitions:
          exhausted: {targets: [publish]}
      publish:
        kind: bot_task
        display_name: Publish
        assignee: {type: bot_binding, binding: writer}
        instruction: Publish the final result.
        final_output: true
```

- `loop` requires all eight fields shown above; unknown fields are rejected. Body nodes use the ordinary executable-node rules.
- `loop.continue_display_name` is optional and labels the logical return edge and expanded continue edges. Exhausted labels belong to `transitions.<exhausted_outcome>.display_name`, not a separate Loop field.
- `continue_outcomes` must be nonempty; `break_outcomes` must be present but may be `[]`. Without a result Judge, use exactly `[complete]` and `[]`.
- With a result Judge, continue and break must partition all its outcomes. `exhausted_outcome` is separate and cannot be returned by the Judge.
- Each break/exhausted transition needs at least one existing outer target. Empty break means run to the limit and then route to exhausted targets, not stop the whole workflow.
- Body has exactly one entry and one result terminal. Result defines no body transitions; body nodes cannot be final_output. Keep the unique final_output outside the Loop.
- Loop control nodes allow only `kind`, `display_name`, `loop`, `transitions`, `extensions`; no assignee, instruction, Judge, timeout or final_output fields, including null/false placeholders.
- Nested loops, cross-body targets, arbitrary goto and unbounded loops are unsupported. The `ln-` node ID prefix is reserved in v2; authoring node IDs are at most 128 ASCII bytes.
- Multiple sibling Loops are supported in the same outer acyclic graph. An outer transition can target another Loop ID; BCS enters that Loop's entry node. Never target its body node directly. Each Loop has its own iteration count and previous result; the next Loop starts at iteration 1 with no previous result of its own, while the prior Loop's selected output is an ordinary upstream artifact.
- Server-owned limits come from `collaboration.fixed_loop_limits`. Rejection reports the authoring path before any Run creation.
- Preview retains authoring `graph_mode: hierarchical`, adds `execution_graph_mode: acyclic`, per-node `execution` and result-edge `loop_route`. Last-round edge outcome stays `complete`/the actual continue outcome while route kind/logical_outcome shows exhaustion.

The internal compiler version is `bcs.fixed-loop.compiler/v1`. Generated IDs use a namespace-separated SHA-256 of the JSON
tuple `(definition_id, definition_version, loop_id, iteration, body_node_id)`, truncated to 128 bits and prefixed with `ln-`.
Never parse or construct these IDs in clients. Preview IDs do not promise future Run identity.

The example above intentionally has no Judge and always runs to its limit. For review-driven work, give the result a Judge with `outcomes: [approved, revise]`, set `continue_outcomes: [revise]` and `break_outcomes: [approved]`, then define outer `approved` and `exhausted` transitions. These business outcome names are chosen by the author, not reserved control keywords.

| Field or value | Meaning |
| --- | --- |
| `approved`, `revise` | Actual outcomes selected by the result node's Judge |
| `continue_outcomes`, `break_outcomes` | Classify actual outcomes into Loop routing behavior |
| `continue`, `break`, `exhausted` | BCS route kinds in execution metadata, not target node IDs |
| `exhausted_outcome` | Outer exit key produced when the last result still requests continuation; the result's actual outcome remains `revise` |
| `transitions.approved` | Route selected for the `approved` outcome; a Loop only declares break/exhausted exits here |
| `targets: [writing_loop]` | Existing outer node IDs to activate; may include a sibling Loop |
| `entry_node`, `result_node` | Body node IDs scoped to this particular Loop |
| `assignee.binding` | Participant role to bind to a Bot; it is not a routing target |
| `transitions.<outcome>.display_name` | Optional display-only label, shared by every target of the transition |
| `loop.continue_display_name` | Optional display-only label shared by all continuation outcomes of this Loop |

For two review stages, place `research_loop` and `writing_loop` beside each other under outer `nodes`. Route `research_loop.approved` to `writing_loop`, route each exhausted outcome to an explicit handler, and converge the chosen branch on one final-output node. Both bodies may name their result `review`; their execution IDs and contexts remain separate. The Workbench seed template `research-writing-loops` demonstrates this with researcher, writer, reviewer and polisher roles.

Display names are supported on ordinary transitions, body transitions, and Loop exits. Omit them to keep the existing outcome labels (and `continue` for the return edge). When present, use nonblank strings, never null or a locale object. They do not rename Judge outcomes or change routing; the final continuation still saves its actual outcome and uses the exhausted exit's display name. Preview and the Run panel show these names, while tooltips retain the actual outcome. Historical Runs and reruns use their saved names. Deployments must support these authoring fields before using the updated templates.

```yaml
# Fragment inside a Loop node; other required Loop fields are omitted here.
loop:
  continue_outcomes: [revise]
  continue_display_name: 根据意见修订
  break_outcomes: [approved]
  exhausted_outcome: exhausted
transitions:
  approved:
    display_name: 评审通过
    targets: [polish]
  exhausted:
    display_name: 转入重写
    targets: [rewrite]
```

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

## Human input node

For a Human who is already Present in the current Workbench session, use a
frontend HumanInput node:

```yaml
nodes:
  owner_acceptance:
    kind: human_input
    display_name: 店主验收
    instruction: |
      请针对当前公开版本明确回复“接受”，或给出需要修改的条款。
    node_timeout_ms: 600000
    visibility: shared
    transitions:
      complete:
        targets:
          - publish
```

- A frontend `human_input` node has no `assignee` and no `notification`.
  Any Present Human participant in the run session may respond from the
  Workbench.
- A Human is not a logical Bot participant. Do not add an `owner` participant
  slot for this node, do not pass `--binding owner=human_001`, and never bind a
  `human_*` actor ID to a `bot_task`.
- Require an explicit positive `node_timeout_ms` on the node. The state-machine
  default timeout is not inherited by HumanInput.
- Do not set `max_attempts` or `final_output` on HumanInput. Put the single
  final-output `bot_task` after the HumanInput node when the Human response
  must be incorporated into the deliverable.
- Without a judge, use only the `complete` transition. A HumanInput node may
  use the same LLM judge contract described below when branching on natural
  language is required.
- Starting a definition with HumanInput requires at least one Present Human
  session participant. This is checked by BCS independently of Bot role
  bindings.

IM-targeted HumanInput uses `assignee.type: runtime_actor` plus `notification`
and `human_input_channel`. It is a different delivery mode. Do not add those
fields merely to target the Human already using the current Workbench session.

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
Bot role binding together:

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
HumanInput has no `--binding` in this frontend flow.

## Validation errors

- `YAML_PARSE` or `DUPLICATE_KEY`: repair YAML syntax or duplicate mapping keys.
- `FORBIDDEN_AUTHORING_FIELD`: remove top-level `api_version`, `id`, or `version`; BCS owns those values during group creation.
- `UNKNOWN_KEY`: remove a misspelled or unsupported field.
- `INVALID_DEFINITION`: fix unsupported runtime fields, participant bindings, transition targets, graph reachability, entry/final counts, cycles, or invalid node settings according to the message.
- `UNAVAILABLE_FEATURE`: the definition uses `judge`, but the current BCS instance has no LLM provider configured.
- A run error requiring a Present Human means the session roster has no Human
  participant in Present mode; do not work around it by binding the Human ID as
  a Bot.

Treat `bcs-cli collaboration validate` output as authoritative for the current BCS instance. Do not bypass an error because the YAML looks plausible. The command exits non-zero and returns structured `errors` when validation fails.


### Loop consumers

Use `collaborate query --run ID` to obtain execution IDs and metadata;
`--graph` includes continue/break/exhausted routes, `--pending` includes trusted
first/later LoopContext, and `--node EXECUTION_ID` includes the retry attempt.
`--no-json` renders those fields as text. `collaborate respond --run ID --node
EXECUTION_ID --content TEXT` only submits to an ID still returned in the
current authenticated Human's pending list. Neither command parses generated
IDs. Read-only historical inspection does not enable v2 execution.
