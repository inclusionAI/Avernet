# clawevolve-diagnose 问题记录

## 2026-08-13：LLM 上游约 90 秒断开连接

### 现象

调用模型 API 时出现 `RemoteDisconnected: Remote end closed connection without response`。客户端已发送请求，但在收到 HTTP 响应状态行前，被上游主动关闭连接。

### 日志证据

- `llm http request sent` 正常出现，请求上传耗时约 0.1 秒。
- 未出现 `llm http response received`，因此没有 HTTP status、响应头或错误 body。
- 已观测的两次断连均发生在请求发送后约 91 秒。
- 重试请求可以收到 HTTP 200，成功响应耗时约 50--107 秒。

### 排查结论

请求体已成功上传，异常发生在客户端等待响应头阶段。当前证据不支持本地 JSON 解析错误或本地 600 秒 judge timeout；更可能是上游网关或模型后端在等待首个响应期间关闭连接，疑似存在约 90 秒的首包或推理链路超时。

客户端无法从该类异常中获得上游具体错误原因，因为断连发生在 HTTP 响应头之前，上游没有返回可读取的错误响应。

### 后续排查与改进

1. 使用日志中的 `request_id` 向模型或网关服务方查询对应请求的服务端日志。
2. 将请求阶段拆分记录：连接建立、TLS 握手、请求上传、等待响应头、读取响应体。
3. 记录远端地址、各阶段耗时、响应头是否收到、已读取响应体字节数和安全的请求追踪 ID。
4. 严禁记录 `Authorization`、API key 或其他敏感凭据。
5. 若服务端支持，评估流式响应和服务端 request ID，降低非流式请求在模型完整生成前长期等待首包的风险。

## 2026-09-09：诊断评审工作区触发 WorkspaceVanishedError

- 原因：Skill 在 `openclaw agents add` 前后删除七个上下文文件；当前 OpenClaw 对已初始化却被清空的工作区有保护，评审启动被拒绝。
- 结论：不能通过删除初始化文件实现上下文隔离。默认助手的 AGENTS/BOOTSTRAP 等虽然无私人数据，却包含人设、首次对话和主动任务规则，不适合作为诊断评审上下文。
- 修复：本地与内部共用 `_replace_judge_workspace_files`，初始化前后用 `judge/workspace_defaults/` 中的七个中立评审模板原子替换，不复制主 Bot 内容，不跟随软链或改动硬链源文件。原会话清理和 Agent 生命周期不变。
- 不删除/伪造 workspace-attestations 或手改 OpenClaw 的初始化状态。OpenClaw 自身可能在完成初始化时移除 BOOTSTRAP，交给引擎处理，Skill 不干预。
- 发布须包含整个 Skill 目录中的 workspace_defaults/*.md；模板缺失或不安全目标路径直接失败，不带着旧人设继续运行。
