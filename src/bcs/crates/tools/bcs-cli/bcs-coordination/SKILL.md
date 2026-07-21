---
name: bcs-coordination
description: 全场景多智能体协同和交互引擎。覆盖多 Bot 复杂任务协同与沉浸式娱乐互动。通过提供注册发现、群组构建、上下文融合及路由通信能力等核心能力，支持能力互补、信息和知识的融合、冲突消解、工作流编排，以及 2C 场景下多人游戏互动等。
allowed-tools:
  - exec
---

# BCS 多智能体协同和交互引擎 (Bot Coordination Service)

## 🎯 核心目标

本技能是处理**所有多 Bot 协同场景**的唯一入口。当遇到以下任一特征时，必须调用：

### 🏢 B2B / 生产力场景

- **能力边界突破**：需引入外部专长（代码、法律、数据）。
- **信息/视角补全**：打破信息孤岛，融合多方数据。
- **权限/资源隔离**：跨系统、跨角色的代理操作。
- **冲突与共识**：多方利益/观点不一致，需仲裁对齐。
- **复杂流程编排**：串行/并行的自动化工作流。

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
    BOT_DATA_DIR="$BOT_DATA_DIR" bcs-cli --url "$BCS_API_BASE_URL" --token "$BCS_BOT_TOKEN" "$@"
  else
    BOT_DATA_DIR="$BOT_DATA_DIR" bcs-cli --url "$BCS_API_BASE_URL" "$@"
  fi
}
```

### Token 自动发现

本技能会按以下顺序查找 token：

1. `BCS_BOT_TOKEN` 环境变量：由上面的 `bcs` 包装函数转成显式 `--token`
2. `$BOT_DATA_DIR/.bcs/session.json`：由 `bcs connect` 或 WebSocket channel 写入
3. `--token` 显式参数：需要手动覆盖时，直接运行完整的 `bcs-cli --url "$BCS_API_BASE_URL" --token "<token>" ...`

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
| session-file | 会话工作区文件上传/下载/分享/列/删 | [references/session-file.md](references/session-file.md) |
| service | 把 Group 当成服务对外暴露，带鉴权和 callback                    | [references/service.md](references/service.md) |

---

## 协作模式快速选择

```
需要借助其他Bot的能力？
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
    ├─ 需要在群组内共享文件？
    │     └─ 是 → 使用 session file → 读取 references/session-file.md
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
8. **会话文件直传后端**: `session file upload` 对 presign 后端（baas/OSS）要求本机/进程网络可达 OSS；仅能连 BCS 的环境用 local 后端。跨主机 PUT 到后端 OSS URL 时 Bearer 不应发送（OSS 预签名 URL 自鉴权），`bcs` CLI 已处理；自定义客户端需注意。
