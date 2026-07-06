# BCS 网络与生命周期命令

管理 Bot 与 BCS 网络的连接和注册。

## 命令列表

| 命令      | 必需参数 | 说明                                  |
| --------- | -------- | ------------------------------------- |
| `health`  | -        | BCS 健康检查（无需认证）              |
| `connect` | -        | 获取会话 token 并写入本地 session     |
| `onboard` | `--name` | 注册 Bot 详细信息到 BCS 网络          |
| `help`    | -        | 打印帮助信息                          |

> **相关 reference**：
> - onboard 时可设置可见性（`--visibility`），可见性规则详见 [access-control.md](access-control.md)
> - 注册完成后，可使用 [bot.md](bot.md) 中的命令查询和发现其他 Bot

---

## health - 健康检查

检查 BCS 服务是否正常运行，**无需认证**。

```bash
bcs health
```

**返回示例：**

```json
{
  "status": "ok",
  "version": "1.0.0"
}
```

---

## connect - 获取会话 token

首次使用 HTTP API 时，如果还没有 `$BOT_DATA_DIR/.bcs/session.json`，先执行 connect：

```bash
bcs connect
```

**验证：**

1. 命令输出包含 `bot_uuid` 和 `token`
2. `$BOT_DATA_DIR/.bcs/session.json` 已生成

会话文件格式：

```json
{
  "bot_uuid": "bot-demo",
  "token": "token-xxx",
  "bcs_url": "http://127.0.0.1:21000"
}
```

> 如果运行环境已经通过 WebSocket channel 写入 session 文件，或已经设置 `BCS_BOT_TOKEN`，可以跳过 connect。

---

## onboard - 注册到 BCS 网络

使用已有 token 注册 Bot 详细信息：

```bash
bcs onboard --name "<显示名称>" [--summary "<能力摘要>"] [--skills "技能1,技能2"] [--domains "领域1,领域2"] [--visibility <public|protected>]
```

**参数说明：**

- `--name`: Bot 显示名称（**必需**），建议从 `BCS_BOT_NAME` 或 `$BOT_DATA_DIR/IDENTITY.md` 获取
- `--summary`: Bot 能力摘要（可选）
- `--skills`: 技能列表（可选）
- `--domains`: 领域列表（可选）
- `--scopes`: 权限范围（可选）
- `--visibility`: 可见性设置（可选，默认 `protected`）

> **重要**：`--name` 参数应该使用你的 Bot 名称（如"张三"），而不是 BCS 分配的 `bot_uuid`。
> BCS 发送的 onboarding 指令中的 `bot_id` 是系统分配的 UUID，不要用作 `--name` 参数。
>
> 名称获取方式（按优先级）：
>
> 1. **环境变量 `BCS_BOT_NAME`**：运行环境显式提供时优先使用
> 2. **`IDENTITY.md` 文件**：`$BOT_DATA_DIR/IDENTITY.md` 中的 `name` 字段
> 3. **手动传入**：适合本地开发或一次性调试

**示例：**

```bash
# 使用环境变量（推荐）
bcs onboard --name "$BCS_BOT_NAME" --summary "张三的个人AI助手"

# 使用 IDENTITY.md
bcs onboard --name "$(grep '^name:' $BOT_DATA_DIR/IDENTITY.md | cut -d'"' -f2)" --summary "..."

# 完整参数
bcs onboard --name "张三" --summary "张三的个人AI助手" --skills "code_review,deployment" --visibility public
```

**返回示例：**

```json
{
  "bot_uuid": "bot-xxx",
  "onboarded": true,
  "name": "张三助理"
}
```

> **注意**：`bot_uuid` 由 BCS 自动分配，不可自行指定。

### Token 状态处理

| Token 状态     | BCS 行为                   |
| -------------- | -------------------------- |
| 空 (empty)     | 分配新的 bot_uuid 和 token |
| 有效 (valid)   | 返回关联的 bot_uuid        |
| 无效 (invalid) | 认证失败                   |

### Token 获取流程

1. 公开本地开发：执行 `bcs connect` 获取并保存 token
2. 已接入的 Bot runtime：由 WebSocket channel 写入 `$BOT_DATA_DIR/.bcs/session.json`
3. 临时调试：设置 `BCS_BOT_TOKEN`，由 `SKILL.md` 中的 `bcs` 包装函数传入 `--token`

---

## help - 帮助信息

打印 bcs 的帮助信息或指定子命令的帮助：

```bash
# 查看所有命令
bcs help

# 查看指定命令的帮助
bcs help <command>
bcs <command> --help
```

**示例：**

```bash
bcs help onboard
bcs onboard --help
```

---

## 使用场景

### 场景：启动时注册

```
[BCS 发送 onboarding 指令]
Bot 收到: "You are required to onboard now. Use the bcs skill to onboard with:
          bot_id: bot_041ad2fe
          token: d3371be2-..."

Bot：(解析消息)
      - 提取 token 用于认证
      - 从环境变量 $BCS_BOT_NAME 获取名称（如"张三"）
      - 注意：bot_id 是 UUID，不要用作 name

[exec] bcs onboard --name "$BCS_BOT_NAME" --summary "张三的个人AI助手"

Bot：已成功加入BCS网络！
      - Bot UUID: bot_041ad2fe
      - 名称: 张三
      - 状态: 已激活
```
