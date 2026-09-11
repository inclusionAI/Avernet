---
name: clawweb-verification
description: 在 AIStudio 中读取 ClawWeb 待验收改进项，使用 OpenClaw NAS 中修复后的新 Session、当前配置和完整执行轨迹判断问题是否消失，并回写 DISAPPEARED、STILL_PRESENT 或 INSUFFICIENT_DATA。也用于主动检查用户未回报的 ACTIVE/未回传完成的 IN_PROGRESS 改进项，在确认问题已修复后推进到 RESOLVED。用户要求运行 Verification Agent、关闭自动验收中的改进项、验收自动/手动修复、确认修复是否有效或将改进项推进到已完成时使用。不要用它创建治理候选、审批、修改 Bot 配置、伪造新 Session 数量或操作非开放验收范围的状态。
---

# ClawWeb Verification Agent

做两类验收：读取已明确进入验收的项目，或主动检查用户已经修复但没有回到 ClawWeb 更新状态的开放项目；寻找修复边界之后的新 Session，对照原始根因判断是否复现，再写回结果。

## 运行环境

- AIStudio 运行时为 **Python 3.12**；脚本仅使用 Python 标准库，不依赖 Node.js。
- Skill 根目录：`/ossfs/workspace/jiangshen_skills`。
- ClawWeb 的 Verification 读写接口不需要 Agent Secret、Cookie 或认证头。

### NAS 路径（验收证据来源）

Bot 当前配置和修复后新 Session 在以下只读 NAS：

```text
/home/admin/.bot_shared_nas/arca/arcaagentclaw/prod       # ~7000 bot
/home/admin/.bot_shared_nas_2/arca/arcaagentclaw/prod     # ~3000 bot
```

定位脚本：`bash /ossfs/workspace/jiangshen_skills/openclaw-nas-locator/locate_bot.sh USER_ID BOT_ID`

Bot 目录格式：`prod_staff_<USER_ID>_openclaw_<BOT_ID>`（含 `_DEVICE-*` 和 `claude_code` 变体）

### ODPS 补充验证

大规模计数或跨 Session 统计可用 PyODPS（AIStudio 自动注入）：

```python
from pypai.utils import env_utils
odps = env_utils.get_odps_instance()
```

离线数据有水位延迟，最终验收优先使用 NAS 当前数据。

### 日志

`$kb-log-query` 只在修复目标是 ClawWeb API、审批、OSS、通知或状态机时使用。

## 不可违反的边界

- 标准验收读取 `GET /internal/governance/verification-candidates`；主动验收读取 `GET /internal/governance/verification-candidates/open` 返回的项目。
- 标准验收目标必须是 `IN_PROGRESS` 且 `handledAt` 非空；主动验收目标必须来自 `GET /internal/governance/verification-candidates/open`，只能是 `ACTIVE` 或 `IN_PROGRESS` 且尚未回传 `handledAt`。
- 标准验收只有在 `handledAt` 已经过 **2 天**后才允许确认问题消失；主动验收只有在开放项目的修复观察边界已经过 **7 天**后才允许确认问题消失。
- 标准验收只向普通结果接口发送 `improvementId`、`version`、`outcome`、`newSessionCount` 和可选 `lastRecurrenceAt`；主动验收只向 `/verification-results/open` 发送同一组结果字段。
- 不直接设置 `status`，不修改 Owner、Bot、审批、修复方式或配置。
- `DISAPPEARED` 必须至少检查一个真实的修复后新 Session；不得为了关单伪造数量。
- 没有新 Session、离线水位未到或证据不完整时使用 `INSUFFICIENT_DATA`。
- 同一根因再次出现时使用 `STILL_PRESENT`，表示验收未通过；标准项目保持 `IN_PROGRESS`，主动项目保持原来的 `ACTIVE/IN_PROGRESS` 状态。
- 主动验收的 `ACTIVE` 项如果存在仍在运行的 Evolve Task，必须等待任务结束，不得把执行中的任务误判为用户已修复；如果问题仍存在，不要为了改变状态而提交 `DISAPPEARED`。
- 写回前重新 list，使用最新 `version`；`409` 后必须重新读取，不能盲目重试。

## 核心流程

### A. 标准验收：修复后的改进项

1. **列出待验收项**：调用 `scripts/verification.py list --json`。
2. **锁定目标**：记录 `improvementId`、`botId`、`ownerUserId`、`handledAt`、原始 Evidence 和最新 `version`。
3. **定位 Bot NAS**：调用 `$openclaw-nas-locator`，覆盖两个 NAS 根、`_DEVICE-*` 变体和所有 Agent。
4. **枚举新 Session**：使用 `scripts/find_new_sessions.py` 查找 `handledAt` 之后的普通 Session JSONL；排除 trajectory 文件。
5. **筛选相关会话**：优先检查同一任务类型、组件、Skill、MCP、工作流或错误路径的新 Session，不要把无关成功 Session 当作验收证据。
6. **读取小型证据**：原始文件先检查大小，只提取目标 Task、相关工具调用、最终结果和错误，不展开整份 Session。
7. **检查当前配置**：确认修复计划中的文件、Skill、MCP 或工作流配置确实已更新；配置更新本身不能代替运行结果。
8. **按 [references/decision-policy.md](references/decision-policy.md) 判定结果**。
9. **重新 list 获取最新版本**，确认从 `handledAt` 起已观察满 2 天，然后用 `scripts/verification.py submit` 回写。
10. **核对终态**：通过时必须返回 `RESOLVED/VERIFIED/AUTO_VERIFIED`；未通过或数据不足时保持 `IN_PROGRESS`。如果响应包含 `ruleEvolutionProposal`，只记录候选 ID 和统计信息，不在 Verification Agent 中批准或发布规则。

### B. 主动验收：用户已修复但没有回传状态

用户可能已经直接修改了 Bot 配置或 Skill，但没有点击“已手动修复”，导致改进项仍停留在 `ACTIVE`，或处于 `IN_PROGRESS` 但没有 `handledAt`。Verification Agent 可以主动检查这些开放项目，但必须先确认实际修复边界，不能仅凭项目创建时间或用户没有报错就关单。

1. **列出开放项目**：调用 `scripts/verification.py list-open --json`。
2. **筛选可检查项目**：跳过 `PENDING_ADMIN`、`ARCHIVED`、`RESOLVED`，以及仍有运行中 Evolve Task 的项目；`latestEvolveTaskId/latestEvolveTaskStatus` 仅用于判断是否应等待，不可作为修复成功证据。
3. **确定检查边界**：优先使用项目的 `gmtModified` 作为主动验收起点；若能从 NAS 或 Evolve 记录确认实际修改完成时间，使用更晚且更准确的时间。主动验收必须从该边界连续观察满 7 天；不要把原始失败发生时间当成修复边界。
4. **检查真实证据**：定位目标 Bot 的 NAS，核对修复目标是否已经落盘，再检查边界之后与同一根因相关的新 Session；必要时用 ODPS 交叉确认。只要仍复现同一根因，就不能关闭。
5. **回写结果**：确认问题消失且观察已满 7 天时使用 `scripts/verification.py submit-open --outcome DISAPPEARED`；发现复现时使用 `submit-open --outcome STILL_PRESENT`，证据不足时使用 `INSUFFICIENT_DATA`。主动结果接口只允许操作开放项目，不会修改 Owner 或审批信息。
6. **核对终态**：成功后项目进入 `RESOLVED/VERIFIED/AUTO_VERIFIED`。如果返回 `ruleEvolutionProposal`，只记录结果，仍由 ClawWeb Admin 审核规则进化建议。

### C. 主动验收：检查已标记完成的改进项是否真的修复

用户手动标记"已完成"的改进项，clawweb-verification 应定期主动复查，确认问题是否真的消失。有了 NAS 可以直接看对方的聊天记录和完整环境。

1. **列出已完成的项**：调用 `scripts/verification.py list-resolved --since "30d ago"`。
2. **逐个检查**：对每个 RESOLVED/VERIFIED/AUTO_VERIFIED 项：
   a. 定位 Bot NAS 目录（两个根 + `_DEVICE-*` 变体）
   b. 查找 `resolvedAt` 之后的**相关新 Session**（同类任务、同 Skill/MCP、同 failure path）
   c. 读取新 Session 中目标 Task 的工具调用结果、错误和最终回复
   d. **同时查 ODPS full_message**：`asec_aigame.dws_sec_log_teamclaw_arca_openclaw_session_full_di` 中该 Bot 在 resolvedAt 之后的 Session，确认没有同类失败
3. **判定**：
   - 新 Session 无同类根因且当前配置已更新 → **确实已修复**，保持 RESOLVED
   - 新 Session 出现同一根因 → **假修复**，应标记 `STILL_PRESENT` + `lastRecurrenceAt`
   - 没有新 Session 且 NAS 配置未变化 → **可能未实际修理**，标记 `INSUFFICIENT_DATA`
4. **回写**: `scripts/verification.py submit --outcome STILL_PRESENT --last-recurrence-at "..."`（仅当发现复现时）

## 常用命令

列出待验收项（标准流程）：

```bash
export CLAWWEB_URL="https://clawweb.alipay.com"
python3 /ossfs/workspace/jiangshen_skills/clawweb-verification/scripts/verification.py list \
  --limit 100 \
  --json
```

列出用户可能已修复但尚未回传状态的开放项：

```bash
python3 /ossfs/workspace/jiangshen_skills/clawweb-verification/scripts/verification.py list-open \
  --limit 100 \
  --json
```

列出已完成的改进项（主动验收）：

```bash
python3 /ossfs/workspace/jiangshen_skills/clawweb-verification/scripts/verification.py list-resolved \
  --limit 100 \
  --since "2026-07-20T00:00:00+08:00" \
  --json
```

定位新 Session：

```bash
python3 /ossfs/workspace/jiangshen_skills/clawweb-verification/scripts/find_new_sessions.py \
  --user-id USER_ID \
  --bot-id BOT_ID \
  --since "2026-08-18T17:05:26+08:00" \
  --limit 100
```

验收通过：

```bash
python3 /ossfs/workspace/jiangshen_skills/clawweb-verification/scripts/verification.py submit \
  --improvement-id ID \
  --version VERSION \
  --outcome DISAPPEARED \
  --new-session-count REAL_COUNT
```

验收未通过：

```bash
python3 /ossfs/workspace/jiangshen_skills/clawweb-verification/scripts/verification.py submit \
  --improvement-id ID \
  --version VERSION \
  --outcome STILL_PRESENT \
  --new-session-count REAL_COUNT \
  --last-recurrence-at ISO_TIME
```

证据不足：

```bash
python3 /ossfs/workspace/jiangshen_skills/clawweb-verification/scripts/verification.py submit \
  --improvement-id ID \
  --version VERSION \
  --outcome INSUFFICIENT_DATA \
  --new-session-count 0
```

主动验收通过并关闭开放项：

```bash
python3 /ossfs/workspace/jiangshen_skills/clawweb-verification/scripts/verification.py submit-open \
  --improvement-id ID \
  --version VERSION \
  --outcome DISAPPEARED \
  --new-session-count REAL_COUNT
```

主动验收发现问题仍存在：

```bash
python3 /ossfs/workspace/jiangshen_skills/clawweb-verification/scripts/verification.py submit-open \
  --improvement-id ID \
  --version VERSION \
  --outcome STILL_PRESENT \
  --new-session-count REAL_COUNT \
  --last-recurrence-at ISO_TIME
```

## 成功标准

- 标准验收只统计 `handledAt` 之后真实存在的相关新 Session，并且确认消失前至少经过 2 天；主动验收使用确认过的修复边界，并且确认消失前至少经过 7 天，边界至少不能早于项目 `gmtModified`。
- 比较的是规范化根因，不是“是否出现任何失败”。
- 通过结论同时具有当前配置已应用和真实运行未复现两类证据。
- 未通过时指出复现的 Session、Task、组件和错误摘要。
- 最终回写严格符合 [references/api.md](references/api.md)。
- 规则进化候选只能由 ClawWeb 在达到成功验收门槛后生成，Verification Agent 不直接修改规则 JSON。
