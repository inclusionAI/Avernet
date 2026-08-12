# 小店天团的 Fuse 多视角复核

本页是「一家小店的背后，站着一个天团」系列的操作附录。主流程（店主提目标 → 店长组织平台三专家 → 生成 SOP → 店主 HumanInput 验收）保持不变；这里演示如何在 SOP 出来后，用 BCSFuse 的**右侧浮窗融合模式**做一次多视角复核。

> 目标读者：已经跑通小店天团主流程，想让 demo 再多一层「AI 专家会诊」效果的开发者。  
> 预计耗时：5 分钟（不含环境准备）。

---

## 1. Fuse 能做什么

BCSFuse 是 Avernet 的多视角融合服务。它基于**当前群聊上下文**和**参与 Bot 的公开画像**（SOUL、MEMORY、TOOLS、AGENTS 等），让多个 Bot 对同一个问题给出各自视角，再融合成一份结构化建议。

在同一个 `/api/v1/groups/{group_id}/fuse` 接口上，可以通过 `fusion_mode` 切换不同能力：

| 模式 | fusion_mode | 侧重点 | 本次 demo 用途 |
|---|---|---|---|
| G1 | `agent` | 基于群聊消息的通用多视角融合，输出各 Bot 观点 + 综合建议。 | 快速了解群里多方意见的汇总。 |
| G2 | `conflict_alignment` | 在 G1 基础上增加**冲突/对齐点**和**关键洞察**识别。 | 当各 Bot 意见明显冲突，想看清分歧维度时使用。 |
| G5 | `expert_diagnosis` | 专家会诊模式，输出风险评估、关键问题、专家建议、上线条件。 | 适合把 SOP 当作一个整体对象做风险会诊。 |
| G9 | `bot_profile_fuse` | 深度读取 Bot **公开画像**后生成视角，最能体现不同身份的专业立场差异。 | **本次主打的复核方式**：让 4 个经营 Bot 从自身画像出发 review SOP。 |

> 当前前端右侧浮窗默认使用 **G9 `bot_profile_fuse`**，后面的示例也按 G9 讲解。

Fuse 接口的核心输入输出：

- **输入**
  - `question`：本轮要问的问题（可以附 SOP 摘要）。
  - `participants`：参与融合的 Bot UUID 列表。
  - `driver_bot_id`：发起者 Bot UUID。
  - `fusion_mode`：`agent`、`conflict_alignment`、`expert_diagnosis`、`bot_profile_fuse` 四选一。
  - `options.timeout_ms`：超时时间，建议 `180000`（3 分钟）。
- **输出**
  - `perspectives`：每个参与 Bot 的视角，含 `summary`、`confidence`、`key_points`、`concerns`、`evidence`。
  - `recommendation`：融合后的综合建议，含 `summary`、`decision`（`yes/no/conditional_yes/needs_more_information`）、`reasoning`、`risks`、`missing_information`、`next_actions`、`confidence`。

---

## 2. 什么时候在小店天团里用

建议在主流程的**一次性自定义协作运行结束、页面进入 HumanInput 等待店主验收**时打开浮窗：

```
店主提目标
    ↓
店长 → 平台三专家协商
    ↓
生成一次性自定义协作 → 三方复核
    ↓
页面停在 HumanInput（等待店主验收）
    ↓
**店主打开右侧「融合模式」，做一次最终 fuse 复核**
    ↓
店主参考 fuse 结论，在 HumanInput 中选择「接受」或「要求修改」
```

Fuse 结论**不自动写回群聊**，只作为店主本人的参考浮窗；最终决策权仍然在 HumanInput 里的店主手上。

---

## 3. 前置条件

确保已启动 BCS、前端、bcsfuse 和 4 个经营 Bot：

```bash
export BOTS_PROFILE_DIR=scripts/4bots_merchant_operations_profile
./scripts/singlebox.sh --standalone start bcs_frontend
./scripts/singlebox.sh --standalone start bcsfuse
./scripts/singlebox.sh --standalone start bots --profile-dir "$BOTS_PROFILE_DIR"
```

> 需要先导出 `BOTS_PROFILE_DIR`：BCS 启动时会把它作为 `bots_base_dir` 写进运行时配置，这样后续 bots onboard 时 bcs-fusion 才能找到 `IDENTITY.md`、`SOUL.md` 等 profile 文件并同步给 bcsfuse。

> 从本改动开始，`singlebox` 在 bots 启动成功并 onboard 后，会自动为当前 profile 的 4 个 Bot 开启 `fusion_enable`。启动日志里应能看到 `Profile fusion enabled for ...`。

检查 fuse 是否就绪：

```bash
./scripts/singlebox.sh --standalone status bots --profile-dir scripts/4bots_merchant_operations_profile
```

4 个 Bot 都应为 `Running` 状态。然后进入前端：

[http://127.0.0.1:8000/bcn/chat/list](http://127.0.0.1:8000/bcn/chat/list)

切换到**人类店主视角**，进入已经跑完主流程的店庆任务协作群。

---

## 4. 使用步骤

1. **打开右侧浮窗**

   在群聊右下角点击「融合模式」按钮。如果 4 个 Bot 的 `fusion_enable` 已开启，浮窗中会显示可选 Bot 列表。

2. **确认参与 Bot**

   默认勾选当前群内已开启画像融合的 Bot。小店天团应包含：

   - 店长日常运营
   - 平台营销方案
   - 平台数据分析
   - 平台供应链

3. **输入复核问题**

   把当前 SOP 的关键信息作为问题背景，示例：

   ```text
   当前这份 18 周年庆 SOP 已经过平台营销、数据、供应链和店长的多轮协商。请各位从各自专业视角再评估一次：
   1) 这份 SOP 是否完整可执行？
   2) 营销、数据、供应三个专业判断之间有没有互相冲突的假设？
   3) 对店主来说最大的三个风险是什么？
   4) 给出 go / no-go / 带条件 go 的建议，并列出必须确认的下一步。
   ```

4. **等待并阅读结果**

   G9 模式会做三次模型调用（群聊上下文总结 → Profile 融合 → Prompt 构建与回答生成），一次完整调用通常需要数十秒到数分钟，取决于模型速度和网络。

---

## 5. 看懂返回结果

浮窗里返回的 Markdown 内容来自 `recommendation.summary`。G9 `bot_profile_fuse` 会把四个 Bot 的 Profile 融合成一个「超级专家」后再回答，因此当前实现下 `perspectives` 字段通常为 `[]`；各角色的立场差异会体现在 `recommendation.summary` 的论证里。

### 5.1 融合里隐含的多视角

虽然返回结构里没有逐条列出每个 Bot 的 perspective，但 G9 在生成 `recommendation` 时已经读过四个 Bot 的 SOUL / MEMORY / TOOLS，所以你会看到类似这样的立场交叉：

- **店长日常运营**：关注目标完整性、owner 是否明确、是否与私有约束冲突。
- **平台营销方案**：关注券结构是否支持新老客目标、补贴边界和宣传合规。
- **平台数据分析**：关注客流/转化假设的数据口径和产能支撑。
- **平台供应链**：关注护理耗材库存、交期、Plan B 和品质证据。

> 这些视角差异正是 Fuse 的价值所在：不是让同一个大模型答四遍，而是让四个不同画像在回答里共同发声。如果需要逐条 perspective，可改用 G1/G2 模式。

### 5.2 recommendation（综合建议）

关键字段含义：

| 字段 | 含义 |
|---|---|
| `summary` | 综合结论的 Markdown 摘要。 |
| `decision` | `yes` / `no` / `conditional_yes` / `needs_more_information`。 |
| `reasoning` | 支持该决策的 2–5 条理由。 |
| `risks` | 识别的 2–5 条风险点。 |
| `missing_information` | 还需要补齐的信息（如果有）。 |
| `next_actions` | 2–5 条可执行的下一步。 |
| `confidence` | 0–1 之间的置信度，通常保守给出。 |

如果 `decision` 是 `conditional_yes` 或 `needs_more_information`，店主应先把这些条件/信息补全，再回到 HumanInput 完成验收。

---

## 6. 结果怎么用

Fuse 结论目前**只停留在浮窗**，不会自动发到群聊里。推荐用法：

1. 店主阅读 `summary`、风险点和 `missing_information`。
2. 如果 `summary` 里提到某一方存在保留意见或冲突，回到群聊中让对应 Bot 再澄清一次。
3. 确认无重大问题后，在 HumanInput 面板中选择「接受当前版本」。
4. 如果需要修改，在 HumanInput 中说明要调整的公开条款，触发下一轮复核。

---

## 7. 想用 curl 手动触发

前端浮窗底层调用的是 `POST /bcnfuse/api/v1/groups/{group_id}/fuse`。你也可以直接 curl：

```bash
GROUP_ID="你的群 group_id"
DRIVER="店长日常运营的 bot_uuid"
B1="平台营销方案的 bot_uuid"
B2="平台数据分析的 bot_uuid"
B3="平台供应链的 bot_uuid"

curl -s -X POST "http://127.0.0.1:8765/api/v1/groups/${GROUP_ID}/fuse" \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d "{
    \"question\": \"当前这份18周年庆SOP是否完整可执行？营销、数据、供应三个视角是否存在冲突假设？请给出go/no-go/带条件go的建议。\",
    \"participants\": [\"${B1}\",\"${B2}\",\"${B3}\",\"${DRIVER}\"],
    \"driver_bot_id\": \"${DRIVER}\",
    \"fusion_mode\": \"bot_profile_fuse\",
    \"options\": {\"timeout_ms\": 180000}
  }" | jq .
```

如果想试试其它模式，把 `fusion_mode` 换成 `agent`、`conflict_alignment` 或 `expert_diagnosis` 即可。

---

## 8. 常见问题

### Q1：浮窗提示「协作群内无 Bot 公开画像，融合模式暂不可用」

1. 确认 bcsfuse 已启动：`./scripts/singlebox.sh --standalone status`。
2. 确认 bots 启动日志里有 `Profile fusion enabled for ...`。
3. 如果仍不可用，手动检查并开启：
   ```bash
   BCSFUSE_URL="http://127.0.0.1:8765"
   BCSFUSE_AUTH_TOKEN="dev-opencore-token"
   for bot_uuid in bot_xxxx1 bot_xxxx2 bot_xxxx3 bot_xxxx4; do
     curl -s -X PUT "$BCSFUSE_URL/v1/workers/$bot_uuid/config" \
       -H "Authorization: Bearer $BCSFUSE_AUTH_TOKEN" \
       -H "Content-Type: application/json" \
       -d '{"fusion_enable":true}' | jq .
   done
   ```

### Q2：fuse 返回内容很空、被截断或报「未提供 SOP 草案」

1. 确认使用了真实 LLM，而不是 mock。
2. 查看 `.dependencies/standalone/bcsfuse/logs/bcsfuse.log`：
   - 若出现 `Request URL is missing an 'http://' or 'https://' protocol`，说明 `BCN_BASE_URL` 未设置，bcsfuse 没有拿到群聊上下文。新版 singlebox 启动 bcsfuse 时会自动设置该变量；手动重启时请带上 `BCN_BASE_URL=http://127.0.0.1:21000`。
   - 若出现 `无会话历史` / `context_messages_count=0`，可能是 SOP 消息太久远或单条消息超过 500 字被截断。
   - 若答案明显到一半就断了，check token 用量是否顶到 `max_tokens`，默认已从 4096 提到 8192（通过 `FUSION_CHAT_MAX_TOKENS`）。
3. **最稳妥做法**：直接把 SOP 原文贴进 fuse 问题里，不要只写「参考当前群聊里的 SOP」。

### Q3：G9 与 G1/G2/G5 怎么选

- 日常快速汇总：G1。
- 想看清多方冲突：G2。
- 把方案当整体做风险会诊：G5。
- **想展示不同 Bot 画像带来的立场差异**：G9（本次推荐）。

---

## 9. 本附录改动了什么

1. `scripts/modules/bots.sh`：bots 启动成功后自动为当前 profile 的 Bot 开启 `fusion_enable`。
2. `scripts/4bots_merchant_operations_profile/*/SOUL.md`：在每个角色 SOUL 中补充了「评估复杂方案时的本能张力」，让 G9 融合出来的 perspective 差异更明显。
3. 本文档：说明 G9 浮窗在小店天团中的接入方式和接口能力。
