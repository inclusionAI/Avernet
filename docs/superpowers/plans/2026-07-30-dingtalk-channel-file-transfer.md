# 钉钉 Channel 文件传输一期实施计划

## 一期范围

一期只支持钉钉 DM 与 `group_chat_scope=per_sender` 的文件入站。BCS 将钉钉换取的短期 URL 作为 `type=file` attachment 仅交给活动 `chat.send`；`chat.inject` 不携带该能力 URL。`conversation_shared` 群返回 `UnsupportedAttachment`。Web、已有图片和文本行为保持不变。

BaaS 由独立团队负责无损透传顶层 `attachments` 与可信 `materializationContext`，不负责下载、生成 workspace 路径或改写为 `session_file_id`。本期不修改 Backend、ConversationShared SessionFile、稳定 descriptor 与方案 HTML。

## BCS 与钉钉 Channel

- Domain 与 wire contract 增加 `AttachmentType::File`，沿用 `attachment_id`、`file_name`、`url` 及可选 MIME、大小、SHA-256、过期时间。
- 入站消息只要求正文非空或至少存在一个合法 attachment，不为纯文件消息伪造正文。
- 钉钉 adapter 解析 file callback，并用 `robotCode + downloadCode` 换取短期 URL；URL 不得进入日志、数据库、历史或错误文本。
- binding 解析后，真实 DM 与 PerSender 群允许文件；ConversationShared 群返回现有 `UnsupportedAttachment`，由 notifier 给出明确提示。
- 临时文件 attachment 只进入目标 Bot 的 `chat.send`；所有 `chat.inject` 都过滤它。未 @Bot 的群消息仍在兑换后的业务处理前被忽略。
- `stable_metadata()` 继续只持久化稳定字段并删除 `url/expires_at`。

钉钉 callback adapter 位于独立的内部 BCS 仓库，需在对应仓库使用同一 wire contract 完成 `file` callback 与临时 URL 兑换；公开 Avernet 分支不复制内部 provider 实现。

## OpenClaw BCN 插件

- 将图片准备扩展为通用受控 attachment staging。图片保留 magic bytes/MIME 检查；普通文件使用独立数量、大小、超时限制，并校验可用的过期时间、声明大小与 SHA-256。
- 继续复用 OpenClaw `fetchRemoteMedia` 与 `saveMediaBuffer`，只写入 OpenClaw 管理的 media store。任一文件失败则清理本批已保存文件，并在 Agent 启动前发送稳定 `FILE_*` 终态错误。
- 优先动态加载 `openclaw/plugin-sdk/channel-inbound` 的 `toInboundMediaFacts`，成功时只传 `media` facts；subpath 或函数缺失时回退到 `MediaPath/MediaPaths/MediaType/MediaTypes`。
- 能力检测结果按进程缓存；不提升 `openclaw >=2026.3.28` peer dependency。纯文件消息保持空 Body，host 路径与短期 URL 都不拼入 prompt。

## Engine 物化与 Runtime 交付

- 保留唯一 `ResourceMaterializationService`，增加内部 `materialize_chat_attachment()`；不新增 HTTP API，不调用 Engine 自己的 HTTP API，也不触发 Backend callback。
- 新增 `TemporaryUrlPullClient` plugin port 与受控 HTTP adapter。下载只允许 HTTPS，禁止 userinfo 和 redirect，拒绝非公网 DNS 地址，并执行大小、超时、摘要与原子落盘校验。
- Engine 不要求配置临时 URL host allowlist；下载器接受任意公网 HTTPS host，并继续执行 DNS 解析与公网 IP 校验、IP pinning、禁止重定向、大小和超时限制。可用 `ENGINE_TEMPORARY_URL_MAX_BYTES` 与 `ENGINE_TEMPORARY_URL_TIMEOUT_SECONDS` 收紧限制。
- WebSocket 在 ACK 前校验 attachment schema、HTTPS URL 与 `materializationContext`；ACK 后执行网络下载。失败通过稳定 `ATTACHMENT_MATERIALIZATION_*` 终态事件返回，且不调用 `chat_plugin.stream()`。
- 多文件 all-or-nothing；失败或取消时清理本批已发布文件和临时文件。Manifest 记录 `source_kind=temporary_url`、attachment ID 与 URL 哈希，不记录原 URL。
- 成功后删除下游远程 file attachment，生成受控 placeholder，并复用 `ResourceReferenceService` 校验 session 所属、文件大小和摘要，得到 `<file-ref name path>` 与 `extraParams.materializedFiles` 后再启动 Runtime。

## 统一 workspace 路径

Engine Core 提供内部纯函数：

```python
build_session_file_relative_path(
    scope_key_hash=...,
    session_key_hash=...,
    resource_id=...,
    filename=...,
)
```

路径规则与 Backend SessionFile v1 保持一致：

```text
.teamclaw/session-files/{scope_key_hash}/{session_key_hash}/{resource_id}/{safe_filename}
```

现有 Backend 物化入口用该函数计算期望路径并继续校验上游路径；聊天入口自行生成路径，不接受 BCS/BaaS 提供的本地路径。BaaS 仅补充：

```json
{
  "materializationContext": {
    "layout_version": "session_file_v1",
    "scope_key_hash": "<64位sha256>"
  }
}
```

## 错误与发布

`chat.send` 保持先 ACK、后异步准备：纯协议错误在 ACK 前拒绝；DNS、下载、超限、摘要、落盘或 staging 错误在 ACK 后发送终态 error。错误和日志不得包含 URL、凭证、query、host 路径或 workspace 绝对路径。

测试覆盖 BCS file contract、DM/PerSender/ConversationShared、`chat.inject` URL 过滤、BCN facts-first 与 legacy fallback、文件失败清理、Engine 路径契约、Manifest、物化后 `<file-ref>`、URL 移除、失败回滚及现有 Web/图片回归。发布顺序为 Engine、BaaS 透传、BCN 插件、BCS/钉钉能力，开关默认关闭后逐步启用。
