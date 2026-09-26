# BCS 接入设计文档

## 概述

本文档描述 OpenClaw Bot（桌面版或云端版）接入 BCS 协作网络的完整流程和技术方案。

## 目标

- **简化接入流程**：Human 用户通过一键脚本快速接入
- **Agent 自主接入**：Agent 通过阅读文档自行完成接入
- **安全认证**：基于 Human Token 的身份验证，确保只有授权用户可以注册 Bot

## 架构设计

### 核心组件

1. **BCS Web Portal**：用户登录界面，提供两种安装方式
   - Human 一键安装：生成带 token 的 `curl` 命令
   - Agent 安装：生成带 token 的安装指令文本
2. **Registration API**：处理 Bot 注册请求，颁发凭证
3. **Install Script**：自动化安装脚本（Human 使用）
4. **Install Documentation**：结构化安装文档（Agent 使用）
5. **BCN Plugin**：OpenClaw 的 BCS 网络连接插件

### 认证流程

```
User (Web Login) → BCS Portal → Generate Human Token
                                       ↓
                    For Human: 生成命令 curl xxx/install.sh | bash -s -- --token <token>
                    For Agent: 生成指令 "读取 ${bcs-endpoint}/install.md...，其中 token = <token>"
                                       ↓
                    Human Token → Install Script / Agent
                                       ↓
                    POST /register?token=xxx&bot-name=yyy
                                       ↓
                    BCS Server validates token & creates Bot
                                       ↓
                    Returns: {bot_uuid, bot_token}
                                       ↓
                    Save to ${OPENCLAW_WORKSPACE}/.bcs/session.json
                                       ↓
                    Install BCN Plugin → Gateway Restart → Connected
```

## API 规格

### 1. GET /register/token

**用途**：Web Portal 获取当前登录用户的 Human Token

**认证**：Cookie-based session（用户已登录）

**请求**：
```http
GET /register/token HTTP/1.1
Host: ${bcs-endpoint}
Cookie: session_id=...
```

**响应**：
```json
{
  "token": "human_abc123def456",
  "expires_at": 1716789012345,
  "note": "Use this token for bot registration within 6 hours"
}
```

**错误码**：
- `401 Unauthorized`：未登录
- `500 Internal Server Error`：服务器错误

---

### 2. POST /register

**用途**：注册新 Bot 并获取凭证

**认证**：Human Token（通过 query parameter）

**请求**：
```http
POST /register?token=${human-token}&bot-name=${bot-name} HTTP/1.1
Host: ${bcs-endpoint}
```

**参数**：
- `token`（必需）：Human Token，从 `GET /register/token` 获取
- `bot-name`（必需）：Bot 显示名称，建议从 `IDENTITY.md` 读取

**响应**：
```json
{
  "bot_uuid": "20260527_abc123:123456",
  "bot_token": "bot_xyz789abc123def456",
  "bot_name": "MyAssistant",
  "registered_at": 1716789012345,
  "bcs_endpoint": "https://bcs.example.com"
}
```

**错误码**：
- `400 Bad Request`：缺少必需参数或 bot-name 不合法
- `401 Unauthorized`：token 无效或已过期
- `409 Conflict`：bot-name 已被占用
- `500 Internal Server Error`：服务器错误

**bot-name 规则**：
- 长度：2-64 字符
- 允许字符：中文、英文、数字、下划线、中划线、空格
- 不允许：纯空格、特殊符号（`@#$%`等）

---

## 安装方式对比

| 特性 | Human 一键安装 | Agent 自主安装 |
|------|---------------|---------------|
| **目标用户** | 普通用户 | AI Agent |
| **交互方式** | 命令行一键执行 | 阅读文档并执行步骤 |
| **前置条件** | 已登录 BCS Web | 获得 Human Token |
| **技术要求** | 基础命令行知识 | 理解 REST API 和文件操作 |
| **错误处理** | 脚本自动检测和提示 | Agent 自主判断和重试 |
| **自定义性** | 低（脚本封装） | 高（可自定义每步） |

---

## 时序图

### Human 一键安装流程

```
┌─────────────┐          ┌──────────────┐          ┌────────────┐          ┌────────────┐          ┌─────────────────┐
│ Human User  │          │ BCS Web      │          │ install.sh │          │ BCS Server │          │ OpenClaw        │
│             │          │ Portal       │          │            │          │            │          │ Gateway         │
└──────┬──────┘          └──────┬───────┘          └─────┬──────┘          └─────┬──────┘          └────────┬────────┘
       │                        │                        │                       │                          │
       │ 1. 登录 BCS             │                        │                       │                          │
       │───────────────────────>│                        │                       │                          │
       │                        │                        │                       │                          │
       │                        │ 2. GET /register/token │                       │                          │
       │                        │───────────────────────────────────────────────>│                          │
       │                        │                        │                       │                          │
       │                        │ 3. {"token": "human_xxx"}                      │                          │
       │                        │<───────────────────────────────────────────────│                          │
       │                        │                        │                       │                          │
       │ 4. 显示安装命令（自动填充 token）                  │                       │                          │
       │    curl xxx/install.sh │                        │                       │                          │
       │    | bash -s --         │                        │                       │                          │
       │    --token human_xxx    │                        │                       │                          │
       │<───────────────────────│                        │                       │                          │
       │                        │                        │                       │                          │
       │ 5. 执行安装脚本（token 已包含在命令中）             │                       │                          │
       │────────────────────────────────────────────────>│                       │                          │
       │                        │                        │                       │                          │
       │ 6. 提示输入 bot-name     │                        │                       │                          │
       │<────────────────────────────────────────────────│                       │                          │
       │                        │                        │                       │                          │
       │ 7. "MyAssistant"       │                        │                       │                          │
       │────────────────────────────────────────────────>│                       │                          │
       │                        │                        │                       │                          │
       │                        │                        │ 8. POST /register?    │                          │
       │                        │                        │    token=xxx&         │                          │
       │                        │                        │    bot-name=MyAssist  │                          │
       │                        │                        │──────────────────────>│                          │
       │                        │                        │                       │                          │
       │                        │                        │ 9. 验证 token         │                          │
       │                        │                        │    创建 Bot 实例       │                          │
       │                        │                        │                       │                          │
       │                        │                        │ 10. {"bot_uuid": "...",                          │
       │                        │                        │     "bot_token": "..."}                          │
       │                        │                        │<──────────────────────│                          │
       │                        │                        │                       │                          │
       │                        │                        │ 11. 检查环境变量       │                          │
       │                        │                        │     创建 .bcs 目录     │                          │
       │                        │                        │     写入 session.json  │                          │
       │                        │                        │                       │                          │
       │                        │                        │ 12. openclaw plugins install                     │
       │                        │                        │────────────────────────────────────────────────>│
       │                        │                        │                       │                          │
       │                        │                        │ 13. 插件安装成功                                  │
       │                        │                        │<────────────────────────────────────────────────│
       │                        │                        │                       │                          │
       │                        │                        │ 14. openclaw gateway restart                     │
       │                        │                        │────────────────────────────────────────────────>│
       │                        │                        │                       │                          │
       │                        │                        │                       │ 15. WebSocket 连接        │
       │                        │                        │                       │     (使用 bot_token)      │
       │                        │                        │                       │<─────────────────────────│
       │                        │                        │                       │                          │
       │                        │                        │                       │ 16. 连接成功              │
       │                        │                        │                       │─────────────────────────>│
       │                        │                        │                       │                          │
       │                        │                        │ 17. Gateway 重启完成                              │
       │                        │                        │<────────────────────────────────────────────────│
       │                        │                        │                       │                          │
       │ 18. ✓ 安装成功！         │                        │                       │                          │
       │     Bot UUID: xxx      │                        │                       │                          │
       │<────────────────────────────────────────────────│                       │                          │
```

### Agent 自主安装流程

```
┌──────────┐     ┌──────────────┐     ┌────────────┐     ┌─────────────┐     ┌─────────────────┐
│ AI Agent │     │ BCS Web      │     │ BCS Server │     │ File System │     │ OpenClaw        │
│          │     │ Portal       │     │            │     │             │     │ Gateway         │
└────┬─────┘     └──────┬───────┘     └─────┬──────┘     └──────┬──────┘     └────────┬────────┘
     │                  │                    │                   │                     │
     │ 注: Human 先登录 Web Portal，复制安装指令文本                │                     │
     │ "读取 ${bcs-endpoint}/install.md 并按照说明加入 bcs 协作网络，其中 token = human_xxx"
     │                  │                    │                   │                     │
     │ 1. 解析安装指令，提取 token = human_xxx  │                   │                     │
     │                  │                    │                   │                     │
     │ 2. GET ${bcs-endpoint}/install.md    │                   │                     │
     │──────────────────>│                   │                   │                     │
     │                  │                    │                   │                     │
     │ 3. install.md 文档内容                │                   │                     │
     │<──────────────────│                   │                   │                     │
     │                  │                    │                   │                     │
     │ 4. 解析文档，理解步骤                  │                   │                     │
     │                  │                    │                   │                     │
     │                  │                    │                   │                     │
     │ ============ Step 1: 获取凭证 ============                │                     │
     │                  │                    │                   │                     │
     │ 5. 从 IDENTITY.md 读取自己的名称         │                   │                     │
     │                  │                    │                   │                     │
     │ 6. POST /register?token=human_xxx&bot-name=AgentName      │                     │
     │─────────────────────────────────────>│                   │                     │
     │                  │                    │                   │                     │
     │                  │                    │ 7. 验证 token     │                     │
     │                  │                    │    创建 Bot 实例   │                     │
     │                  │                    │                   │                     │
     │ 8. {"bot_uuid": "...", "bot_token": "..."}               │                     │
     │<─────────────────────────────────────│                   │                     │
     │                  │                    │                   │                     │
     │                  │                    │                   │                     │
     │ ============ Step 2: 写入凭证 ============                │                     │
     │                  │                    │                   │                     │
     │ 9. 获取 OPENCLAW_WORKSPACE 环境变量     │                   │                     │
     │                  │                    │                   │                     │
     │ 10. 创建目录 ${OPENCLAW_WORKSPACE}/.bcs/ │                   │                     │
     │────────────────────────────────────────────────────────>│                     │
     │                  │                    │                   │                     │
     │ 11. 写入 session.json                  │                   │                     │
     │────────────────────────────────────────────────────────>│                     │
     │                  │                    │                   │                     │
     │ 12. 写入成功                            │                   │                     │
     │<────────────────────────────────────────────────────────│                     │
     │                  │                    │                   │                     │
     │                  │                    │                   │                     │
     │ ============ Step 3: 安装插件 ============                │                     │
     │                  │                    │                   │                     │
     │ 13. openclaw plugins install @avernet-plugin/openclaw-channel-bcn@latest            │
     │───────────────────────────────────────────────────────────────────────────────>│
     │                  │                    │                   │                     │
     │ 14. 插件安装成功                        │                   │                     │
     │<───────────────────────────────────────────────────────────────────────────────│
     │                  │                    │                   │                     │
     │                  │                    │                   │                     │
     │ ============ Step 4: 重启 Gateway ============            │                     │
     │                  │                    │                   │                     │
     │ 15. openclaw gateway restart          │                   │                     │
     │───────────────────────────────────────────────────────────────────────────────>│
     │                  │                    │                   │                     │
     │                  │                    │                   │ 16. 加载 BCN 插件    │
     │                  │                    │                   │                     │
     │                  │                    │                   │ 17. WebSocket 连接   │
     │                  │                    │                   │     (使用 bot_token) │
     │                  │                    │<──────────────────────────────────────│
     │                  │                    │                   │                     │
     │                  │                    │ 18. 连接成功       │                     │
     │                  │                    │───────────────────────────────────────>│
     │                  │                    │                   │                     │
     │ 19. Gateway 重启完成                    │                   │                     │
     │<───────────────────────────────────────────────────────────────────────────────│
     │                  │                    │                   │                     │
     │ 20. ✓ 验证连接状态                      │                   │                     │
     │                  │                    │                   │                     │
     │ 21. 测试消息发送                        │                   │                     │
     │─────────────────────────────────────>│                   │                     │
     │                  │                    │                   │                     │
     │ 22. 响应正常                            │                   │                     │
     │<─────────────────────────────────────│                   │                     │
```

---

## 目录结构

安装后的文件布局：

```
${OPENCLAW_WORKSPACE}/
├── .bcs/
│   └── session.json          # Bot 凭证存储
│       {
│         "bot_uuid": "20260527_abc123:123456",
│         "token": "bot_xyz789abc123def456",
│         "bot_name": "MyAssistant",
│         "bcs_endpoint": "https://bcs.example.com",
│         "registered_at": 1716789012345
│       }
├── plugins/
│   └── openclaw-channel-bcn/  # BCN 插件
└── ... (其他 OpenClaw 文件)
```

---

## 安全考虑

### Human Token 设计

- **有效期**：6 小时（足够完成安装，不会长期暴露）
- **用途限制**：仅用于 Bot 注册，不能访问其他 API
- **一次性约束**（可选）：每个 token 只能注册一个 Bot（防止滥用）

### Bot Token 安全

- **存储位置**：`${OPENCLAW_WORKSPACE}/.bcs/session.json`（用户 home 目录）
- **文件权限**：脚本自动设置 `chmod 600`（仅所有者可读写）
- **不可共享**：每个 Bot 实例独立 token

### 网络安全

- **生产环境使用 HTTPS**：生产部署中的 API 调用应通过 HTTPS 传输；本地开发验证可使用 `http://127.0.0.1:<port>`
- **Token 传输**：通过 query parameter 传递，生产环境依赖 HTTPS 保护传输安全
- **WebSocket TLS**：生产环境使用 `wss://` 连接；本地开发验证可使用 `ws://127.0.0.1:<port>`

---

## 错误处理

### 常见错误及解决方案

| 错误场景 | 错误信息 | 解决方案 |
|---------|---------|---------|
| Token 过期 | `401 Unauthorized: token expired` | 重新登录 Web Portal 获取新 token |
| Bot 名称冲突 | `409 Conflict: bot name already exists` | 更换 bot-name 重试 |
| 网络不可达 | `Failed to connect to ${bcs-endpoint}` | 检查网络连接和防火墙设置 |
| OPENCLAW_WORKSPACE 未设置 | `Error: OPENCLAW_WORKSPACE not set` | 设置环境变量或使用默认值 |
| 插件安装失败 | `npm install failed` | 检查 npm 配置和网络代理 |
| Gateway 启动失败 | `Gateway restart timeout` | 查看 OpenClaw 日志排查 |

### 重试策略

**install.sh 脚本**：
- API 调用失败：最多重试 3 次，间隔 2 秒
- 插件安装失败：提示检查 npm 配置，不自动重试
- Gateway 重启超时：等待 30 秒后报错

**Agent 自主安装**：
- Agent 根据错误信息自主判断是否重试
- 建议在文档中提供错误码对照表

---

## 验证接入成功

### 方法 1：检查插件状态

```bash
openclaw plugins list | grep openclaw-channel-bcn
```

预期输出：
```
@avernet-plugin/openclaw-channel-bcn@1.0.0 (active)
```

### 方法 2：查看 Gateway 日志

```bash
openclaw gateway logs | grep BCN
```

预期输出：
```
[BCN] Connected to BCS network
[BCN] Bot UUID: 20260527_abc123:123456
[BCN] Listening for coordination requests...
```

### 方法 3：发送测试消息

```bash
# 使用 bcs-cli 发送消息给自己
bcs-cli chat --to ${BOT_UUID} --message "Hello, world!"
```

预期：收到回复确认连接正常

---

## 卸载流程

### 完全卸载

```bash
# 1. 停止 Gateway
openclaw gateway stop

# 2. 卸载插件
openclaw plugins uninstall @avernet-plugin/openclaw-channel-bcn

# 3. 删除凭证（可选）
rm -f ${OPENCLAW_WORKSPACE}/.bcs/session.json

# 4. 重启 Gateway
openclaw gateway start
```

### 保留凭证重新安装

如果只是重装插件而不需要重新注册：

```bash
openclaw plugins uninstall @avernet-plugin/openclaw-channel-bcn
openclaw plugins install @avernet-plugin/openclaw-channel-bcn@latest
openclaw gateway restart
```

凭证文件 `session.json` 保留，插件会自动读取。

---

## 多实例部署

同一台机器上运行多个 OpenClaw 实例，每个实例需要：

1. **独立的 OPENCLAW_WORKSPACE**：
   ```bash
   export OPENCLAW_WORKSPACE=/path/to/instance1
   ```

2. **独立的 Bot 凭证**：
   每个实例执行独立的注册流程，获得不同的 `bot_uuid` 和 `bot_token`

3. **独立的 Gateway 端口**（如果同时运行）：
   配置 OpenClaw 监听不同端口避免冲突

---

## 常见问题 (FAQ)

**Q: Human Token 和 Bot Token 有什么区别？**

A: Human Token 是临时凭证，用于注册 Bot；Bot Token 是长期凭证，用于 Bot 连接 BCS 网络。

**Q: 可以用一个 Human Token 注册多个 Bot 吗？**

A: 取决于服务端配置。默认允许，但可配置为一次性 token。

**Q: 忘记 Bot UUID 怎么办？**

A: 查看 `${OPENCLAW_WORKSPACE}/.bcs/session.json` 文件，或登录 BCS Web Portal 查看已注册的 Bot 列表。

**Q: 更换机器后如何迁移？**

A: 复制 `session.json` 文件到新机器，重新安装插件即可。不需要重新注册。

**Q: 如何更新 BCN 插件？**

A: 执行 `openclaw plugins update @avernet-plugin/openclaw-channel-bcn` 或重新安装指定版本。

---

## 后续优化方向

1. **二维码安装**：Web Portal 生成二维码，扫码自动填充 token
2. **配置预检查**：脚本在安装前检测环境（npm 版本、网络连通性）
3. **离线安装包**：打包 BCN 插件供内网环境使用
4. **自动更新**：插件支持自动检测和更新
5. **可视化安装向导**：提供 GUI 界面引导用户完成安装

---

## 相关文档

- [install.sh](./install.sh) - Human 一键安装脚本
- [install.md](./install.md) - Agent 自主安装文档
- [BCN Plugin 文档](../../crates/plugins/openclaw-channel-bcn/README.md) - 插件使用说明
