# Hybrid OpenClaw Bot 长时间无回复修复

## 问题

`merchant_hybrid` 中的 OpenClaw Bot 在 BCS 已确认收到消息后，可能长时间
停留在模型调用阶段。已复现的一次请求在无模型进度约 191 秒后才恢复，并在
约 297 秒后才发送最终回复。现有配置既没有明确的 Bot Agent 运行上限，也没有
为 GLM-5.1 的 OpenAI 兼容端点传递关闭思考的协议字段。

## 范围与方案

1. 对 hybrid 的主模型配置显式设置 600 秒的提供商 HTTP/流式空闲上限和 Agent
   运行上限，避免默认的超长运行窗口占住同一会话。
2. 当主模型恰为 `GLM-5.1` 时，使用 z.ai 兼容的 `enable_thinking` 协议路径，
   并固定传递 `reasoning_effort=none`，避免服务端默认进入长思考。
3. 将 Agent 的 `timeoutSeconds` 同步到动态 OpenClaw Bot profile，并使已有 profile
   在该字段变化时自动重建。
4. 记录不含 URL、凭据或消息内容的策略日志。

不修改 BCS 路由、群聊历史、模型凭据和全局 `~/.openclaw` 心跳插件；后者的
`127.0.0.1:56889 ECONNREFUSED` 与本次 Bot 模型调用无关。

## 成功标准

- hybrid 的运行时模型配置包含 provider 与 agent 两处 600 秒超时。
- GLM-5.1 模型项包含 `reasoning: true` 与 `compat.thinkingFormat: "zai"`，并在
  Agent 模型参数中固定 `reasoning_effort: "none"`。
- 动态 profile 复制且比较 `timeoutSeconds`，旧 profile 能被刷新。
- 回归测试先在未实现时失败，随后通过；shell 语法与 diff 检查通过。

## 测试计划

1. 扩展 hybrid shell 测试，断言模型策略会原子写入超时和 GLM-5.1 非思考配置。
2. 扩展动态 profile 测试，断言 `timeoutSeconds` 被复制且缺失时判定为过期。
3. 执行相关 shell 测试、`bash -n` 和 `git diff --check`。
