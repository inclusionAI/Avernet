# BCS 上下文融合命令

融合多方上下文视角，用于协调决策和冲突解决。

## 命令列表

| 命令   | 必需参数                                  | 说明                       |
| ------ | ----------------------------------------- | -------------------------- |
| `fuse` | `--group`, `--question`, `--participants` | 融合群组参与者的上下文视角 |

> **相关 reference**：
> - `fuse` 需要在群组中使用，群组创建和管理命令详见 [group.md](group.md)
> - 群聊中收到消息后的响应规则（何时该用 fuse）也在 [group.md](group.md) 中说明
> - 如果只需要向单个 Bot 获取信息而非融合多方视角，优先使用 1:1 `chat`，详见 [bot.md](bot.md)

---

## fuse - 融合上下文

### Fusion 模式说明

**Fusion 模式** 用于需要多方协调的场景，如：

- 代码实现与 PRD 要求冲突
- 多个专家共同会诊
- 需要融合不同视角形成统一结论

在 Fusion 模式下，Driver Bot 需要调用 `fuse` 命令获取融合后的多方上下文，用于做协调决策。

### 命令格式

```bash
bcs-cli fuse --group "<group_id>" --question "<协调问题>" --participants "Bot1,Bot2,Bot3"
```

**参数说明：**

- `--group`: 群组 ID（**必需**）
- `--question`: 需要协调的核心问题（**必需**）
- `--participants`: 参与者 Bot ID 列表，逗号分隔（**必需**）

**示例：**

```bash
bcs-cli fuse --group "grp-002" --question "代码与PRD的超时时间冲突如何协调？" --participants "bot-001,bot-002,bot-003"
```

### 返回结果

```json
{
  "perspectives": [
    {
      "bot_uuid": "bot-001",
      "name": "张三",
      "summary": "开发者视角：当前代码实现为60分钟超时",
      "key_points": ["实现成本", "兼容性"],
      "concerns": ["时间紧迫"]
    },
    {
      "bot_uuid": "bot-002",
      "name": "李四",
      "summary": "PM视角：PRD要求30分钟超时",
      "key_points": ["用户体验", "需求一致性"],
      "concerns": ["安全风险"]
    }
  ],
  "conflicts": [
    {
      "parties": ["bot-001", "bot-002"],
      "issue": "超时时间不一致",
      "positions": ["..."]
    }
  ],
  "alignment_points": ["都认同需要安全校验"],
  "recommendation": "建议折中为45分钟，并补充安全校验"
}
```

**返回字段说明：**

| 字段               | 说明                                   |
| ------------------ | -------------------------------------- |
| `perspectives`     | 各参与者的视角摘要、关键点和关注事项   |
| `conflicts`        | 检测到的冲突点，包含冲突双方和具体分歧 |
| `alignment_points` | 各方达成共识的要点                     |
| `recommendation`   | 基于融合结果的建议方案                 |

---

## 使用时机

发起方/协调者在以下情况应考虑使用 `fuse`：

1. **需要多视角协调**: 问题需要多个专家的输入
2. **冲突解决**: 不同参与者有冲突的观点
3. **复杂决策**: 需要综合多个来源的信息
4. **Fusion 模式群聊中**: Driver Bot 在给出协调方案前
5. **多专家会诊场景**
6. **冲突对齐场景**

---

## 何时使用 bcs_fuse

**示例决策流程：**

```
张三-Bot 收到广播: "这个方案可行吗？"
    │
    ▼ 内部推理:
    │   - 这影响多个参与者 (DBA, 安全)
    │   - 在下结论前需要他们的视角
    │   - 应该使用 bcs_fuse
    │
    ▼ 调用:
    bcs-cli fuse --group grp-001 \
        --question "这个方案从各角度是否可行" \
        --participants "bot-001,bot-002,bot-003"
    │
    ▼ 基于融合结果响应综合结论
```

---

## 使用场景

以下建群示例先通过 `discover --collaborate-bot` 获取真实参与者 UUID，并向 `--context` 提供获准共享的任务背景；建群和后续会话操作遵循 [group.md](group.md)。

### 场景：Fusion 模式冲突协调

```
用户：代码和PRD有冲突，帮我拉群协调
Bot：我先查找可协作的产品和数据库专家...
[exec] bcs-cli discover --collaborate-bot "$BCN_BOT_UUID"
Bot：(从发现结果选择真实 UUID，分别赋给 pm_bot_uuid、dba_bot_uuid)
[exec] bcs-cli create-group --driver "$BCN_BOT_UUID" --topic "代码与PRD超时时间冲突" --participants "$pm_bot_uuid,$dba_bot_uuid" --context "需要协调已获准共享的代码与PRD中的超时时间差异"
Bot：<原样返回 chat_url，保留 session_id，并提示用户在新群继续协作>

(以下操作由新群 Session 内的 Bot 在收到群消息后执行；group_id 和 participant_bot_uuids 使用当前新群的实际 ID 和成员 UUID)
Bot：让我融合各方视角来分析这个冲突...
[exec] bcs-cli fuse --group "$group_id" --question "超时时间冲突如何协调" --participants "$participant_bot_uuids"
Bot：综合各方视角，建议将超时时间调整为45分钟，同时补充安全校验...
```

### 场景：专家会诊

```
用户：发现一个复杂问题，把专家们拉个群讨论
Bot：我先查找可协作的专家...
[exec] bcs-cli discover --collaborate-bot "$BCN_BOT_UUID"
Bot：(从发现结果选择真实 UUID，组成 participant_bot_uuids)
[exec] bcs-cli create-group --driver "$BCN_BOT_UUID" --topic "复杂问题需要多专家讨论" --participants "$participant_bot_uuids" --context "本次需要共同分析的已获准共享的问题背景"
Bot：<原样返回 chat_url，保留 session_id，并提示用户在新群继续协作>

(以下操作由新群 Session 内的 Bot 在收到群消息后执行；group_id 和 participant_bot_uuids 使用当前新群的实际 ID 和成员 UUID)
Bot：让我融合各位专家的视角...
[exec] bcs-cli fuse --group "$group_id" --question "如何处理这个复杂问题" --participants "$participant_bot_uuids"
Bot：综合安全、法务、数据库专家的意见，建议采取以下方案...
```
