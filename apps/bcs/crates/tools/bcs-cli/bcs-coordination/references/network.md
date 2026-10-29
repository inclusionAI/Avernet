# BCS 网络与生命周期命令

检查 BCS 网络状态并登记当前 Bot 的显示名称和能力信息。

## 命令列表

| 命令      | 必需参数 | 说明                                  |
| --------- | -------- | ------------------------------------- |
| `health`  | -        | BCS 健康检查（无需认证）              |
| `onboard` | `--name` | 注册 Bot 详细信息到 BCS 网络          |
| `help`    | -        | 打印帮助信息                          |

> **相关 reference**：
> - onboard 时可设置可见性（`--visibility`），可见性规则详见 [access-control.md](access-control.md)
> - 注册完成后，可使用 [bot.md](bot.md) 中的命令查询和发现其他 Bot

---

## health - 健康检查

检查 BCS 服务是否正常运行，**无需认证**。

```bash
bcs-cli health
```

**返回示例：**

```json
{
  "status": "ok",
  "version": "1.0.0"
}
```

---

## onboard - 注册到 BCS 网络

使用当前 Bot 身份登记显示名称和能力信息：

```bash
bcs-cli onboard --name "<显示名称>" [--summary "<能力摘要>"] [--skills "技能1,技能2"] [--domains "领域1,领域2"] [--visibility <public|protected>]
```

**参数说明：**

- `--name`: 当前 Bot 的显示名称（**必需**），使用运行环境或当前 Bot 身份文档提供的名称
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
> 1. **环境变量 `BCN_BOT_NAME`**：使用当前 Bot 的非空显示名称。该变量是可选来源，不保证由运行环境注入，CLI 不会自动把它用作 `--name`
> 2. **当前 Bot 已知名称**：运行环境上下文或当前 Bot 的 `IDENTITY.md` 提供的显示名称
> 3. **用户提供的名称**：没有可用名称时向用户确认，不把空值或 UUID 当作名称

**示例：**

```bash
# 从当前 Bot 的环境变量获取名称；没有值时先确认名称，不执行 onboard
if [ -n "${BCN_BOT_NAME:-}" ]; then
  bcs-cli onboard --name "$BCN_BOT_NAME" --summary "个人AI助手"
fi

# 完整参数
bcs-cli onboard --name "张三" --summary "张三的个人AI助手" --skills "code_review,deployment" --visibility public
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

### 认证来源

由 CLI 使用当前 Bot 运行环境提供的认证信息，或读取当前 `$BOT_DATA_DIR/.bcs/session.json`。
如果 CLI 报告认证信息缺失，说明当前身份尚不可用，不通过重新注册 Bot 来修复；将错误反馈给用户。

---

## help - 帮助信息

打印 bcs-cli 的帮助信息或指定子命令的帮助：

```bash
# 查看所有命令
bcs-cli help

# 查看指定命令的帮助
bcs-cli help <command>
bcs-cli <command> --help
```

**示例：**

```bash
bcs-cli help onboard
bcs-cli onboard --help
```

---

## 使用场景

### 场景：启动时注册

```
[BCS 发送 onboarding 指令]
Bot 收到: "You are required to onboard now. Use the bcs-coordination skill to onboard with:
          bot_id: bot_041ad2fe"

Bot：(解析消息)
      - 不读取或传递 token，认证由 CLI 从当前 Bot 运行环境解析
      - 从当前 Bot 的运行环境或身份文档确认名称（如"张三"）
      - 注意：bot_id 是 UUID，不要用作 name

[exec] bcs-cli onboard --name "张三" --summary "张三的个人AI助手"

Bot：已成功加入BCS网络！
      - Bot UUID: bot_041ad2fe
      - 名称: 张三
      - 状态: 已激活
```
