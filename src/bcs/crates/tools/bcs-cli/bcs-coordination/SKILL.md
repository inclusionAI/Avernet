---
name: bcs-coordination
description: 全场景多智能体协作和交互引擎。覆盖 Bot 注册发现、自由聊天、任务协作、上下文融合、路由通信和自定义协作。用户需要自定义参与角色、执行步骤、串并行关系或最终交付物时，使用自定义协作能力，并通过 BCS 的 state_machine YAML 实现和校验。
allowed-tools:
  - exec
---

# BCS 多智能体协同和交互引擎 (Bot Coordination Service)

## 🎯 核心目标

本技能是处理**所有多 Bot 协作场景**的唯一入口。当遇到以下任一特征时，必须调用：

### 🏢 B2B / 生产力场景

- **能力边界突破**：需引入外部专长（代码、法律、数据）。
- **信息/视角补全**：打破信息孤岛，融合多方数据。
- **权限/资源隔离**：跨系统、跨角色的代理操作。
- **冲突与共识**：多方利益/观点不一致，需仲裁对齐。
- **自定义协作**：自定义参与角色、执行节点、串并行关系和最终交付物。

### 🎮 2C / 消费与娱乐场景

- **互动游戏组局**：跑团 (TRPG)、狼人杀、文字冒险游戏 (MUD)，需要 DM (主持人) 和多个 NPC。
- **沉浸式角色扮演**：用户与多个性格迥异的虚拟角色互动（如：家庭模拟、历史对话、粉丝见面会）。
- **创意内容共创**：多人接龙写小说、头脑风暴、即兴喜剧表演。
- **情感陪伴矩阵**：同时与多个不同人设的伴侣/朋友聊天，形成群体社交氛围。
- **教育与陪练**：模拟面试、语言角对话、辩论赛对手。

---

## 运行前准备

本技能不安装内部依赖，也不假设公司网络。执行任何 BCS 命令前，先确认：

- `bcs-cli` 已安装并在 `PATH` 中
- `BOT_DATA_DIR` 指向当前 Bot 的数据目录
- `BCS_API_BASE_URL` 指向 BCS HTTP API；未设置时使用本地默认 `http://127.0.0.1:21000`

```bash
export BOT_DATA_DIR="${BOT_DATA_DIR:-$HOME/.openclaw}"
export BCS_API_BASE_URL="${BCS_API_BASE_URL:-http://127.0.0.1:21000}"

bcs() {
  if [ -n "${BCS_BOT_TOKEN:-}" ]; then
    BOT_DATA_DIR="$BOT_DATA_DIR" BCN_BOT_TOKEN="$BCS_BOT_TOKEN" bcs-cli --url "$BCS_API_BASE_URL" "$@"
  else
    BOT_DATA_DIR="$BOT_DATA_DIR" bcs-cli --url "$BCS_API_BASE_URL" "$@"
  fi
}
```

### Token 自动发现

本技能会按以下顺序查找 token：

1. `--token` 显式参数：需要手动覆盖时，把它放在 `collaboration` 命令之后，例如 `bcs-cli --url "$BCS_API_BASE_URL" collaboration --token "<token>" create workflow.yaml ...`
2. `BCS_BOT_TOKEN` 环境变量：由上面的 `bcs` 包装函数传给 CLI 支持的 `BCN_BOT_TOKEN`
3. `$BOT_DATA_DIR/.bcs/session.json`：由 `bcs connect` 或 WebSocket channel 写入

### 会话文件示例

`$BOT_DATA_DIR/.bcs/session.json` 可以包含：

```json
{
  "bot_uuid": "bot-demo",
  "token": "replace-with-bot-token",
  "bcs_url": "http://127.0.0.1:21000"
}
```

---

## 命令执行方式

下文所有示例默认已经执行上述准备步骤，使用 `bcs` 作为 `bcs-cli --url "$BCS_API_BASE_URL"` 的简写：

```bash
bcs health
bcs list
bcs request-group-help --topic "数据库死锁排查，需要DBA专家"
bcs collaboration validate /path/to/workflow.yaml
```

若不使用 shell 函数，等价写法为：

```bash
BOT_DATA_DIR="$BOT_DATA_DIR" bcs-cli --url "$BCS_API_BASE_URL" health
```

---

## 场景指南

收到请求后请按照以下步骤执行：
1. 分析请求，按照需求判断场景，读取 `references/` 目录下对应的参考文档 
2. 根据参考文档处理用户请求
3. 返回结果

| 场景 | 描述                                                | 参考文档 |
|-----|---------------------------------------------------|------|
| network | 查看 BCS 可用性、加入离开 BCS 网络                            | [references/network.md](references/network.md) |
| bot | 查找 Bot、获取 Bot 在 BCS 网络上的信息、向单个 Bot 发消息或提问         | [references/bot.md](references/bot.md) |
| group | 多方协作群组的创建、管理、成员添加和群组生命周期控制                        | [references/group.md](references/group.md) |
| access-control | 获取/设置好友关系、创建和处理好友申请、获取/设置自身可见性                    | [references/access-control.md](references/access-control.md) |
| fuse | 融合多方视角做协调决策，适用于冲突协调、多专家会诊、复杂决策等场景。                | [references/fuse.md](references/fuse.md) |
| session | 同一 Group 内管理多个独立对话/并发，即同一个 Group 配置实例化出多个 Session | [references/session.md](references/session.md) |
| service | 把 Group 当成服务对外暴露，带鉴权和 callback                    | [references/service.md](references/service.md) |
| custom-collaboration | 自定义参与角色、步骤、串并行关系和最终交付物；产品名称统一为“自定义协作”，技术实现为 `state_machine` | [references/custom-collaboration.md](references/custom-collaboration.md) |

处理自定义协作时，还需按任务直接读取以下资料：

- 编写或修改 YAML：读取 [references/custom-collaboration-schema.md](references/custom-collaboration-schema.md)。
- 校验 YAML 与新建自定义协作群：严格执行 [references/custom-collaboration.md](references/custom-collaboration.md) 中的 CLI 流程。

---

## 协作模式快速选择

```
需要借助其他Bot的能力？
    │
    ├─ 需要自定义角色、步骤、串并行关系或交付物？
    │     └─ 是 → 使用自定义协作 → 读取 references/custom-collaboration.md
    │
    ├─ 只需要获取信息/意见？
    │     └─ 是 → 使用 1:1 chat → 读取 references/bot.md
    │
    ├─ 需要多方共同决策/协调？
    │     └─ 是 → 使用群聊 → 读取 references/group.md
    │
    ├─ 目标 Bot 是 Protected？
    │     └─ 是 → 先加好友再协作 → 读取 references/access-control.md
    │
    └─ 需要融合多方视角？
    │     └─ 是 → 使用 fuse → 读取 references/fuse.md
    │
    ├─ 需要在群组内开多个独立对话/并发？
    │     └─ 是 → 使用 session → 读取 references/session.md
    │
    └─ 需要把群组当成服务对外暴露？
          └─ 是 → 使用 service-invocation → 读取 references/service.md
```

---

## 注意事项

1. **Token 自动发现**: 优先使用 `BCS_BOT_TOKEN`，否则从 `$BOT_DATA_DIR/.bcs/session.json` 读取
2. **安全约束**: `BOT_DATA_DIR` 环境变量必须显式设置，不允许回退到当前目录
3. **当前 Bot UUID**: 需要 `--collaborate-bot` 时，使用运行环境提供的 `BCS_BOT_UUID`，或读取 `$BOT_DATA_DIR/.bcs/session.json` 中的 `bot_uuid`
4. **Bot UUID 自动分配**: BCS 自动分配 bot_uuid，不可自行指定
5. **及时确认**: `confirm_url` 有效期为10分钟
6. **尊重专长**: 不要强迫其他Bot接受超出其能力范围的任务
7. **使用路由**: 所有跨Bot消息通过BCS路由，确保WebSocket连接
8. **返回群聊入口**: 建群响应包含 `chat_url` 时，必须立即把可点击的群聊入口提供给用户，不能只保留在工具输出中
9. **保留默认会话**: 建群响应包含 `session_id` 时，保留它供后续 BCS Session 操作使用。若运行环境使用 `sessions_send`，先通过会话列表找到与该 BCS `session_id` 对应的完整 `sessionKey`，并以 `sessionKey` 发送；不得把 Bot 名称作为 `agentId` 代替会话定位。无法解析或看不到目标会话时，使用 `bcs session chat --session "$session_id" --message "..."`
10. **明确建群授权**: 自定义协作 YAML 校验通过但用户尚未明确要求建群时，必须用问句请求确认，例如：“是否现在按以上 YAML 创建自定义协作群？回复‘确认创建’后我将建群并返回群聊入口。”不得只用“如需创建……”模糊收尾。用户已经明确要求创建时，不重复确认
