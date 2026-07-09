# Bot 治理通知 + 审计

## Summary

在 OCB 后端 `economy` 模块下新增治理子模块 `governance`（负向治理），每日定时读取 OceanBase
中 ODPS 同步的治理分析数据，对 actionable 决策的 Bot Owner 发送 DingTalk 通知，收集
用户 4 种反馈（已优化/需时间/不认可/申请加白），并记录完整审计链路。`economy` 与 `harness`
并列，后续还将接入 `incentive`（正向激励）子模块。通知分发采用单阶段发送（Phase 1 在扫描锁内直接发送 pending 通知），审计链路完整。

## Motivation

OCB 平台已有离线治理管线 `economy_governance`，每天在 ODPS 上运行 LLM 深度分析，产出
Bot 维度的治理建议（5 治理维度 + 标准化决策 actionable/observe/justified）。这些数据
同步到 OceanBase 后，目前没有任何在线系统消费。Bot Owner 对治理判定无感知，也无法反馈
——治理闭环断裂。

本 Feature 接通离线分析 → 在线通知 → 用户反馈 → 审计记录的全链路，使治理从"只分析不
执行"变为"可感知可互动"。

## 分期策略

当前方案涉及通知闭环、离线 batch upsert、DingTalk 交互卡片、白名单、审计、自动关闭、
数据刷新等多个状态机。一次性实施容易在幂等边界出问题。采用分期收敛：

| | Phase 1 | Phase 2 | Phase 3 |
|---|---|---|---|
| **通知通道** | Markdown 单向通知（sampleMarkdown）**或** TC 卡片通知（createAndDeliver），两者可互换 | DingTalk 交互卡片（HTTP 回调按钮）| 不变 |
| **反馈入口** | HTTP API only（前端页面 resolve） | + 卡片回调双入口 | 不变 |
| **状态机** | open/muted/closed/expired + response + close_reason | + card_callback 来源 | + data_refreshed |
| **自动关闭** | ✅ 不在治理范围连续N天 / observe / justified | 不变 | 不变 |
| **数据刷新** | ❌ 不做 | 不做 | ✅ latest_dt 刷新 |
| **再通知** | ❌ 不做 | 不做 | ✅ 策略化再通知 |

**Phase 1 设计原则**：
- **通知通道支持两种模式，可互换**：
  - **Markdown 简单通知**（默认）：DingTalk `batchSend` (sampleMarkdown) 发送单向通知，
    消息内嵌 OCB 前端治理详情页深链接，用户点击链接跳转前端页面进行反馈。
  - **TC 卡片通知**（升级版）：DingTalk `createAndDeliver` 发送卡片壳通知，
    卡片壳内由 DDRichTextView 渲染 Markdown 格式的 reason 内容（标题、指标、摘要、建议），
    底部"立即查看"链接打开 teamclaw preview iframe 渲染完整 React 组件
    （LLMComponent_v2.jsx），用户在 iframe 内查看详情并提交反馈。
    卡片壳标签名由模板 `cardName` 决定（如"成本优化通知"），不再显示"HITL"。
  - **互换机制**：通过 `GovernanceConfig.notify_channel` 配置项切换
   （`markdown` / `tc_card`）。TC 卡片模式需要额外的 `card_template_id` 配置
    和 `app_key` / `app_secret` 凭证，凭证未配置时自动降级为 Markdown 模式。
    两种模式的 `notification_structured` 数据结构和反馈 API 完全一致，仅发送通道不同。
  - 交互卡片（含 HTTP 回调按钮的完整交互卡片，如复选框 + 4 按钮）推迟到 Phase 2。
- 通知创建后内容冻结，状态可追踪。`governance_status` 仅 4 态
  （`open` / `muted` / `closed` / `expired`），业务语义通过 `response` + `close_reason` + `audit`
  表达，不塞进主状态。扫描只处理 `open` 和 `muted`。不做数据刷新，不做再通知。

本文档描述 Phase 1 完整规格。Phase 2 和 Phase 3 在文末附录中列出增量差异。

## User Stories

- 作为 Bot Owner，我希望收到治理通知后能选择"已优化"，让系统知道我已经处理了，避免重复
  通知。
- 作为 Bot Owner，我希望选择"需时间"申请延期，让系统暂时不再催促。
- 作为 Bot Owner，如果我不认可治理判定，我希望能选择"不认可"并附上理由，让治理团队复核。
- 作为 Bot Owner，如果我的 Bot 有合理原因需要豁免治理，我希望能申请加白名单。
- 作为平台运营，我希望看到完整的审计记录（每次扫描、每条通知、每次反馈），以衡量治理
  效果和合规性。
- 作为 Bot Owner，我不希望收到已被加白的 Bot 的治理通知。

## Acceptance Criteria — Phase 1

### 扫描

- [ ] 每日 14:00 自动触发治理扫描（可配置时段）。
- [ ] 扫描使用分布式锁（`governance_scan_lock:{env}`），TTL 30 分钟，防止多 Pod 重复执行。
- [ ] **数据就绪检查**：扫描开始时读取 `MAX(ac_governance_task_record_daily.last_sync_at)`，
      与上次扫描成功时间比较。若 `last_sync_at` 未更新（即离线管线无新数据写入），
      则**禁止所有依赖离线数据新鲜度的操作**：
      - ❌ 不创建新通知（避免基于旧数据重复开单）
      - ❌ 不执行状态追踪（不更新 `latest_decision` / `consecutive_normal_days`）
      - ❌ 不执行静默期过滤中的 `last_seen_at` 更新
      - ✅ 继续执行时间驱动操作：提醒到期处理（`remind_at` 检查）、过期关闭、发送重试
      - 审计 `action_taken='data_not_ready'`，**不 return**——跳过通知创建和状态追踪后继续执行

      **原因**：通知创建和状态追踪都依赖离线数据新鲜度。如果 `last_sync_at` 未变，
      说明 task_record_daily 中的数据仍是上一批次的结果，用它来创建新通知或更新工单状态
      会导致基于旧数据重复开单或误判。
- [ ] 从 `ac_governance_task_record_daily` 读取最新 `dt_version` 分区中
      `governance_decision = 'actionable'` 且 `analysis_status` 在
      `('completed', 'success', 'success_with_warnings')` 的记录。
      该表包含全量字段（bot_id, bot_name, hit_dimensions, task_summary 等），
      扫描创建通知时只需查此表，不需要再 JOIN analysis_daily。
- [ ] 当无可用分区时（数据延迟），日志告警并跳过本次扫描，不创建空通知。
- [ ] `dry_run` 模式下，扫描逻辑完整执行但跳过通知写入，审计记录标记 `dry_run=1`。

### 通知创建

- [ ] 对每个 actionable Bot，按以下顺序检查后决定是否创建通知：
  1. **加白过滤**：`ac_bot_whitelist` 中 `whitelist_type='governance'` 且未过期的 Bot 跳过，
     审计 `action_taken='whitelist_filtered'`。
  2. **静默期过滤**：该 Bot 存在 `governance_status='muted'` 的通知
     → 跳过创建（扫描处理 muted 记录时更新 `last_seen_at`），
     审计 `action_taken='muted'`。
  3. **已有未反馈通知**：`governance_status='open'` 的 Bot → 跳过，不重复创建。
  4. **冷却期检查**：该 Bot 最近一条 `governance_status='closed'` 记录 `cooldown_until > now()` → 跳过，
     审计 `action_taken='cooldown_filtered'`。**expired 不参与 cooldown 判断——expired 不是周期闭环，不阻断续催。**
  5. **创建新通知**：详见下方「创建新通知规则」。
- [ ] 同一 Bot 同一天不重复创建通知（UK `(bot_id, dt_version)` 去重）。
- [ ] **冷却策略**：`cooldown_until` 仅在 `governance_status='closed'` 时写入，
      表示治理周期已闭环后的冷却期。同一 Bot 在 `cooldown_until` 前不创建新的治理通知。
      **`expired` 不写 `cooldown_until`**——expired 是单条记录终态，不是周期终态，
      不阻断下一轮续催。
      `cooldown_days` 可通过 `GovernanceConfig` 配置（默认 14 天）。
- [ ] **创建新通知规则**（对每个通过加白过滤的 actionable Bot 按顺序检查）：
  1. **已有 open 通知** → 跳过，不重复创建。
  2. **已有 muted 通知** → 跳过创建（扫描处理 muted 记录时更新 `last_seen_at`），
     审计 `action_taken='muted'`。
  3. **最近一条 closed 且 `cooldown_until > now()`** → 跳过，
     审计 `action_taken='cooldown_filtered'`（仅 closed 参与 cooldown 判断）。
  4. **最近一条 expired** → 继承 `governance_cycle_id`，创建新一轮通知
     （expired 不是闭环，同一周期续催。不查 cooldown）。
  5. **最近一条 closed 且 cooldown 已过** → 生成新 `governance_cycle_id`，创建新周期通知。
  6. **无历史记录** → 生成新 `governance_cycle_id`，创建首次通知。
  审计 `action_taken='enqueued'`。
  **核心口径**：一直没响应 → 持续催同一 `governance_cycle_id`；有响应/系统闭环 → closed + cooldown；cooldown 后再次 actionable → 新 `governance_cycle_id`。
- [ ] 通知内容包含：Bot 名称、命中维度、优先级、预估节省、`notification_md` 正文。
- [ ] **Markdown 通知模板**（Phase 1 简单通知，`sampleMarkdown` 格式）：

  消息结构设计为：一目了然的关键信息 + 引导点击深链接反馈。

  ```markdown
  #### 📦 Bot 成本优化建议: {bot_name}

  **👤 负责人**：{owner} | {department}
  **📊 日均消耗**：{daily_tokens} Token
  **🎯 优化判定**：{optimization_summary}
  **🔖 命中维度**：{hit_dimensions}

  ---

  **📋 问题摘要**

  {problem_summary}

  **🔧 优化建议**

  {action_items_formatted}

  ---
  > {disclaimer}
  > 📎 [查看详情 / 反馈]({resolve_url})
  ```

  **模板变量来源**：
  - `bot_name` / `owner` / `department` / `daily_tokens` — 来自 `notification_structured.meta`
  - `optimization_summary` / `hit_dimensions` — 来自 `notification_structured.meta`
  - `problem_summary` — 来自 `notification_structured.problem_summary`
  - `action_items_formatted` — 来自 `notification_structured.action_items`，
    格式为 `index. ⚠️ action ↓ effect（需确认）`，`needs_owner_confirm=true` 的加 ⚠️ 标记
  - `disclaimer` — 来自 `notification_structured.disclaimer`
  - `resolve_url` — `https://ocb.teamclaw.com/governance/notify/{notification_id}`
    （前端页面，用户在此进行 4 种反馈操作）

  **DingTalk 发送方式**：
  - API: `POST /v1.0/robot/oToMessages/batchSend`
  - `msgKey`: `sampleMarkdown`
  - `msgParam`: `{"title": "Bot 治理通知", "text": <rendered_markdown>}`
  - `robotCode`: 从配置读取（如 `dingyboqbe20cac7exdp`）
  - `userIds`: `[entity_id]`（工号/staffId）
  - 凭证来源：`GovernanceDingTalkConfig(app_key, app_secret, robot_code)`，
    DI 从 SecretResolver 或环境变量注入

- [ ] **TC 卡片通知模板**（Phase 1 升级版，`createAndDeliver` 格式，与 Markdown 简单通知互换）：

  使用预注册的钉钉卡片壳模板，卡片壳内由 `DDRichTextView` 渲染 Markdown 格式 reason，
  底部"立即查看"链接打开 teamclaw preview iframe 展示完整 React 组件并收集反馈。

  **卡片壳模板**：
  - 模板 ID: `bc2d6541-d26c-4e66-a1a1-ba40fa424a70.schema`
  - 卡片标签名（`cardName`）: "成本优化通知"（不再显示"HITL"）
  - 模板变量: `reason`（markdown 类型，DDRichTextView 渲染）+ `detailLink`（深链接）
  - 卡片组件布局: BaseText("成本优化通知") → BaseText(默认提示，reason 为空时显示) → Markdown(reason) → 分割线 → Link("立即查看", detailLink)

  **reason 内容**（Markdown 格式，由 `_build_governance_reason()` 从 `notification_structured` 构建）：

  ```markdown
  ### 📦 {title}

  **⚠️ 严重程度**：{severity}

  **👤 负责人**：{owner}
  **🏢 组织**：{organization}
  **🤖 Bot**：{bot_name}

  **📊 日均消耗**：{daily_tokens} Token
  **💡 优化潜力**：{optimization_potential} Token
  **📈 优化率**：**{optimization_rate}**

  ---

  ### 📋 问题摘要

  > {problem_summary}

  ---

  ### 🔧 优化建议

  **1. {suggestion_1_title}**
  > {suggestion_1_desc}

  **2. {suggestion_2_title}**
  > {suggestion_2_desc}

  ---

  📎 [点击详情链接查看完整分析并提交反馈]
  ```

  **detailLink 深链**：
  - 三层嵌套编码: `open_platform_link` → `open_side_popup_wnd` → teamclaw preview URL
  - preview URL: `https://teamclaw.com/preview?skipBrain=true&type=custom&botId={bot_id}&cardId={card_id}&data={base64_json}&params={base64_json}`
  - `data` 参数: base64(`{"data": notification_structured}`)
  - `params` 参数: base64(`{"type": "custom", "botId": bot_id}`)
  - 点击后在钉钉侧边栏打开 iframe，由 `LLMComponent_v2.jsx` 渲染完整通知内容 + 反馈表单

  **DingTalk 发送方式**：
  - API: `POST /v1.0/card/instances/createAndDeliver`
  - `cardTemplateId`: `bc2d6541-d26c-4e66-a1a1-ba40fa424a70.schema`
  - `cardData.cardParamMap`: `{"reason": <markdown_string>, "detailLink": <deep_link>}`
  - `openSpaceId`: `dtv1.card//im_robot.{user_id}`（用接收人工号，不是 robotCode）
  - `callbackType`: `STREAM`（Phase 1 不使用 HTTP 回调）
  - `robotCode`: 从配置读取
  - `userIdType`: `1`（staffId）
  - 凭证来源：同 Markdown 通道的 `GovernanceDingTalkConfig`

  **与 Markdown 简单通知的互换规则**：
  - 由 `GovernanceConfig.notify_channel` 决定使用哪个通道
    （`"markdown"` = 简单通知，`"tc_card"` = TC 卡片通知）
  - **降级规则**：当 `notify_channel="tc_card"` 但 `card_template_id` 或
    `app_key`/`app_secret` 未配置时，自动降级为 Markdown 通道，审计记录降级原因
  - **数据一致性**：两种通道使用相同的 `notification_structured` 数据源，
    差异仅在发送 API 和渲染方式，反馈 API 和审计逻辑完全一致
  - **提醒通知**也遵循同一通道：首发用 TC 卡片则提醒也用 TC 卡片
    （reason 追加"⚠️ 此通知已超期 N 天未处理"前缀）

  **已知局限**：
  - `reason` 内容有长度上限（约 2000 字符），超长 `optimizationSuggestions` 需截断
  - 卡片壳标签名由模板 `cardName` 决定，运行时不可修改
  - 点击"立即查看"打开的 iframe 依赖 teamclaw 前端 /preview 页面可用

  **提醒通知模板**：与首发模板结构相同，在标题前追加
  `⚠️ 此通知已超期 {overdue_days} 天未处理` 提示前缀。

- [ ] 单次扫描最大通知数可配置（默认 200），超出部分日志告警。

### 状态追踪 + 自动关闭

通知内容冻结（`notification_md` / `notification_structured` 不更新），但追踪 Bot 的
最新治理决策状态。连续 N 天非 actionable 时自动关闭工单。

- [ ] `ac_governance_notify_log` 已有字段（结构预留，Phase 1 启用逻辑）：
  - `latest_decision` String(32) nullable：最近一次扫描时该 Bot 的
    governance_decision。初始值 = 创建时的 `governance_decision`（即 `'actionable'`）。
  - `consecutive_normal_days` Integer default 0：连续非 actionable 天数。初始值 = 0。
- [ ] **状态追踪**（仅在数据就绪检查通过后执行，数据未就绪时跳过）：对 `governance_status`
  为 `'open'` 或 `'muted'` 的通知，读取当天 `dt_version` 下**完整 decision 集合**（不仅限于 actionable）：
  - 读取 `ac_governance_task_record_daily` 中 `dt_version` 且 `analysis_status` 在已完成集合的所有记录。
    因 UK `(worker_id, dt_version)` 保证每个 worker 每天只有一条记录，无需 GROUP BY，
    直接构建 `decision_map: dict[worker_id, governance_decision]`。
    **注意**：不能用 `MAX(governance_decision)` 聚合——字符串字典序 (`observe` > `justified` > `actionable`)
    与业务优先级 (`actionable` > `observe` > `justified`) 不一致。若未来 UK 放宽导致
    同一 worker 同一天可能有多条记录，则必须按业务优先级显式聚合：
    `actionable ∨ observe ∨ justified`（任一 actionable 则整体 actionable，否则有 observe 则 observe，否则 justified）。
  - **`governance_status='open'` 的通知**：
    - **worker_id 在 decision_map 中**：
      - `decision == 'actionable'` → `latest_decision='actionable'`,
        `consecutive_normal_days=0`。Bot 仍有问题，工单保持 open。
      - `decision in ('observe', 'justified')` → `latest_decision=该decision`,
        `consecutive_normal_days += 1`。Bot 明确已恢复。
    - **worker_id 不在 decision_map 中**（不在治理范围内）：
      - `latest_decision` 保持不变
      - `consecutive_normal_days += 1`。**不在治理范围 = 恢复正常**。
      - 审计 `action_taken='out_of_scope'`
    - 若 `consecutive_normal_days >= auto_resolve_threshold_days`（默认 3）→
      `governance_status='closed'`, `response='resolved_by_system'`,
      `response_source='system_auto'`, `close_reason='auto_resolved'`,
      `closed_at=now()`, `cooldown_until=now()+cooldown_days`，
      审计 `action_taken='auto_resolved'`。
    - 否则：仅更新两个字段，工单保持 open。
  - **`governance_status='muted'` 的通知**（用户选择 need_time，静默中）：
    - **Bot 已恢复**（worker_id 在 decision_map 且 `decision != 'actionable'`，
      或 worker_id 不在 decision_map / 不在治理范围内）：
      → 静默期内 Bot 恢复正常，直接 `governance_status='closed'`,
      `response='resolved_by_system'`, `response_source='system_auto'`,
      `close_reason='auto_resolved'`, `closed_at=now()`,
      `cooldown_until=now()+cooldown_days`，
      审计 `action_taken='auto_resolved'`。
    - **Bot 仍 actionable，静默期未过**（`mute_until > now()`）：
      → 仅更新 `last_seen_at=now()`，不创建新通知。
    - **Bot 仍 actionable，静默期已过**（`mute_until <= now()`）：
      → 用户的修复承诺未兑现，`governance_status='expired'`,
      `close_reason='mute_expired'`, `closed_at=now()`,
      `cooldown_until=NULL`（expired 不写 cooldown），
      审计 `action_taken='mute_expired'`。
      下次扫描若 Bot 仍 actionable，创建新记录（同一 `governance_cycle_id`，新一轮）。

  **核心规则**：非 actionable = 正常恢复。actionable 重置计数器为 0。
  "不在治理范围"与"observe/justified"同等处理——都不再是当前追踪的治理问题。
- [ ] `reader.get_completed_decisions(dt_version) -> dict[str, str]` 新增方法，
  从 `ac_governance_task_record_daily` 按 `analysis_status` 已完成集合读取当天完整 decision
  集合（不限 governance_decision 值），直接按 `worker_id` 映射（UK 保证唯一，无需聚合）。
  返回 `{worker_id: governance_decision}`。
- [ ] `auto_resolve_threshold_days` 可通过 `GovernanceConfig` 配置（默认 3）。
- [ ] `mute_grace_days` 可通过 `GovernanceConfig` 配置（默认 7）。`mute_until` 计算公式：
      `mute_until = repair_deadline + mute_grace_days`。

**状态机**：
```
open  → closed    (response=optimized / whitelist / dispute, 各自 close_reason)
open  → muted     (response=need_time + repair_deadline)
open  → closed    (close_reason=auto_resolved, 连续N天非actionable / 不在治理范围)
open  → expired   (close_reason=no_response_expired, 7天未回应)
muted → closed    (close_reason=auto_resolved, 静默期内Bot恢复)
muted → expired   (close_reason=mute_expired, 静默期过Bot仍actionable)
```

### 通知分发

Phase 1 发送链路由治理扫描任务内部循环完成，且受同一分布式锁保护。
当前仅单实例执行发送，不引入 claim_pending / sending / send_attempt_id 机制。

`notify_status` 只表示首发投递状态（pending/sent/cancelled），不承载提醒语义。
提醒发送走独立的 `remind_at` + `remind_count` 控制，不回写 `notify_status`。

**A. 首发通知**：
- [ ] 扫描创建通知后，在**同一锁保护下的内部循环**中查询
      `notify_status='pending'` 且 `governance_status='open'` 的通知。
      **`muted` 不进入首发查询**：muted 是用户已反馈 `need_time` 后的静默态，
      首发通知语义不再适用。若未来需要"静默期即将结束提醒"，
      应走独立的 reminder 逻辑，不复用首发 pending。
- [ ] 逐条发送 HTTP 通知：
      - **Markdown 通道**：DingTalk sampleMarkdown batchSend + 深链接，
        消息内嵌 OCB 前端治理详情页链接，用户点击链接到前端页面反馈。
      - **TC 卡片通道**：DingTalk createAndDeliver + Markdown reason + detailLink 深链，
        卡片壳内展示结构化通知内容，"立即查看"链接打开 teamclaw preview iframe 反馈。
      - 通道由 `GovernanceConfig.notify_channel` 决定；TC 卡片凭证未配置时自动降级为 Markdown。
- [ ] **发送成功**：`notify_status='sent'`，`sent_at=now()`，`external_message_id` 记录外部消息 ID，
      `send_attempt_count += 1`，`last_send_at=now()`，`last_send_error=NULL`。
- [ ] **发送失败**：`notify_status` 保持 `'pending'`，`send_attempt_count += 1`，
      `last_send_at=now()`，`last_send_error=error`。下次扫描继续重试 pending 通知。
- [ ] 对 `governance_status IN ('closed', 'expired')` 且 `notify_status='pending'` 的通知，
      发送前自动标记 `notify_status='cancelled'`。

**B. 提醒通知**：
- [ ] 扫描时对 `governance_status='open'` 且 `notify_status='sent'` 且 `remind_at <= now()` 的记录，
      发送提醒 Markdown。
- [ ] **提醒成功**：`remind_count += 1`，`remind_at = NULL`，审计 `reminded`。
- [ ] **提醒失败**：保留 `remind_at`，下次扫描继续尝试。

- [ ] 若未来 notify-sender 独立部署或多实例运行，再升级为 claim 机制。

### 提醒 + 过期关闭

以一周为周期管理未回应的工单。提醒通过 `remind_at` 触发独立发送，
不复用 `notify_status`，不创建新记录。一轮周期结束后仍未回应则过期关闭工单，
后续扫描创建新记录时携带最新治理数据和累积未反馈天数，新记录即时发送。

**一个周期（7天）内的节奏**：
```
Day 0:  创建通知，notify_status='pending'      ← 即时首发
Day 3:  remind_at 到期，发送提醒 Markdown      ← 第2次提醒（notify_status 保持 sent）
Day 7:  无反馈 → expired关闭 → 扫描创建新记录(带最新数据) → 即时发送
```

- [ ] **创建通知时**设置 `remind_at = gmt_create + 3天`，`remind_count = 0`，
      `expire_at = gmt_create + expire_days`。
- [ ] 扫描时检查提醒（发送逻辑见「通知分发 B. 提醒通知」），提醒结果影响过期判断。
- [ ] **过期关闭**：`governance_status='open'` 且 `expire_at <= now()`
      且 `remind_count >= 1` →
      `governance_status='expired'`, `close_reason='no_response_expired'`,
      `closed_at=now()`, `cooldown_until=NULL`（expired 不写 cooldown），
      审计 `action_taken='expired_unresolved'`。
- [ ] **新记录接续周期**：过期后下次扫描若 Bot 仍 actionable，创建新记录
      （同一 `governance_cycle_id`，`governance_status='open'`），
      即时创建并发送（`notify_status='pending'`），携带最新治理数据。
      跨记录的未反馈天数可通过 `WHERE governance_cycle_id = :cid AND governance_status = 'expired'`
      查询聚合，无需冗余字段。
- [ ] `notification_md` 在第 2 次提醒时可追加"⚠️ 此通知已超期 3 天未处理"提示前缀。
- [ ] `expire_days` 可通过 `GovernanceConfig` 配置（默认 7）。

### 用户反馈

- [ ] `POST /api/economy/governance/notifications/{notification_id}/resolve` 接受正式 response：
      `optimized` / `need_time` / `dispute` / `whitelist`。
      也接受从 pending 态升级：当 `response` 当前为 `dispute_pending`/`whitelist_pending`
      时，允许调用 resolve 将其升级为正式 `dispute`/`whitelist`（需带 remark）。
- [ ] 只有 Bot Owner 可操作（`owner_id` 校验）；非 Owner 返回 403。
- [ ] 一条通知只能正式反馈一次（幂等：response 已为正式值时重复请求返回已有结果）。
      `dispute_pending`/`whitelist_pending` 为中间态，不占正式反馈名额。
- [ ] `response = 'whitelist'` 时，自动调用 `GovernanceWhitelistService.batch_add()` 将 Bot 加入
      `ac_bot_whitelist`（`whitelist_type='governance'`, `source='owner'`）。
- [ ] `response = 'dispute'` 时，remark 字段必填；若为空返回 400。
- [ ] `response = 'need_time'` 时，`repair_deadline` 字段必填（用户承诺的修复截止日期）。
      若为空返回 400。系统自动计算静默截止时间：
      `mute_until = repair_deadline + mute_grace_days`（默认 7 天，可配置）。
      `governance_status` 转为 `'muted'`，扫描不再提醒但继续追踪 Bot 状态。
      静默期内 Bot 恢复 → 直接 `closed + auto_resolved`；静默期过仍 actionable → `expired + mute_expired`。
- [ ] 反馈写入 `ac_governance_notify_log` 的 `response` + `response_at` +
      `response_remark` + `response_source` + `mute_until` + `cooldown_until` 字段（不另建反馈表）。
      `optimized`/`dispute`/`whitelist` → `cooldown_until = closed_at + cooldown_days`；
      `need_time` → 无 `cooldown_until`（尚未终态）。
      **`expired` 不写 `cooldown_until`**（expired 不是闭环，不阻断续催）。
- [ ] `response_source` Phase 1 为 `'http_api'` 或 `'system_auto'`
      （Phase 2 增加 `'card_callback'`）。
- [ ] 每次反馈写入 `ac_governance_check_audit`（`action_taken='user_resolved'`）。

### 加白名单

- [ ] `POST /api/economy/governance/whitelist/batch` 支持批量加白，`source` 默认 `'manual'`，
      写入 `ac_bot_whitelist`（`whitelist_type='governance'`）。
- [ ] `GET /api/economy/governance/whitelist` 查询当前治理加白列表（`whitelist_type='governance'`）。
- [ ] 加白条目支持可选过期时间 `expires_at`，过期后扫描时视为未加白。
- [ ] 同一 `(bot_id, owner_id, whitelist_type)` 重复加白为幂等操作（skip，不报错）。
- [ ] **source 渠道透传**：`feedback_service.py` 在 `response=whitelist` 时，将 `resolve()` 接收到的
      `source`（`http_api` / `card_callback`）透传给 `batch_add()`，不再硬编码 `"owner"`。
      DDL `source` COMMENT 更新为 `system/owner/admin/manual/emergency/card_callback/http_api/owner_feedback`，
      ORM model 和 API schema 注释同步。
- [ ] **reason 应用层截断**：`whitelist_repo.batch_add()` 中 `reason` 字段截断至 500 字符
      ——`(entry.get("reason") or "")[:500]`。同时修复 `reason=None` 时返回 `None` 的问题。

### 审计

- [ ] 每次扫描生成唯一 `run_id`，所有审计记录关联该 `run_id`。
- [ ] Phase 1 审计事件：扫描到 actionable Bot（enqueued）、加白过滤
      （whitelist_filtered）、静默期过滤（muted）、冷却期过滤（cooldown_filtered）、
      自动关闭（auto_resolved）、静默期过期（mute_expired）、
      掉出追踪范围（out_of_scope）、
      提醒（reminded）、过期关闭（expired_unresolved）、
      数据未就绪（data_not_ready）、扫描异常（error）、用户反馈（user_resolved）、
      紧急制动（emergency_paused / emergency_resumed / emergency_whitelisted / emergency_cancelled）。
- [ ] Phase 1 暂不需要：observe_filtered、data_refreshed（Phase 2 补上）。
- [ ] 审计记录包含 `dry_run` 标记，区分真实执行和干运行。

### 手动触发

- [ ] `POST /api/economy/governance/internal/trigger-scan` 可手动触发扫描（测试用），
      可选 `dry_run` 覆盖。

### 紧急制动（Emergency Brake）

运行时紧急开关，基于 ZCache 分布式缓存，跨 Pod 生效，不需要重启服务。

**状态存储**：
- ZCache Key: `governance:emergency:{env}`
- Value: `JSON {"action": "pause", "reason": "...", "operator": "...", "paused_at": "..."}`
- TTL: 7 天（防忘记恢复，自动过期）

**检查点**：
- 扫描 Cron 启动时 → 检查 key 存在且 `action == "pause"` → 跳过扫描
- 发送前 → 检查 key 存在且 `action == "pause"` → 跳过发送（pending 通知保留，待 resume 后下次扫描重试）
- 用户反馈 → **不受 pause 影响**，始终可处理

- [ ] `POST /api/economy/governance/internal/emergency` 支持以下 action：
- [ ] `pause`：停止扫描 + 停止通知发送（pending 通知保留，不取消）。写 ZCache key（TTL 7 天），
      审计 `action_taken='emergency_paused'`。重复 pause 覆盖。
- [ ] `resume`：恢复正常运行。删除 ZCache key，
      审计 `action_taken='emergency_resumed'`。无 pause 时无效但幂等。
- [ ] `bulk-whitelist`：批量加白 + 取消这些 Bot 的 pending 通知。
      批量写 `ac_bot_whitelist`（`whitelist_type='governance'`, `source='emergency'`），
      批量更新 `ac_governance_notify_log SET notify_status='cancelled', governance_status='closed', close_reason='emergency_closed', closed_at=now(), cooldown_until=now()+cooldown_days WHERE bot_id IN (...) AND response IS NULL AND governance_status IN ('open', 'muted')`，
      审计 `action_taken='emergency_whitelisted'`。不设 pause flag。
- [ ] `cancel-pending`：取消所有 pending 通知。
      批量更新 `ac_governance_notify_log SET notify_status='cancelled', governance_status='closed', close_reason='emergency_closed', closed_at=now(), cooldown_until=now()+cooldown_days WHERE response IS NULL AND governance_status IN ('open', 'muted')`，
      审计 `action_taken='emergency_cancelled'`。不设 pause flag。
- [ ] `GET /api/economy/governance/internal/emergency` 查询当前紧急状态：paused(bool),
      reason, operator, paused_at, pending_count, whitelist_count。
- [ ] 请求体含 `reason` 字段（必填），每次操作记录操作原因。
- [ ] 所有 emergency 操作写审计 `ac_governance_check_audit`，含 `action_taken` 和 `error_msg`。

### Admin close_all_open 端到端验证

- [ ] 使用 `OpenclawSessionAnalysis` 的 upload 脚本向 singlebox 注入 task_rec_daily 数据，
      触发扫描后确认 `ac_governance_notify_log` 有 `governance_status='open'` 的记录。
- [ ] 调用 `POST /internal/emergency` `action=close-all-open` 关单，
      确认 `open_count` 归零、已关闭记录 `governance_status='closed'` + `close_reason='admin_closed'` +
      `closed_at IS NOT NULL` + `cooldown_until IS NOT NULL`。
- [ ] 审计记录已写入：`ac_governance_check_audit` 中有 `action_taken='admin_closed_all'` + `source='admin_api'`。
- [ ] 已有用户反馈的 muted 记录（`response='need_time'`）也被关闭，
      但 `response` / `response_source` / `mute_until` / `repair_deadline` 保持原值。

### 离线数据写入（ODPS → 在线库）

- [ ] 提供 `POST /api/economy/governance/records/offline-batch` 端点，供离线治理管线
      （`economy_governance`）将 T+1 分析结果批量 upsert 到在线 DB。
      路径与 `POST /api/harness/diagnose/records/offline-batch` 平行（economy 与 harness
      为并列一级模块）。
- [ ] 写入目标为 `ac_governance_task_record_daily`（在线库自有表，非 ODPS 映射）。
      ODPS 管线的 `analysis_daily` 仅是分析过程数据，不透出到在线库。
- [ ] 逻辑 key upsert：同名+同 dt+同 task_create_key 的记录匹配则更新，否则插入
     （同 `harness/offline-batch` 模式）。
- [ ] 每次调用时，整批记录共享同一个 `last_sync_at = now()`，无论 INSERT 还是 UPDATE
     都写入该值。扫描通过 `MAX(last_sync_at)` 判断是否有新数据到达。
- [ ] 支持自定义 `gmt_create`（离线跑批可带原始时间戳）。
- [ ] 端点使用 Bearer token 鉴权（同 `bot_dormant` 的 SecretResolver 模式）。
- [ ] 离线管线调用时机：每日 ODPS 跑批完成后（约 T+1 凌晨），调用此接口写入。
- [ ] 扫描 Cron 读取目标是 `ac_governance_task_record_daily` 在线表，而非 ODPS 远程表。

### 可观测性

- [ ] 每次扫描跑完输出一条结构化日志：
      ```python
      log.info("[GovernanceScan] Completed run", extra={
          "run_id": run_id,
          "duration_seconds": duration,
          "dt_version": dt_version,
          "total_actionable": total,
          "newly_enqueued": newly_enqueued,
          "whitelist_filtered": whitelist_filtered,
          "muted": muted,
          "cooldown_filtered": cooldown_filtered,
          "auto_resolved": auto_resolved,
          "out_of_scope": out_of_scope,
          "data_not_ready": data_not_ready,
          "skipped_existing": skipped_existing,
          "errors": errors,
          "dry_run": dry_run,
      })
      ```
- [ ] 告警规则（运维侧接入 antlogs）：
  - cron 连续 2 天没跑 → 钉钉告警（infra 故障）
  - 单次 `errors / total > 10%` → 钉钉告警（扫描异常率过高）
  - offline-batch 端点连续 3 天无调用 → 钉钉告警（离线数据链路断裂）
  - 单次新创建通知数 > 500 → 钉钉告警（异常放量，检查数据）

### 数据保留期

| 表 | 保留期 | 理由 |
|---|---|---|
| `ac_governance_notify_log` | 180 天 | 通知 + 反馈记录，运营查询主表 |
| `ac_governance_check_audit` | 365 天 | 审计记录，长期合规需求 |
| `ac_bot_whitelist` | 永久 | 加白是长有效期操作，不自动清理 |
| `ac_governance_task_record_daily` | 90 天 | 离线数据中转，扫描只需近期分区 |

### 不添加的字段（刻意不加，记录原因）

| 不加的字段 | 原因 |
|---|---|
| `notification_retries` | 不使用此命名。Phase 1 用 `send_attempt_count` 统计所有发送尝试（不论成功失败），失败保持 pending 由下次扫描重试，不做复杂退避 |
| `total_unresponded_days` | 冗余。`governance_cycle_id` 已能查到同一周期所有 expired 记录，跨记录未反馈天数可聚合查询，无需冗余字段 |
| `last_remind_at` | 再通知策略 Phase 2 才做 |
| `BotModel.governance_status` | 治理工单状态在 notify_log.governance_status 中管理，不改 Bot 主表 |
| `GovernanceNotifyLog.notification_version` | 内容不刷新（Phase 1 冻结），无需版本号 |
| `governance_status` 值 `scheduled`/`resolved`/`disputed` | `governance_status` 只表达系统处理态，不承载业务语义。`need_time` → `muted`（活跃），`optimized`/`dispute`/`whitelist` → `closed` + 各自 `close_reason`。避免状态膨胀 |
| `parent_notification_id` | 链式遍历复杂。改用 `governance_cycle_id`：同一 Bot 同一治理周期所有记录共享 cycle_id，`WHERE governance_cycle_id = :cid` 一查即获全周期历史 |

### DB 优化不变更项

| 项 | 原因 |
|---|---|
| DDL ALTER 语句（source 类型/长度） | source 仅注释更新，VARCHAR(64) 足够容纳新值 |
| DDL ALTER 语句（reason 类型） | 保持 VARCHAR(512) 不变，应用层截断更安全；TEXT 在 OceanBase 上可能影响索引策略 |
| UNIQUE KEY | 已覆盖去重需求，无需变更 |
| admin_service.py `source="emergency"` | 语义准确，保持不变 |
| 白名单查询逻辑 | source 仅记录来源，不影响查询/过滤 |

## In Scope — Phase 1

- `core/economy/governance/` 模块：ORM 模型、离线批量写入服务、扫描服务、反馈服务、
  加白服务、内部服务、Markdown 通知模板（单向 sampleMarkdown）、生命周期、紧急制动服务。
- 离线→在线数据通路：`POST /api/economy/governance/records/offline-batch` 端点 + upsert 逻辑。
  路径与 harness 的 `/api/harness/diagnose/records/offline-batch` 平行。
- `adapters/http/economy/`：公开路由 + 内部路由 + schemas + auth。
- DI 模块绑定 + 配置。
- 审计表 `ac_governance_check_audit`。
- **通知通道**：Markdown 简单通知（sampleMarkdown batchSend）**或** TC 卡片通知
  （createAndDeliver + Markdown reason + detailLink），两种通道可互换。
  降级规则：TC 卡片凭证未配置时自动降级为 Markdown。
  Phase 1 在扫描锁内单阶段发送，不依赖独立 notify-sender。

## Out of Scope — Phase 1

- DingTalk 交互卡片 + HTTP 回调按钮（Phase 2，含复选框 + 4 按钮的完整交互卡片）。
  TC 卡片通知（Markdown reason + detailLink 深链）已在 Phase 1 实现。
- 数据刷新（`latest_dt` / `data_refresh_count` 更新逻辑，Phase 2）。
- 再通知策略（Phase 2）。
- 前端页面 `/governance/notify`（Phase 2+）。
- 效果验证（"已优化"后自动复查 Token 变化，Phase 2+）。
- 治理动作执行（自动降频/限流/暂停 Bot）——本 Feature 只做"通知 + 反馈"闭环。
- 离线治理管线（`economy_governance`）本身的改动——管线侧代码不在本 Feature scope 内。
- `bot_dormant` 模块的任何改动。
- DingTalk 卡片模板的创建/注册（TC 卡片模板 `bc2d6541` 已注册，Phase 2 交互卡片模板待注册）。

## Data Model Changes

新增 2 张可写表（`ac_` 前缀）+ 1 张统一白名单表 + 1 张离线数据表（`ac_` 前缀，离线写入在线读取）：

> **设计说明**：ODPS 管线产出的 `analysis_daily` 仅为分析过程数据，不透出到在线库。
> 在线侧只存 `task_record_daily`（扫描+通知所需的核心字段），通知内容也从此表构建，
> 无需 JOIN `analysis_daily`。

### `ac_governance_notify_log`

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BigInteger auto | PK | |
| notification_id | String(64) | UK | 通知唯一 ID（UUID4） |
| bot_id | String(64) | | |
| bot_name | String(128) | | 冗余，方便展示 |
| owner_id | String(64) | | |
| entity_id | String(64) | | 工号 |
| worker_id | String(128) | | user_id:bot_id |
| dt_version | String(8) | | 数据版本标记 |
| governance_decision | String(32) | | actionable/observe/justified |
| hit_dimensions | String(512) | | 命中维度 JSON array |
| hit_dimensions_count | Integer | | |
| expected_token_saving | BigInteger | | |
| saving_ratio | Float | | 0-1 |
| notification_md | Text | | 正文 Markdown |
| notification_structured | Text | | 结构化 JSON |
| analysis_ref | String(128) | | 关联分析 ID |
| governance_max_dimension | String(64) | | |
| governance_max_priority | String(8) | | P0/P1 |
| notify_status | String(16) | | pending/sent/cancelled |
| sent_at | DateTime | nullable | 发送成功时间 |
| send_attempt_count | Integer | | 发送尝试次数。初始 = 0，每次发送（不论成功失败）+1 |
| last_send_at | DateTime | nullable | 最近一次发送尝试时间 |
| last_send_error | Text | nullable | 最近一次发送失败错误信息 |
| external_message_id | String(128) | nullable | 外部消息 ID（DingTalk 返回） |
| governance_status | String(16) | | open/muted/closed/expired。系统处理态，仅 4 值 |
| notify_type | String(32) | | actionable_governance |
| notify_channel | String(16) | | 发送通道：`markdown` / `tc_card`。记录实际使用的通道（含降级后的结果） |
| notify_source | String(32) | | daily_scan/manual |
| governance_cycle_id | String(64) | | 治理周期 ID。同一 Bot 未闭环期间共享，closed 后新周期用新 ID |
| response | String(32) | nullable | optimized/need_time/dispute/whitelist/dispute_pending/whitelist_pending/resolved_by_system。`expired` 不进此字段，由 `close_reason` 表达 |
| response_at | DateTime | nullable | |
| response_remark | Text | nullable | 用户备注（dispute/whitelist 时填写） |
| response_source | String(32) | nullable | Phase 1: http_api / system_auto；Phase 2 增加 card_callback |
| close_reason | String(32) | nullable | 关闭/过期原因。user_optimized/user_disputed/user_whitelisted/auto_resolved/mute_expired/no_response_expired/emergency_closed |
| closed_at | DateTime | nullable | 关闭/过期时间 |
| cooldown_until | DateTime | nullable | 冷却截止时间。**仅 `closed` 时写入**；= `closed_at + cooldown_days`。同一 Bot 在此时间前不创建新通知。`expired` 不写 `cooldown_until`（expired 是单条记录终态，不是周期终态，不阻断续催） |
| repair_deadline | DateTime | nullable | 用户承诺的修复截止日期（need_time 时必填） |
| latest_dt_version | String(8) | | Phase 1: 创建时 = dt_version；Phase 2: 随数据刷新更新 |
| data_refresh_count | Integer | | Phase 1: 固定 0；Phase 2: 随数据刷新递增 |
| latest_decision | String(32) | nullable | 最近扫描时该 Bot 的 decision。初始 = governance_decision（'actionable'） |
| consecutive_normal_days | Integer | | 连续非 actionable 天数。初始 = 0。达到阈值自动关闭 |
| mute_until | DateTime | nullable | 静默截止时间。此时间前不创建新通知。`need_time` 时 = repair_deadline + 7天宽限期 |
| last_seen_at | DateTime | nullable | 静默期内最近一次扫描发现该 Bot 仍 actionable 的时间 |
| feedback_payload | Text | nullable | 结构化用户反馈 JSON。格式预定义，见下方规范 |
| remind_count | Integer | | 当前工单已提醒次数。初始 = 0。提醒发送成功时 +1 |
| remind_at | DateTime | nullable | 下次提醒时间。创建时 = gmt_create + 3天，提醒后清空（一轮仅提醒1次） |
| expire_at | DateTime | nullable | 本轮过期时间。创建时 = gmt_create + expire_days |
| dry_run | Integer | | 0/1 |
| gmt_create | DateTime | | |
| gmt_modified | DateTime | | |

UK: `(bot_id, dt_version)`

**Phase 1 语义说明**：
- `governance_status` 仅 4 态：**`open`**（待处理，活跃）/ **`muted`**（用户申请延期，活跃）/ **`closed`**（已闭环，终态）/ **`expired`**（本轮过期，终态）。业务语义不塞进 status，由 `response` + `close_reason` + `audit` 表达
- **`open` 和 `muted` 是活跃态**，扫描每次处理。`closed` 和 `expired` 是终态，不再修改
- `governance_cycle_id` 治理周期 ID。同一个 Bot 的同一治理问题，只要没有进入 closed，
  就一直复用同一个 `governance_cycle_id`。open / muted / expired 都属于未闭环周期。
  expired 后如果问题仍 actionable，新建 notify_log 记录但继承 `governance_cycle_id`。
  只有 closed 才表示该治理周期结束，后续再次触发治理时创建新 `governance_cycle_id`。
  查询 `WHERE governance_cycle_id = :cid` 获得同一催促周期内的全部记录。
  跨周期历史通过 `WHERE bot_id = :bot_id ORDER BY gmt_create` 查询
- `dt_version` 创建时标记数据版本，语义等同离线分区日期但命名解耦
- `latest_dt_version` 创建时等于 `dt_version`，Phase 1 不会被更新
- `data_refresh_count` 创建时为 0，Phase 1 不会被更新
- `response` 有 4 种用户主动反馈值 + `resolved_by_system`（系统自动关闭）。`expired` 不进 response，由 `close_reason` 表达
- `close_reason` 仅在 `governance_status` 为 `closed` 或 `expired` 时有值：
  - `closed` 时：`user_optimized` / `user_disputed` / `user_whitelisted` / `auto_resolved` / `emergency_closed`
  - `expired` 时：`no_response_expired` / `mute_expired`
- `closed_at` 在 `governance_status` 变为 `closed` 或 `expired` 时写入
- `cooldown_until` **仅在 `closed` 时写入**，= `closed_at + cooldown_days`。
  同一 Bot 在 `cooldown_until` 前不创建新通知。
  `expired` 不写 `cooldown_until`——expired 是单条记录终态，不是周期终态，不阻断续催。
  `need_time` → `muted` 时不写 `cooldown_until`（尚未终态）
- `response_source` 有 `http_api`（用户反馈）+ `system_auto`（系统自动关闭）
- `mute_until` 仅在 `response='need_time'` 时设置，= repair_deadline + mute_grace_days(7)
- `repair_deadline` 用户承诺的修复截止日期，`need_time` 时必填
- `expire_at` 创建时 = gmt_create + expire_days(7)，过期判断用此字段
- `last_seen_at` 静默期内扫描仍发现该 Bot actionable 时更新，不创建新通知
- `remind_count` 一轮周期内最多 +1（Day 3 提醒1次），提醒发送成功时 +1
- `remind_at` 创建时 = gmt_create + 3天，提醒后清空
- **`notify_status` 仅 3 态**：`pending`（待发送/发送失败待重试）/ `sent`（已发送）/ `cancelled`（已取消）。失败不设独立状态，保持 `pending` 靠 `send_attempt_count` 跟踪
- `sent_at` 仅在 `notify_status='sent'` 时写入
- `send_attempt_count` 初始 = 0，每次发送（不论成功失败）+1。下次扫描继续重试 pending 通知
- `last_send_at` 最近一次发送尝试时间（不论成功失败）
- `last_send_error` 最近一次发送失败错误信息，成功后清空
- `external_message_id` 发送成功后记录 DingTalk 返回的消息 ID

**`feedback_payload` 结构化反馈格式**：

`feedback_payload` 存储 JSON，采用宽表思路——一个通知一条 JSON，预定义格式不轻易变。
与 `response`/`governance_status` 解耦：`response` 决定工单去向，`feedback_payload` 存微观态度。
反馈条目对照 `notification_structured.action_items` 的 `index`，用户按 `index` 逐条给出反馈。
条目之间不要求正交，用户可以跨条目表达意见，也可以只反馈部分条目。

```json
{
  "version": 1,
  "overall_action": "partial",
  "overall_remark": "整体接受，但部分建议有异议",
  "repair_deadline": "2026-07-15",
  "items": [
    {
      "index": 1,
      "action": "accepted",
      "remark": null
    },
    {
      "index": 2,
      "action": "rejected",
      "remark": "该工具仍在使用中，判定有误"
    },
    {
      "index": 3,
      "action": "partial",
      "remark": "只认30%的浪费，其余是业务需要"
    }
  ]
}
```

**顶级字段**：
- `version` 整数，当前为 `1`。后续格式变更时递增，代码按 version 兼容解析，**禁止删改已有字段**，只允许追加
- `overall_action` 枚举：`accepted` / `rejected` / `partial`。用户对整个通知的整体态度
- `overall_remark` 自由文本，用户对整个通知的总体意见
- `repair_deadline` ISO 日期字符串，仅 `response='need_time'` 时有效，其他 response 时为 null

**`items` 逐条反馈**：
- 每条对照 `notification_structured.action_items` 中对应 `index` 的建议项
- `index` 整数，与 `action_items[].index` 一致，标识用户反馈的是哪条建议
- `action` 枚举：`accepted`（接受）/ `rejected`（拒绝）/ `partial`（部分接受）
- `remark` 该条上的用户备注，可为 null

**约束**：
- `items` 可为空数组（用户只填了整体反馈，没逐条评价）
- `items` 不要求覆盖所有 `action_items`（未列出的 = 用户未评价，默认 skipped）
- 整个 `feedback_payload` 可为 null（用户只选了 response 没填细节）
- 系统不解析 JSON 内容结构，只校验是合法 JSON；格式由前端保证
- `notification_structured` 的 schema 由离线管线定义（当前 `schema_version: "v1"`），
  `feedback_payload` 的 `items[].index` 与其 `action_items[].index` 对齐，两者通过 `index` 关联

**`governance_status` 系统处理态**：

`governance_status` 与 `notify_status` 是两个正交维度：
- `notify_status` = **通知投递**状态（pending → sent/cancelled）。失败不设独立状态，保持 `pending` 靠 `send_attempt_count` 跟踪
- `governance_status` = **系统处理态**（仅表达"系统还要不要继续处理这条通知"）

| governance_status | 语义 | 是否终态 | 扫描行为 | 触发条件 |
|---|---|---|---|---|
| `open` | 待处理 | 否（可转换） | ✅ 扫描处理 | 扫描创建通知时 |
| `muted` | 延期静默中 | 否（可转换） | ✅ 扫描处理 | 用户反馈 `need_time` + `repair_deadline` |
| `closed` | 已闭环 | **终态** | ❌ 不处理 | `close_reason` 区分：user_optimized / user_disputed / user_whitelisted / auto_resolved / emergency_closed |
| `expired` | 本轮过期 | **终态** | ❌ 不处理 | `close_reason` 区分：no_response_expired / mute_expired |

**设计原则**：
- `governance_status` 只表达"系统还要不要继续处理"，不承载业务语义
- 业务语义由 `response`（用户做了什么）+ `close_reason`（为什么关闭/过期）+ `audit`（中间过程）表达
- 扫描只处理 `open` 和 `muted`，`closed` / `expired` 永远不再修改

Phase 1 转换规则（`cooldown_until` 仅在 closed 时写入，expired 不写）：
```
open  → closed   (response=optimized,    close_reason=user_optimized,     cooldown_until=closed_at+cooldown_days) — 终态
open  → closed   (response=dispute,      close_reason=user_disputed,      cooldown_until=closed_at+cooldown_days) — 终态
open  → closed   (response=whitelist,    close_reason=user_whitelisted,   cooldown_until=closed_at+cooldown_days) — 终态
open  → muted    (response=need_time,    repair_deadline必填, 无cooldown_until)                        — 活跃
open  → closed   (response=resolved_by_system, close_reason=auto_resolved, cooldown_until=closed_at+cooldown_days) — 终态
open  → closed   (close_reason=emergency_closed, cooldown_until=closed_at+cooldown_days)                — 终态
open  → expired  (close_reason=no_response_expired, cooldown_until=NULL)                                — 单条记录终态，非周期闭环
muted → closed   (response=resolved_by_system, close_reason=auto_resolved, cooldown_until=closed_at+cooldown_days) — 终态，静默期内恢复
muted → expired  (close_reason=mute_expired, cooldown_until=NULL)                                       — 单条记录终态，非周期闭环
```

**`expired` 后新一轮**：expired 后下次扫描若 Bot 仍 actionable，创建新记录
（同一 `governance_cycle_id`，`governance_status='open'`）。
旧 expired 记录不再变动。expired 是单条 notify_log 的终态，不是治理周期终态；
expired 不写 `cooldown_until`，也不阻断下一轮续催。
**`closed` 后新一轮**：closed 后 cooldown 过若 Bot 再次 actionable，创建新记录
（**新 `governance_cycle_id`**，`governance_status='open'`）。
closed = 周期结束，cooldown 保护后再开新周期。

**不可以做的事**：
- ❌ 不要给 `governance_status` 加 `scheduled` / `resolved` / `disputed` 等业务语义值
- ❌ 不要让 `governance_status` 同时表示"是否活跃"和"业务怎么结束"
- ❌ 不要往 status 里加不影响扫描器行为的值——那种信息放 `close_reason` 或 `audit`

### `ac_governance_check_audit`

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BigInteger auto | PK | |
| run_id | String(64) | | 扫描运行 ID |
| bot_id | String(64) | | |
| owner_id | String(64) | | |
| check_result | String(32) | | Phase 1: actionable/observe/justified/skipped_whitelist/skipped_cooldown/auto_resolved/out_of_scope/errored |
| governance_decision | String(32) | | |
| hit_dimensions | String(512) | | |
| expected_token_saving | BigInteger | nullable | |
| saving_ratio | Float | nullable | |
| action_taken | String(64) | | Phase 1: enqueued/whitelist_filtered/muted/cooldown_filtered/auto_resolved/mute_expired/out_of_scope/reminded/expired_unresolved/data_not_ready/error/user_resolved/emergency_paused/emergency_resumed/emergency_whitelisted/emergency_cancelled |
| source | String(32) | | daily_scan |
| error_msg | Text | nullable | |
| dry_run | Integer | | |
| gmt_create | DateTime | | |

### `ac_bot_whitelist`（统一白名单表）

统一白名单表，`whitelist_type` 区分用途。governance 使用 `whitelist_type='governance'`，
未来 dormant 等模块可使用 `whitelist_type='dormant'` 等。现有 `ac_bot_dormant_whitelist`
表不动，dormant 模块继续独立使用，未来可按需迁移到本表。

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BigInteger auto | PK | |
| bot_id | String(64) | | |
| owner_id | String(64) | | |
| whitelist_type | String(32) | | governance / dormant（预留）/ 未来扩展 |
| source | String(64) | | 来源: system / owner / admin / manual / emergency / card_callback / http_api / owner_feedback |
| reason | String(512) | | 加白原因，应用层截断至 500 字符（留 12 字节余量防多字节溢出） |
| created_by | String(64) | | |
| expires_at | DateTime | nullable | 空=永久 |
| gmt_create | DateTime | | |

UK: `(bot_id, owner_id, whitelist_type)`

### `ac_governance_task_record_daily`（离线写入，在线读取）

离线管线通过 `POST /offline-batch` upsert 写入，在线扫描 Cron 读取。
只存扫描+通知创建阶段必需的核心字段。管线簿记字段（task_create_key、
analysis_ref、estimated_improvement_range、baseline_tokens 等）不透出。
`notification_structured`（Phase 1.5 卡片预构建 JSON）和 `env` 等后续按需增加。

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BigInteger auto | PK | |
| worker_id | String(128) | | user_id:bot_id |
| bot_id | String(64) | | Bot ID |
| dt_version | String(8) | | 数据版本标记 |
| governance_decision | String(32) | | actionable/observe/justified |
| bot_name | String(128) | | 通知展示必需 |
| hit_dimensions | String(512) | | 命中维度，通知核心 |
| hit_dimensions_count | Integer | | 命中维度数 |
| governance_max_priority | String(8) | | P0/P1 |
| expected_token_saving | BigInteger | nullable | 预估节省 |
| saving_ratio | Float | nullable | 0-1 |
| task_summary | Text | nullable | 一句话摘要 |
| analysis_status | String(32) | | success/success_with_warnings/error/skipped |
| last_sync_at | DateTime | | 最近一次同步时间。每次 offline-batch 调用时整批统一写入当前时间 |
| gmt_create | DateTime | | 离线可自定义 |
| gmt_modified | DateTime | | |

UK: `(worker_id, dt_version)`

## API / Interface Changes

### 公开路由 `/api/economy/governance`

| Method | Path | Auth | 说明 |
|---|---|---|---|
| GET | /notifications | RequestContext | 查询当前用户待处理通知 |
| GET | /notifications/history | RequestContext | 历史反馈记录 |
| GET | /notifications/{notification_id} | RequestContext | 通知详情 |
| POST | /notifications/{notification_id}/resolve | RequestContext | 用户反馈 |
| POST | /whitelist/batch | RequestContext | 批量加白 |
| GET | /whitelist | RequestContext | 查询加白列表 |

Resolve 请求体：
```json
{
  "response": "optimized|need_time|dispute|whitelist",
  "remark": "optional",
  "repair_deadline": "2026-07-15",
  "feedback_payload": {
    "version": 1,
    "overall_action": "partial",
    "overall_remark": "整体接受，但部分建议有异议",
    "repair_deadline": "2026-07-15",
    "items": [
      {"index": 1, "action": "accepted", "remark": null},
      {"index": 2, "action": "rejected", "remark": "工具仍在使用中"},
      {"index": 3, "action": "partial", "remark": "只认30%的浪费"}
    ]
  }
}
```

- `response` 必填
- `remark` 可选（`dispute` 时必填）
- `repair_deadline` 仅 `response='need_time'` 时必填，ISO 日期格式
- `feedback_payload` 可选。传入时直接存入 `ac_governance_notify_log.feedback_payload`，
  系统不解析内容，只校验是合法 JSON。格式由前端保证。
  `items[].index` 与 `notification_structured.action_items[].index` 对齐

### 内部路由 `/api/economy/governance`（Bearer token）

| Method | Path | 说明 |
|---|---|---|
| POST | /records/offline-batch | 离线治理管线批量写入分析结果（与 `/api/harness/diagnose/records/offline-batch` 平行） |
| POST | /internal/trigger-scan | 手动触发扫描 |
| POST | /internal/emergency | 紧急制动（pause/resume/bulk-whitelist/cancel-pending） |
| GET | /internal/emergency | 查询紧急状态 |

**Phase 1 无 `/card-callback` 端点**，Phase 2 增加。

## Notes / Intentional Design Decisions

- **Phase 1 支持两种可互换的通知通道**：Markdown 简单通知（sampleMarkdown batchSend）
  和 TC 卡片通知（createAndDeliver + Markdown reason + detailLink）。
  TC 卡片通知是 Markdown 简单通知的升级版：卡片壳内由 DDRichTextView 渲染结构化的
  Markdown reason（标题、指标、摘要、建议），底部"立即查看"链接打开 teamclaw
  preview iframe 渲染完整 React 组件（LLMComponent_v2.jsx）供用户查看详情和提交反馈。
  卡片壳标签由模板 `cardName` 决定（"成本优化通知"），不再显示"HITL"。
  两种通道由 `GovernanceConfig.notify_channel`（`"markdown"` / `"tc_card"`）切换，
  TC 卡片凭证未配置时自动降级为 Markdown。数据源和反馈 API 完全一致，仅发送通道不同。
  Phase 2 的交互卡片（含 HTTP 回调按钮的完整交互卡片）仍为独立增量。
  Phase 1 发送链路在扫描锁内直接完成，不依赖独立 notify-sender 拉取。
  聚焦于：数据通路（offline-batch → 在线库）+ 扫描创建 + 扫描锁内发送 +
  HTTP API 反馈 + 审计。先把闭环跑通，再叠交互卡片能力。

- **Phase 1 状态机 4 态**：`governance_status` 仅 `open` / `muted` / `closed` / `expired`，
  业务语义由 `response` + `close_reason` + `audit` 表达。`muted` 是活跃态，扫描每次处理。
  `open` 和 `muted` 是唯一可转换态；`closed` 和 `expired` 是终态。
  状态追踪仅在数据就绪检查通过后执行。不做数据刷新，不做再通知。

- **独立模块，不与 `bot_dormant` 合并。** 两者业务逻辑不同：dormant 管理 Bot 生命周期
  （回收/复用），governance 管理成本优化（通知/反馈）。独立演进，共享模式但不共享代码。

- **离线→在线数据通路：HTTP batch upsert**（参照 `harness/offline-batch` 模式）。
  离线治理管线（`economy_governance`）在 ODPS 跑批完成后，调用
  `POST /api/economy/governance/records/offline-batch` 将分析结果 upsert 到在线
  `ac_governance_task_record_daily` 表。路径与 harness 的
  `/api/harness/diagnose/records/offline-batch` 平行（economy 与 harness
  为并列一级模块）。ODPS 侧的 `analysis_daily` 仅是分析过程数据，
  不透出到在线库。在线扫描 Cron 读取 `task_record_daily` 表，而非远程查 ODPS。

- **统一白名单表 `ac_bot_whitelist`，`whitelist_type` 区分用途。** governance 用
  `whitelist_type='governance'`，dormant 等模块未来可用 `whitelist_type='dormant'`。
  现有 `ac_bot_dormant_whitelist` 不动。UK 为 `(bot_id, owner_id, whitelist_type)`，
  同一 Bot 可同时有不同类型的白名单。

- **`latest_dt` 和 `data_refresh_count` Phase 1 写入但不更新。** 建表时就包含这两个
  字段（避免后续 ALTER），但 Phase 1 逻辑中 `latest_dt = dt`、
  `data_refresh_count = 0`，不会被刷新。Phase 2 接管更新逻辑。

- **紧急制动基于 ZCache，不建新表。** `governance:emergency:{env}` key 存在即暂停，
  删除即恢复。TTL 7 天防忘记。扫描 Cron 启动和发送前两个检查点读到 pause 即跳过，
  用户反馈不受影响（已发出去的通知用户必须能回应）。

- **`resolved_by_system` / `system_auto` / `auto_resolved` / `out_of_scope` / `data_not_ready`
  值在 Phase 1 会被产生**（自动关闭逻辑）。`card_callback`、`observe_filtered`、
  `data_refreshed` 仍在 Phase 2 / Phase 3 才会产生。

## Open Questions

- 是否需要"连续 N 天已优化自动标记解决"？Phase 1/1.5 暂不实现，Phase 2 视运营反馈决定。
- `need_time`（需时间）是否有明确延期天数？Phase 1 仅记录，不做自动再通知调度。
- 已有未反馈通知的 Bot 在后续扫描中是否需要重新提醒？Phase 1 不重新发送（跳过），
  Phase 2 再通知策略解决。

---

## 附录 A：Phase 2 增量差异（交互卡片）

Phase 2 在 Phase 1 基础上增加 DingTalk **完整交互卡片**能力
（含 HTTP 回调按钮的交互卡片，区别于 Phase 1 的 TC 卡片通知）：

**Phase 1 TC 卡片通知 vs Phase 2 交互卡片**：
- **Phase 1 TC 卡片**（`bc2d6541`）：卡片壳 + Markdown reason + detailLink 深链，
  用户点击"立即查看"在 iframe 内反馈。无卡片内按钮回调。
- **Phase 2 交互卡片**：在 Phase 1 TC 卡片基础上增加交互组件
  （CheckboxListMulti 勾选建议 + 4 个按钮回调），用户在卡片壳内直接操作
  无需打开 iframe。需要额外注册交互卡片模板和 HTTP 回调端点。

### 新增 AC

- [ ] 新增 `GovernanceCardSender`，调用 `POST /v1.0/card/instances/createAndDeliver`
     发送交互卡片（4 按钮：已优化/需时间/不认可/申请加白）。
- [ ] 新增 `POST /api/economy/governance/internal/card-callback` 端点，验签（HMAC-SHA256）
     + 解析 action（`{response}:{notification_id}`）。
- [ ] **卡片回调分流处理**（解决 remark 必填与卡片无输入框的冲突）：
  - `optimized` / `need_time`：卡片回调直接调用 `feedback_service.resolve()`，
    response 正式写入（`response='optimized'` 或 `'need_time'`），
    `response_source='card_callback'`。卡片显示确认并清空按钮。
  - `dispute` / `whitelist`：卡片回调写入 **pending 态**：
    `response='dispute_pending'` 或 `'whitelist_pending'`，
    `response_source='card_callback'`。**不调用 resolve()，不做 remark 校验**。
    卡片显示"请前往详情页补填理由"。
- [ ] **两阶段反馈闭环**：dispute_pending / whitelist_pending 的通知，
  用户在详情页补填 remark 后调用 `POST /api/economy/governance/notifications/{id}/resolve`，
  response 从 `dispute_pending` → `dispute`、`whitelist_pending` → `whitelist`。
  第二次调用时 `response_source='http_api'`。
- [ ] `response` 枚举增加 `dispute_pending` / `whitelist_pending` 两个值。
- [ ] `response_source` 增加 `'card_callback'` 值。
- [ ] 幂等规则：`dispute_pending` / `whitelist_pending` 仍视为"未正式反馈"——
  不写 `cooldown_until`（pending 态不触发冷却），扫描不被跳过。
  只有正式 response（dispute / whitelist / optimized / need_time）且工单终态时才写 `cooldown_until`。
- [ ] 凭证未配置时降级为 Phase 1 通道（TC 卡片 → Markdown 降级链）。
- [ ] `GovernanceDingTalkConfig` 新增 `interactive_card_template_id`
     （交互卡片模板 ID，与 Phase 1 的 `tc_card_template_id` 区分），DI 条件绑定。
- [ ] 卡片发送去重：同一 `notification_id` 24 小时内不重复发送。

### 状态机扩展

Phase 1.5 增加 pending 中间态（`response` 层面，不影响 `governance_status`）：

```
open → closed   (response=optimized, close_reason=user_optimized)
open → muted    (response=need_time + repair_deadline)
open → closed   (response=dispute → close_reason=user_disputed)
open → closed   (response=whitelist → close_reason=user_whitelisted)
open → closed   (response=dispute_pending → 补填 → close_reason=user_disputed)
open → closed   (response=whitelist_pending → 补填 → close_reason=user_whitelisted)
muted → closed  (response=resolved_by_system, close_reason=auto_resolved)
muted → expired (close_reason=mute_expired)
```

### 不改动的部分

- 数据模型不变（`response` 字段本身已是 String，无需 ALTER）
- 扫描逻辑不变
- offline-batch 不变

---

## 附录 B：Phase 3 增量差异（数据刷新 + 再通知）

Phase 3 在 Phase 2 基础上增加数据刷新和再通知能力：

### 新增 AC

- [ ] **数据刷新**：已有未反馈通知的 Bot 再次被扫描到时，刷新通知内容
     （`latest_dt` = 最新 dt，`data_refresh_count++`），审计 `data_refreshed`。
     不重复创建通知，不重复发卡片/Markdown。
- [ ] **observe 过滤**：扫描时审计 `observe_filtered`（`check_result='skipped_observe'`）。
- [ ] **再通知策略**：对 `need_time` 反馈的通知，N 天后可重新触发通知（策略待定）。
- [ ] **效果复查**：对 `optimized` 反馈的 Bot，自动复查 Token 变化，衡量治理效果。

### 状态机变化

Phase 1 的 4 态模型：
```
open / muted / closed / expired
```

Phase 3 扩展（状态不变，新增 refresh 行为）：
```
open → closed   (data_refreshed → auto_resolved)
muted → expired (mute_expired，静默期过期后创建新记录重新通知)
```

### 数据模型变化

`ac_governance_notify_log`：
- `latest_dt_version` 开始被更新（Phase 1 固定 = dt_version）
- `data_refresh_count` 开始递增（Phase 1 固定 = 0）

`ac_governance_check_audit`：
- `check_result` 增加 `skipped_observe`
- `action_taken` 增加 `observe_filtered` / `data_refreshed`

---

## 变更记录

### 架构与分层

模块从单文件拆分为 `repositories/`（IO 层：task_record_repo / notify_log_repo / whitelist_repo）+ `services/`（领域层：scan_service / feedback_service / admin_service）+ `contracts/`（协议 + 模型）。合并 oceanbase_reader + offline_batch_service → task_record_repo，统一 3 处 `_write_audit` → `NotifyLogRepository.add_audit`。

钉钉发送服务从 `governance_notify_sender.py` 通用化为 `dingtalk_sender.py`：删除硬编码常量 `_DEFAULT_CARD_ID` / `_GOVERNANCE_TC_CARD_TEMPLATE_ID`，改为参数注入（`card_template_id=""` 空降级 Markdown，`out_track_id_prefix` 参数化，governance 传 `"gov-notify"`）。

配置统一收束到 YAML `application-prod.yaml`（pre + prod 共用 `_pre` 后缀），删除硬编码环境映射和 Mist Secret 依赖。删除 `mute_grace_days`（统一 `cooldown_days`），新增 `scan_minute`。

### 逻辑修复

状态跟踪：每条 open/muted 记录用自身 `dt_version` 查 `decision_map`，不再统一用当前扫描版本，避免旧记录误判 out_of_scope。

数据准备检查：`_check_data_readiness` 删除审计表比较逻辑，离线管线与扫描解耦——有数据即 ready。

iframe 回调：`iframe_callback_url` + `staff_id` 通过 `GovernanceDingTalkConfig` 注入，编码到 TC 卡片深度链接最内层，3 处调用点均已传递。

### 数据模型

4 表索引补全（7 个新索引），`worker_id` 扩至 `String(160)`，删除死字段 `analysis_ref` / `governance_max_dimension`，`expected_token_saving` 改为 `BigInteger`。

### 审计补全

发送审计：首次发送成功/失败记 `first_delivered` / `first_delivery_failed`，提醒失败记 `remind_failed`，router scan-and-deliver 同步写审计。反馈审计：`action_taken` 从笼统的 `"user_resolved"` 细化为 `"feedback_{response}"`（4 种具体类型）。

### 清理

移除两阶段反馈（dispute/whitelist 无 remark 直接 400）、删除 Direct Card 时代遗留脚本和模板。