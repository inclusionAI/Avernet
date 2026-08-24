# Chat 文件上传并生成 BaaS 分享链接

## 目标

在已授权的 Bot Chat 中，用户明确要求分享当前 workspace 的已生成文件时，Skill 调用独立 CLI。CLI 仅通过 Engine 本地 Unix domain socket 请求分享，并从 Chat runner 已注入的 `HITL_SESSION_KEY` 取得当前 session key。Engine 持有运行时 BaaS 身份并复用既有 Session File 的 `upload-url -> OSS upload -> complete -> share-link` 协议，返回 24 小时短期下载链接。

## 范围与边界

- 仅修改 Engine、独立 CLI 和独立 Skill；不改 BaaS、Backend、Gateway/Chat handler、Session File 或既有 `agentclaw-cli`。
- UDS 采用单行 JSON RPC，不作为现有 Engine FastAPI 路由的一部分，也不暴露到 Bot proxypass。
- 请求只能包含 workspace 相对路径。Engine 要求 `OPENCLAW_WORKSPACE_DIR`，以真实路径校验 workspace 边界，拒绝绝对路径、符号链接、目录和缺失文件。
- BaaS tenant、BaaS URL、OSS allowlist 与 socket path 只从 Engine 环境配置加载；Session File 路径不需要额外控制面 headers，CLI 和 Skill 不接收或记录 BaaS 身份。
- BaaS share URL 只在成功响应和 Chat 最终用户回复中出现，不写入服务日志、数据库、CLI 文件或测试报告；分享上下文中的 HTTP access log 也必须抑制，避免 session、transfer ID 或签名 URL 外泄。

## 接口

- UDS request: `{"method":"share","relative_path":"report.txt","session_key":"<runner-injected>"}`。CLI 不提供 session 参数，缺少 `HITL_SESSION_KEY` 时直接失败，绝不猜测或读取别的会话。
- UDS success: `{"ok":true,"data":{"file_name":"report.txt","size_bytes":7,"share_url":"...","expires_at":"..."}}`。
- UDS failure: `{"ok":false,"error":{"code":"..."}}`；错误码不包含绝对路径、headers、transfer ID 或 URL。
- CLI: `teamclaw-file-share share <relative-path>`，输出同一结构化结果并在失败时返回非零退出码。

## 手工投放

- Engine profile 必须同时配置 `OPENCLAW_WORKSPACE_DIR`、绝对
  `ENGINE_CHAT_FILE_SHARE_SOCKET`、`ENGINE_CHAT_FILE_SHARE_BAAS_BASE_URL`、
  `ENGINE_CHAT_FILE_SHARE_TENANT` 和 `ENGINE_CHAT_FILE_SHARE_ALLOWED_OSS_HOSTS`。socket 父目录由运行时以
  owner-only 权限创建，socket 在监听前即以 `0600` bind；若缺失任一值，功能不启动。
- 仅手工复制 `tools/teamclaw-file-share` 目录并将其入口加入 Bot runtime
  的 `PATH`；在 Skill 运行环境中只设置同一个
  `TEAMCLAW_FILE_SHARE_SOCKET`，并继承 Chat runner 提供的 `HITL_SESSION_KEY`。不得把 BaaS tenant、session、headers 或 TTL 传给 CLI。
- 仅手工复制 `skills/infra/chat-file-share` 并显式启用。它不注册技能中心，
  不自动安装或配置 CLI。

## 验收

1. 核心服务与既有 Session File client 覆盖路径边界、session request、上传完成/失败和严格 envelope。
2. UDS 覆盖输入 shape、错误映射、私有父目录、监听前 socket 权限、生命周期启动/清理与成功响应。
3. CLI 覆盖 JSON 输出、退出码和显式 socket 配置；Skill 提供显式分享触发规则与正反触发样例。
4. 手工将 CLI 与 Skill 投放到 Bot `20260723_vae8mlcp` 后，使用原生 Chat 请求分享测试文件，浏览器实际下载并保留 Bot 源文件、BaaS transfer/object 与下载副本。QA 仅记录状态、文件名、字节数、哈希和结论。
