# 自定义协作

使用自定义协作把用户定义的参与角色、执行步骤、串并行关系和最终交付物转换为可运行的多 Bot 工作流。

## 术语边界

- 面向用户和 Agent 认知统一使用“自定义协作”。
- `state_machine`、状态机、节点图和运行实例是实现术语，只在 YAML、API、日志和故障排查中使用。
- “结构化协同”是历史称呼，只用于理解旧资料，不主动对用户使用，也不要与 BCS 的结构化路由混淆。

首次向用户解释时可称“自定义协作工作流”；进入实现时说明“BCS 使用 `state_machine` YAML 执行该工作流”。

## 能力边界

- 生成或修改自定义协作 YAML 前，必须读取 [custom-collaboration-schema.md](custom-collaboration-schema.md)。
- YAML 中只声明逻辑 participant binding，不写真实 Bot UUID。创建群时再把逻辑角色绑定到发现结果中的 Bot。
- 不根据 YAML 外观猜测有效性；交付前必须运行随 Skill 提供的校验器。
- 随附校验器覆盖当前自定义协作的 demo-safe 子集：无环 `bot_task` 节点，以及基于 `complete` 的串行或并行流转。该子集不接受 `guard`、`judge` 或 `action`。BCS 运行时可在配置 judge provider 后执行 LLM judge，但本 Skill 不会在 demo-safe 流程中生成或校验这类节点。

## 工作流

1. 明确用户要自定义的角色、步骤、串并行关系、运行时输入和最终交付物。
2. 将具体执行值与可复用流程分开：YAML 固化流程，单次调用输入提供本次参数。
3. 按职责和能力发现候选 Bot，核对所有必需逻辑角色均可绑定。
4. 读取 schema，根据本次角色和流程编写 YAML。
5. 准备好候选 YAML，不要在多个 Bot 或任务之间共用固定临时路径。
6. 在同一 shell 会话中用 `mktemp` 创建唯一文件，写入候选 YAML，然后运行校验：

```bash
candidate_file="$(mktemp "${TMPDIR:-/tmp}/avernet-custom-collaboration.XXXXXX")"
# 将候选 YAML 写入 "$candidate_file"
{baseDir}/scripts/validate-state-machine-yaml "$candidate_file" --demo-safe --json
```

7. 修复全部错误并重试，最多修复两轮。仍失败时报告校验错误，不交付猜测版本。
8. 校验通过后重新读取临时文件，随后执行 `rm -f -- "$candidate_file"`。在用户可见回复中提供完整 `yaml` 代码块、逻辑角色绑定表和校验摘要。校验失败且不再重试时也要删除临时文件。

## 编写约束

- 顶层只允许 `name`、可选 `metadata`、`participants` 和 `runtime`。
- 保留 `runtime.kind: state_machine` 和 `runtime.state_machine.version: 1`。
- 不输出顶层 `api_version`、`id` 或 `version`；这些字段由 BCS 创建群时提供。
- 不把真实 Bot UUID、token、私密地址或运行时 participant role 写进 YAML。
- 用户可见交付必须包含完整 YAML，不能用临时文件路径、工具输出或“见上文”代替。
- 执行节点只输出自己的业务产物；不要要求每个节点重复传递完整参数对象。
