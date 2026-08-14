# 自定义协作

使用自定义协作把用户定义的参与角色、执行步骤、串并行关系和最终交付物转换为可运行的多 Bot 工作流。

## 术语边界

- 面向用户和 Agent 认知统一使用“自定义协作”。
- `state_machine`、状态机、节点图和运行实例是实现术语，只在 YAML、API、日志和故障排查中使用。
- “结构化协同”是历史称呼，只用于理解旧资料，不主动对用户使用，也不要与 BCS 的结构化路由混淆。

首次向用户解释时可称“自定义协作工作流”；进入实现时说明“BCS 使用 `state_machine` YAML 执行该工作流”。

## 能力边界

- 生成或修改自定义协作 YAML 前，必须读取 [custom-collaboration-schema.md](custom-collaboration-schema.md)。
- YAML 中只声明逻辑 participant binding，不写真实 Bot UUID。运行或创建群时，再通过 CLI 参数把逻辑角色绑定到当前 session 或发现结果中的 Bot。
- 不根据 YAML 外观猜测有效性。设计交付或建群前必须通过 `bcs-cli collaboration validate`；当前 session 一次性运行由 `bcs collaborate run` 的服务端接口执行同一套 authoring 和运行时校验。
- 服务端固定拒绝当前运行时尚未实现的 `guard`、`action`、`output_contract`、`variables`、`events` 和 `input_schema`。`judge` 仅在当前 BCS 实例配置了 LLM provider 时可用。
- 当前运行时要求无环图、唯一零入度入口、唯一 `final_output` 出口，且所有节点从入口可达并能到达最终出口。这些限制始终生效，不由 CLI 参数切换。

## 选择执行方式

- 群成员已经在当前 BCS session 中讨论，并决定临时用状态机解决本次问题：执行“当前 session 一次性运行”。
- 用户需要可复用的独立状态机群，或后续 service invocation 要复用同一工作流：执行“新建持久自定义协作群”。
- 两种方式都使用同一 authoring YAML schema。一次性运行的角色绑定只对本次 run 生效，不修改 Group 的持久配置。

## 当前 session 一次性运行

1. 从当前 BCS 群消息上下文、建群响应或 session 查询结果中取得准确的 `session_id`。不得把 Bot 名称、OpenClaw `sessionKey` 或 group ID 当成 BCS session ID。
2. **编写 YAML 前必须先查询权限**：

```bash
bcs collaborate permission --session "$session_id"
```

只以服务端响应为准：

- `allowed: true`：继续编写和提交。
- `allowed: false`：停止，不提交 YAML；把 `reason_code` 和 `message` 告知群成员。

不得因为当前 Bot 看起来是 driver、manager 或群主就跳过查询。当前策略可能改变，CLI 不拥有授权规则。

3. 明确本次运行的逻辑角色、步骤、串并行关系、输入和唯一最终交付物。只从当前 session 的 Bot 成员中选择角色绑定。
4. 读取 [custom-collaboration-schema.md](custom-collaboration-schema.md)，创建唯一临时文件并写入 authoring YAML：

```bash
candidate_file="$(mktemp "${TMPDIR:-/tmp}/avernet-session-collaboration.XXXXXX")"
# 将候选 YAML 写入 "$candidate_file"
```

5. 一次性提交 YAML、全部必需角色绑定和本次运行输入：

```bash
bcs collaborate run "$candidate_file" \
  --session "$session_id" \
  --binding "planner=$initiator_bot_uuid" \
  --binding "researcher=$researcher_bot_uuid" \
  --binding "writer=$writer_bot_uuid" \
  --input '{"question":"本次需要解决的问题"}'
```

`--binding` 格式是 `逻辑角色=Bot UUID`，可重复；真实 Bot UUID 不得写进 YAML。`--input` 接受 JSON 字面量或 `@path/to/input.json`。该命令只发送一次运行请求；BCS 在服务端重新检查权限、严格校验 YAML 和角色绑定，然后以临时绑定启动 run，不修改 Group 的持久定义或绑定。

6. 提交成功后保留返回的 run ID 用于诊断。BCS 会向原 session 发送 `bcsPanel.StateMachineRunView` AixUI 副屏消息；完成后，BCS 以发起 Bot 的身份将唯一最终产物发回原群，并保持 Chat session 为 Running。发起 Bot 不要再次手工转发结果。
7. 无论成功或失败，最后执行 `rm -f -- "$candidate_file"`。

## 新建持久自定义协作群

1. 明确用户要自定义的角色、步骤、串并行关系、运行时输入和最终交付物。
2. 将具体执行值与可复用流程分开：YAML 固化流程，单次调用输入提供本次参数。
3. 按职责和能力发现候选 Bot，核对所有必需逻辑角色均可绑定。
4. 读取 schema，根据本次角色和流程编写 YAML。
5. 为每个逻辑角色选定一个 Bot；driver 必须同时绑定到至少一个逻辑角色。
6. 准备候选 YAML，不要在多个 Bot 或任务之间共用固定临时路径。
7. 在同一 shell 会话中用 `mktemp` 创建唯一文件，写入候选 YAML，然后通过 `bcs-cli` 请求当前 BCS 服务端校验：

```bash
candidate_file="$(mktemp "${TMPDIR:-/tmp}/avernet-custom-collaboration.XXXXXX")"
# 将候选 YAML 写入 "$candidate_file"
bcs collaboration validate "$candidate_file"
```

8. 修复全部错误并重试，最多修复两轮。仍失败时报告校验错误，不交付或创建猜测版本。
9. 如果用户只要求设计工作流，或尚未明确授权建群，校验通过后重新读取临时文件，在用户可见回复中提供完整 `yaml` 代码块、逻辑角色绑定表和校验摘要，并用明确问句请求确认：

```text
自定义协作 YAML 已设计并校验通过，目前尚未创建执行群。
是否现在按以上 YAML 创建自定义协作群？回复“确认创建”后，我将建群并返回群聊入口。
```

不得只写“如需执行可使用上述 YAML 创建群”。用户已经明确要求建群时，不重复确认，直接继续下一步。若用户在后续轮次确认，而原临时文件已清理，则从此前交付给用户的完整 YAML 原样创建新的唯一临时文件，再校验一次后建群。
10. 用户已明确要求或确认新建自定义协作群后，使用同一个已校验文件执行建群。每个 `--binding` 使用 `逻辑角色=Bot UUID`，且覆盖校验结果中所有 `required: true` 或 `assigned: true` 的角色：

```bash
bcs collaboration create "$candidate_file" \
  --driver "$driver_bot_uuid" \
  --binding "planner=$driver_bot_uuid" \
  --binding "researcher=$researcher_bot_uuid" \
  --binding "writer=$writer_bot_uuid" \
  --context "$collaboration_goal" \
  --topic "$group_topic"
```

`collaboration create` 会再次调用服务端校验接口，再检查逻辑角色、必填角色和 driver 绑定，最后通过 `/groups` 创建 `state_machine` 群。需要让后续 service invocation 自动启动同一工作流时，再加 `--auto-start-on-service-invocation`。

11. 建群成功后向用户返回 group ID、driver、participants 和可点击的 `chat_url`（若服务端提供），并保留响应中的 `session_id` 供后续 BCS Session 操作使用。`session_id` 是 BCS 会话标识，不等同于 OpenClaw `sessions_send` 所需的完整 `sessionKey`；使用 `sessions_send` 前先从会话列表解析对应 `sessionKey`，无法解析时改用 `bcs session chat --session "$session_id" --message "..."`。无论校验失败、建群失败还是成功，最后都执行 `rm -f -- "$candidate_file"`。

## 编写约束

- 顶层只允许 `name`、可选 `metadata`、`participants` 和 `runtime`。
- 保留 `runtime.kind: state_machine` 和 `runtime.state_machine.version: 1`。
- 不输出顶层 `api_version`、`id` 或 `version`；这些字段由 BCS 创建群时提供。
- 不把真实 Bot UUID、token、私密地址或运行时 participant role 写进 YAML。
- 真实 Bot UUID 只放在 `collaborate run --binding` 或 `collaboration create --binding` 参数中，不写入 YAML。
- 用户可见交付必须包含完整 YAML，不能用临时文件路径、工具输出或“见上文”代替。
- 执行节点只输出自己的业务产物；不要要求每个节点重复传递完整参数对象。
