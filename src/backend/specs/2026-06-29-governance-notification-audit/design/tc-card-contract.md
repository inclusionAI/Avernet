# TC Card + Callback 标准对接方案

> 本文档将 TC 卡片发送、卡片 UI 反馈、回调接口、测试验证合并为一份完整对接方案。
> 原拆分文件 `04-feedback-standard.md` / `05-card-llm-prompt.md` / `06-card-callback-spec.md` / `07-card-callback-test.md` 已合并至本文档，后续以本文档为唯一权威来源。

---

## 1. 背景与问题

### 1.1 现状

当前治理通知系统存在 **两套不兼容的反馈语义**：

| 维度 | spec 定义（权威） | tc_card 实现（LLMComponent_v2.jsx） |
|---|---|---|
| 反馈决策 | `response` 4 值：`optimized` / `need_time` / `dispute` / `whitelist` | `feedback.isAdopted` boolean + `feedback.action` 6 值 + `feedback.schedule` + `feedback.notes` |
| 逐条态度 | `feedback_payload.items[]` 逐条对照 `action_items` 表态 | 无逐条反馈能力，6 个预设优化维度与 `action_items` 无关 |
| 整体态度 | `feedback_payload.overall_action`：`accepted` / `rejected` / `partial` | `isAdopted` boolean，无 `partial` 粒度 |
| 加白入口 | `response=whitelist` 是 4 种正式 response 之一 | 卡片 UX 无加白按钮 |
| 回调接口 | `GovernanceFeedbackService.resolve()` 接受 `response` + `remark` + `repair_deadline` + `feedback_payload` | `fetch POST` 到 `callbackUrl` 发送 `{noticeId, botId, feedback: {isAdopted, action, schedule, notes}}` |

**核心矛盾**：卡片回调 POST 的 payload 与 spec 的 `resolve()` 入参完全不通，需要一个适配层将卡片 UX 反馈转化为 spec 语义。

### 1.2 设计原则

1. **以 spec 为准**：`response` 4 值 + `feedback_payload` 格式不变
2. **卡片 UX 重构**：改造 LLMComponent 反馈表单，从"选优化方式"变为"逐条对建议表态 + 选整体 response"，与 spec 语义对齐
3. **回调适配层**：新增 card-callback 端点，接收 tc_card 的 fetch POST，转化为 spec 的 resolve 调用
4. **两阶段反馈**：`dispute` / `whitelist` 需补填理由，走 pending → 正式升级流程

---

## 2. TC 卡片发送规范

### 2.1 发送账号

| 账号 | app_key | 来源 | 用途 |
|---|---|---|---|
| **AI 工作台官方 (TeamClawProd)** | `dingyboqbe20cac7exdp` | `bcs-config-prod.toml` | **标准发送账号**，发送者显示"AI工作台" |
| 治理通知 (TeamClawGovernance) | `dingi36aqke5ljeatq5s` | Mist 密钥 | 备用 |
| HITL (TeamClawHITL) | `dingzhmhx8qt2spkifqn` | HITL 插件 | HITL 场景专用 |

> AppSecret 不在代码中明文存储，需通过 Mist 密钥库或环境变量传入。

### 2.2 卡片壳模板

| 模板 | ID | 说明 |
|---|---|---|
| **治理通知专用** | `bc2d6541-d26c-4e66-a1a1-ba40fa424a70.schema` | **默认模板**，reason 变量为 markdown 类型，DDRichTextView 渲染 |
| HITL 通用 | `7bfd3e2a-ac5c-45c4-bbf1-b009f31956df.schema` | 所有应用可共用，reason 为纯文本 |

### 2.3 卡片变量槽

| 变量名 | 类型 | 说明 |
|---|---|---|
| `reason` | markdown | 卡片壳上显示的摘要，由 DDRichTextView 渲染，支持 Markdown 语法 |
| `detailLink` | string | 深链 URL，点击卡片跳转到 teamclaw preview 页面 |
| `session_id` | string | 会话 ID（卡片壳内部使用） |
| `bot_id` | string | Bot 标识 |

### 2.4 detailLink 三层深链结构

```
第一层: dingtalk://dingtalkclient/action/open_platform_link
  参数: pcLink = <第二层URL编码>
         mobileLink = <第二层URL编码>

第二层: dingtalk://dingtalkclient/action/open_side_popup_wnd
  参数: url = <第三层URL编码>&ddtab=true

第三层: https://teamclaw.com/preview
  参数: skipBrain=true
        type=custom
        botId=xxx
        cardId=xxx
        data=<base64(UTF8 JSON)>     ← 通知数据
        params=<base64(JSON)>
        callbackUrl=<URL编码>          ← ★ 反馈回调地址
        staffId=xxx                    ← ★ 用户工号
```

### 2.5 reason Markdown 构建规则

使用 `bc2d6541` 模板时，`reason` 字段由 DDRichTextView 渲染，支持以下 Markdown 语法：

- 标题（`###`）、加粗（`**`）、引用（`>`）、分割线（`---`）、链接
- 以下 Markdown **不支持**：表格、代码块、HTML 标签

content_hash 构建逻辑（`_build_governance_reason()`）：

```markdown
### 📦 {title}

**⚠️ 严重程度**：{severity}

**👤 负责人**：{owner}
**🏢 组织**：{organization}
**🤖 Bot**：{botName}

**📊 日均消耗**：9775 万 Token
**💡 优化潜力**：9775 万 Token
**📈 优化率**：**100%**

---

### 📋 问题摘要

> {summary}

---

### 🔧 优化建议

**1. {suggestion.title}**
> {suggestion.description}

**2. ...**

---

📎 [点击详情链接查看完整分析并提交反馈]
```

### 2.6 发送脚本（send_tc_card.py）关键参数

```bash
# 标准发送（默认使用 AI 工作台官方账号）
python send_tc_card.py \
  --app-secret $DINGTALK_APP_SECRET \
  --user 168640

# 自定义通知数据
python send_tc_card.py --user 168640 --data-json @notification.json

# 仅发 Markdown 消息 (降级模式)
python send_tc_card.py --mode markdown --user 168640

# 指定反馈回调地址 (编码到 detailLink URL 中)
python send_tc_card.py --user 168640 \
  --callback-url https://agentclaw-pre.teamclaw.com/api/economy/governance/card-callback?staffId=168640
```

### 2.7 双通道互换

| 通道 | `notify_channel` 值 | 说明 |
|---|---|---|
| TC 卡片 | `tc_card` | 默认，支持 Markdown reason + detailLink 深链 |
| Markdown 单聊 | `markdown` | 降级通道，使用 `batchSend` API |

自动降级规则：TC 卡片发送失败 → 自动回退到 Markdown 单聊。

---

## 3. 反馈字段规范

### 3.1 `response` 4 值

| response | 中文 | governance_status | close_reason | cooldown_until | 必填字段 |
|---|---|---|---|---|---|
| `optimized` | 已优化 | `closed` | `user_optimized` | `closed_at + cooldown_days` | 无 |
| `need_time` | 需时间 | `muted` | — | — | `repair_deadline` |
| `dispute` | 不认可 | `closed` | `user_disputed` | `closed_at + cooldown_days` | `remark` |
| `whitelist` | 申请加白 | `closed` | `user_whitelisted` | `closed_at + cooldown_days` | `remark`；同时自动加白 |

### 3.2 `feedback_payload` JSON Schema

```json
{
  "version": 1,
  "overall_action": "accepted | rejected | partial",
  "overall_remark": "自由文本，用户对整个通知的总体意见",
  "repair_deadline": "ISO 日期 | null，仅 response=need_time 时有效",
  "items": [
    {
      "index": 1,
      "action": "accepted | rejected | partial",
      "remark": "该条上的用户备注，可为 null"
    }
  ]
}
```

**字段说明**：

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `version` | integer | 必填，固定 `1` | 格式版本号，后续递增，禁止删改已有字段 |
| `overall_action` | enum | 必填 | `accepted`（全盘接受）/ `rejected`（全盘拒绝）/ `partial`（部分接受）|
| `overall_remark` | string | 可选 | 用户对整个通知的总体意见 |
| `repair_deadline` | string | `response=need_time` 时必填 | ISO 日期格式 |
| `items` | array | 可选，可为空数组 | 逐条反馈 |
| `items[].index` | integer | 必填 | 与 `action_items[].index` 一致 |
| `items[].action` | enum | 必填 | `accepted` / `rejected` / `partial` |
| `items[].remark` | string | 可选 | 该条上的用户备注 |

### 3.3 逐项 Key 绑定机制

`notification_structured.action_items` 与 `feedback_payload.items` 之间通过 **`index` 字段**一对一绑定：

```
notification_structured.action_items[N].index  ←→  feedback_payload.items[M].index
```

- `index` 由离线管线在 `notification_structured` 中分配（从 1 递增），**前端不可修改**
- `feedback_payload.items[].index` 必须与某个 `action_items[].index` 匹配，否则视为无效
- 用户只反馈了部分项 → `items[]` 只包含用户评价过的 `index`，未出现的 = skipped

**回溯逻辑**：

```python
def reconcile_feedback(notification_structured: dict, feedback_payload: dict) -> dict:
    """将 feedback_payload.items 与 action_items 按 index 对齐，产出逐项反馈报告。"""
    action_map = {item["index"]: item for item in notification_structured["action_items"]}
    feedback_map = {item["index"]: item for item in feedback_payload.get("items", [])}

    reconciled = []
    for idx in sorted(action_map.keys()):
        action_item = action_map[idx]
        fb = feedback_map.get(idx)
        reconciled.append({
            "index": idx,
            "suggestion": action_item["action"],
            "feedback_action": fb["action"] if fb else "skipped",
            "feedback_remark": fb.get("remark") if fb else None,
        })
    return reconciled
```

### 3.4 `response_source` 枚举

| 值 | 来源 |
|---|---|
| `http_api` | 前端页面 resolve 请求 |
| `system_auto` | 系统自动关闭 |
| `card_callback` | 卡片回调（tc_card iframe fetch POST）|

### 3.5 `close_reason` 映射规则

| governance_status | close_reason | 触发条件 |
|---|---|---|
| `closed` | `user_optimized` | response=optimized |
| `closed` | `user_disputed` | response=dispute |
| `closed` | `user_whitelisted` | response=whitelist |
| `closed` | `auto_resolved` | 系统自动关闭 |
| `expired` | `no_response_expired` | 7天未回应 |
| `expired` | `mute_expired` | 静默期过仍 actionable |

---

## 4. 卡片 UI 设计（LLMComponent v3）

### 4.1 反馈状态重构

```jsx
// 旧版 (v2) — 已废弃
const [feedback, setFeedback] = useState({
  isAdopted: null, action: '', schedule: '', notes: ''
});

// 新版 (v3)
// 整体决策
const [feedback, setFeedback] = useState({
  response: '',        // optimized | need_time | dispute | whitelist
  remark: '',          // dispute/whitelist 时必填
  repair_deadline: '', // need_time 时必填
  overall_remark: '',  // 补充说明
});

// 逐项反馈：以 action_items[].index 为 key 的 Map
// Map<number, {action: 'accepted'|'rejected'|'partial', remark: string}>
const [itemFeedbacks, setItemFeedbacks] = useState(new Map());
```

### 4.2 UI 4 区域结构

```
┌──────────────────────────────────────────────────────┐
│ 区域 1: 信息展示区                                    │
│   标题 + 严重程度徽章 + 负责人/组织/指标/问题摘要        │
├──────────────────────────────────────────────────────┤
│ 区域 2: 逐项反馈区 ★核心                               │
│   每条 action_item 可独立: ✅ 接受 / 🔶 部分 / ❌ 拒绝  │
│   + 条件备注 + #index 编号 + 进度统计                   │
├──────────────────────────────────────────────────────┤
│ 区域 3: 整体决策区                                     │
│   ✅ 已优化 / ⏳ 需时间 / ❌ 不认可 / 📋 申请加白        │
│   + 条件字段 (截止日期/理由)                            │
├──────────────────────────────────────────────────────┤
│ 区域 4: 提交区                                        │
│   [提交反馈] → fetch POST 到 callbackUrl               │
└──────────────────────────────────────────────────────┘
```

### 4.3 逐项反馈表单渲染

```jsx
// 逐项反馈状态：以 index 为 key 的 Map
const [itemFeedbacks, setItemFeedbacks] = useState(new Map());
const evaluatedCount = itemFeedbacks.size;
const totalCount = (notice.action_items || []).length;

const setItemAction = (index, action) => {
  setItemFeedbacks(prev => {
    const next = new Map(prev);
    const existing = next.get(index) || { remark: '' };
    next.set(index, { ...existing, action });
    return next;
  });
};

const setItemRemark = (index, remark) => {
  setItemFeedbacks(prev => {
    const next = new Map(prev);
    const existing = next.get(index) || { action: '' };
    next.set(index, { ...existing, remark });
    return next;
  });
};

// 渲染逐项清单
{notice.action_items.map((item) => {
  const fb = itemFeedbacks.get(item.index);
  const currentAction = fb?.action || '';
  return (
    <div key={item.index} className="suggestion-card">
      <div className="suggestion-header">
        <span className="suggestion-index">#{item.index}</span>
        <span className="suggestion-title">{item.action}</span>
        {item.needs_owner_confirm && <span className="confirm-badge">⚠️ 需确认</span>}
        {currentAction && <span className="evaluated-badge">
          {currentAction === 'accepted' ? '✅' : currentAction === 'partial' ? '🔶' : '❌'}
        </span>}
      </div>
      <p><strong>改什么：</strong>{item.what_to_change}</p>
      <p><strong>为什么：</strong>{item.why}</p>
      <p><strong>预期效果：</strong>{item.expected_effect}</p>
      <div className="suggestion-actions">
        <button onClick={() => setItemAction(item.index, 'accepted')}
                className={currentAction === 'accepted' ? 'active-green' : ''}>✅ 接受</button>
        <button onClick={() => setItemAction(item.index, 'partial')}
                className={currentAction === 'partial' ? 'active-orange' : ''}>🔶 部分</button>
        <button onClick={() => setItemAction(item.index, 'rejected')}
                className={currentAction === 'rejected' ? 'active-red' : ''}>❌ 拒绝</button>
      </div>
      {(currentAction === 'rejected' || currentAction === 'partial') && (
        <input placeholder="备注（建议说明原因）"
               value={fb?.remark || ''}
               onChange={(e) => setItemRemark(item.index, e.target.value)} />
      )}
    </div>
  );
})}
<p className="evaluated-count">
  已评价 {evaluatedCount}/{totalCount} 项
  {evaluatedCount < totalCount && '（未评价的将标记为 skipped）'}
</p>
```

### 4.4 整体决策按钮

```jsx
<div className="response-buttons">
  <button onClick={() => setResponse('optimized')}
          className={response === 'optimized' ? 'active-green' : ''}>✅ 已优化</button>
  <button onClick={() => setResponse('need_time')}
          className={response === 'need_time' ? 'active-blue' : ''}>⏳ 需时间</button>
  <button onClick={() => setResponse('dispute')}
          className={response === 'dispute' ? 'active-red' : ''}>❌ 不认可</button>
  <button onClick={() => setResponse('whitelist')}
          className={response === 'whitelist' ? 'active-grey' : ''}>📋 申请加白</button>
</div>

{feedback.response === 'need_time' && (
  <input type="date" onChange={(e) => setRepairDeadline(e.target.value)}
         placeholder="修复截止日期（必填）" />
)}
{feedback.response === 'dispute' && (
  <textarea placeholder="不认可理由（必填）" onChange={(e) => setRemark(e.target.value)} />
)}
{feedback.response === 'whitelist' && (
  <textarea placeholder="加白理由（必填）" onChange={(e) => setRemark(e.target.value)} />
)}
```

### 4.5 overall_action 自动推导

| items 中各条 action | 推导 overall_action |
|---|---|
| 全部 `accepted` | `accepted` |
| 全部 `rejected` | `rejected` |
| 混合 | `partial` |
| items 为空 | optimized → `accepted`，dispute/whitelist → `rejected`，need_time → `accepted` |

```javascript
function deriveOverallAction(feedbackItems, response) {
  if (feedbackItems.length === 0) {
    return (response === 'dispute' || response === 'whitelist') ? 'rejected' : 'accepted';
  }
  const actions = new Set(feedbackItems.map(i => i.action));
  if (actions.size === 1) return [...actions][0];
  return 'partial';
}
```

### 4.6 handleSubmit

```jsx
const handleSubmit = async () => {
  if (!feedback.response) return;
  if ((feedback.response === 'dispute' || feedback.response === 'whitelist') && !feedback.remark) {
    alert('请填写理由'); return;
  }
  if (feedback.response === 'need_time' && !feedback.repair_deadline) {
    alert('请填写修复截止日期'); return;
  }

  const feedbackItems = [];
  for (const [index, fb] of itemFeedbacks) {
    if (fb.action) {
      feedbackItems.push({ index, action: fb.action, remark: fb.remark || null });
    }
  }

  const payload = {
    notification_id: notice.notification_id || notice.noticeId,
    response: feedback.response,
    remark: feedback.remark || null,
    repair_deadline: feedback.repair_deadline || null,
    feedback_payload: {
      version: 1,
      overall_action: deriveOverallAction(feedbackItems, feedback.response),
      overall_remark: feedback.overall_remark || null,
      repair_deadline: feedback.response === 'need_time' ? feedback.repair_deadline : null,
      items: feedbackItems,
    }
  };

  // 通道 1: fetch POST
  const callbackUrl = getCallbackUrl();
  if (callbackUrl) {
    await fetch(callbackUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  }
  // 通道 2: aixBridge 兜底
  if (window?.aixBridge?.submit) {
    await window.aixBridge.submit(JSON.stringify(payload));
  }
};
```

### 4.7 notification 数据字段对齐

| 卡片数据字段 | spec 字段 | 说明 |
|---|---|---|
| `noticeId` | `notification_id` | 通知唯一 ID |
| `title` | `notification_structured.title` | 标题 |
| `severity` | `meta.governance_max_priority` 映射 | P0→HIGH, P1→MEDIUM |
| `botName` | `meta.bot_name` 或 `meta.owner` | — |
| `action_items` | `notification_structured.action_items` | 优化建议（标准格式） |
| `optimizationSuggestions` | `action_items` | 兼容旧版格式 |
| `problem_summary` | `notification_structured.problem_summary` | 问题摘要 |

> **建议**：发送卡片时直接传入 `notification_structured` JSON，不再做 `sample_notification.json` 格式的转换。

---

## 5. 数据获取与 URL 参数解析

LLMComponent 运行在钉钉 iframe 内，数据和回调地址从运行环境解析，**不能写死任何 URL**。

### 5.1 数据获取：4 路降级取数

```
优先级 1: cardParamMap.data       → Aix 注入全局变量 data 中的 data 字段
优先级 2: cardParamMap.reason     → Aix 注入全局变量 data 中的 reason 字段
优先级 3: detailLink URL 的 data 参数 → base64 编码的 UTF-8 JSON
优先级 4: 当前页面 URL 的 data 参数     → base64 编码的 UTF-8 JSON
```

```javascript
function decodeBase64UTF8(b64) {
  const binaryStr = atob(b64);
  return JSON.parse(new TextDecoder().decode(
    Uint8Array.from(binaryStr, c => c.charCodeAt(0))
  ));
}

function extractNotice(parsed) {
  if (!parsed) return null;
  if (parsed?.data?.noticeId || parsed?.data?.notification_id) return parsed.data;
  if (parsed?.noticeId || parsed?.notification_id) return parsed;
  return null;
}

function getNoticeData() {
  const rawSource = (typeof data !== 'undefined' ? (data?.renderData || data) : {});

  // 优先级 1: cardParamMap.data
  const rawData = rawSource.data;
  if (rawData) {
    try {
      const parsed = typeof rawData === 'string' ? JSON.parse(rawData) : rawData;
      const notice = extractNotice(parsed);
      if (notice) return notice;
    } catch {}
  }

  // 优先级 2: cardParamMap.reason
  const rawReason = rawSource.reason;
  if (rawReason) {
    try {
      const parsed = typeof rawReason === 'string' ? JSON.parse(rawReason) : rawReason;
      const notice = extractNotice(parsed);
      if (notice) return notice;
    } catch {}
  }

  // 优先级 3: detailLink URL 的 data 参数
  const detailLink = rawSource.detailLink || '';
  try {
    const urlMatch = detailLink.match(/[?&]data=([^&]+)/);
    if (urlMatch) {
      const decoded = decodeBase64UTF8(decodeURIComponent(urlMatch[1]));
      const notice = extractNotice(decoded);
      if (notice) return notice;
    }
  } catch {}

  // 优先级 4: 当前页面 URL 的 data 参数
  try {
    const pageUrl = typeof window !== 'undefined' ? window.location.href : '';
    const urlMatch = pageUrl.match(/[?&]data=([^&]+)/);
    if (urlMatch) {
      const decoded = decodeBase64UTF8(decodeURIComponent(urlMatch[1]));
      const notice = extractNotice(decoded);
      if (notice) return notice;
    }
  } catch {}

  return {};
}
```

### 5.2 回调地址获取：2 路降级

```javascript
function getCallbackUrl() {
  const rawSource = (typeof data !== 'undefined' ? (data?.renderData || data) : {});
  const detailLink = rawSource.detailLink || '';
  try {
    const match1 = detailLink.match(/[?&]callbackUrl=([^&]+)/);
    if (match1) return decodeURIComponent(match1[1]);
  } catch {}

  try {
    const pageUrl = typeof window !== 'undefined' ? window.location.href : '';
    const match2 = pageUrl.match(/[?&]callbackUrl=([^&]+)/);
    if (match2) return decodeURIComponent(match2[1]);
  } catch {}

  return '';
}
```

### 5.3 detailLink 参数提取辅助

```javascript
function extractDetailLinkParams(detailLink) {
  if (!detailLink) return {};
  try {
    const directMatch = detailLink.match(/teamclaw\.alipay\.com\/preview\?(.*)$/);
    if (directMatch) return Object.fromEntries(new URLSearchParams(directMatch[1]));
  } catch {}
  try {
    const pcLinkMatch = detailLink.match(/pcLink=([^&]+)/);
    if (pcLinkMatch) {
      const decoded2ndLayer = decodeURIComponent(pcLinkMatch[1]);
      const urlMatch = decoded2ndLayer.match(/url=([^&]+)/);
      if (urlMatch) {
        const decoded3rdLayer = decodeURIComponent(urlMatch[1]);
        return Object.fromEntries(new URLSearchParams(decoded3rdLayer.split('?')[1] || ''));
      }
    }
  } catch {}
  return {};
}
```

---

## 6. 卡片回调接口

### 6.1 回调机制

卡片 `card_cb190863` 使用 **iframe fetch POST** 回调：

```
用户在卡片内点击"提交反馈"
  → LLMComponent handleSubmit()
  → getCallbackUrl() 从 URL 解析 callbackUrl
  → fetch(callbackUrl, { method: 'POST', body: JSON.stringify(payload) })
```

> 钉钉平台 HTTP 回调（callbackType=HTTP，钉钉服务器 POST 回来）已不需要——
> iframe 内完整 React 表单已覆盖所有反馈场景，卡片壳无交互按钮。

### 6.2 iframe fetch POST 回调接口

**端点**：`POST /api/economy/governance/card-callback`

> **公开路由而非 `/internal/`**：调用方是 TeamClaw 自身前端卡片（iframe 内 fetch POST），
> 复用 session cookie 鉴权，与 `/notifications/{id}/resolve` 一致。

**请求体**：

```json
{
  "notification_id": "test_abc123",
  "response": "optimized",
  "remark": null,
  "repair_deadline": null,
  "feedback_payload": {
    "version": 1,
    "overall_action": "accepted",
    "overall_remark": null,
    "repair_deadline": null,
    "items": [
      {"index": 1, "action": "accepted", "remark": null},
      {"index": 2, "action": "rejected", "remark": "业务需要"},
      {"index": 3, "action": "accepted", "remark": null}
    ]
  }
}
```

**身份来源**：只从 `RequestContext` 取 `user_id`，不从 body / query params 取。

**响应体（成功）**：

```json
{
  "success": true,
  "data": {
    "notification_id": "test_abc123",
    "governance_status": "closed",
    "close_reason": "user_optimized",
    "response": "optimized",
    "response_source": "card_callback",
    "message": null
  }
}
```

**响应体（两阶段 pending）**：

```json
{
  "success": true,
  "data": {
    "notification_id": "test_abc123",
    "governance_status": "open",
    "close_reason": null,
    "response": "dispute_pending",
    "response_source": "card_callback",
    "message": "异议已收到，请前往详情页补填理由以完成闭环"
  }
}
```

### 6.3 请求 Schema

```python
class CardCallbackIFrameRequest(BaseModel):
    """iframe fetch POST 回调请求体。"""
    notification_id: str = Field(..., description="通知唯一 ID")
    response: str = Field(..., description="optimized / need_time / dispute / whitelist")
    remark: str | None = Field(None, description="dispute/whitelist 时必填")
    repair_deadline: str | None = Field(None, description="need_time 时必填，ISO 日期")
    feedback_payload: dict | None = Field(None, description="结构化反馈 JSON")


class CardCallbackResponse(BaseModel):
    """卡片回调响应体。"""
    notification_id: str = ""
    governance_status: str = ""
    close_reason: str | None = None
    response: str = ""
    response_source: str = ""
    message: str | None = None

    @classmethod
    def from_result(cls, result: "ResolveResult") -> "CardCallbackResponse":
        return cls(
            notification_id=result.notification_id,
            governance_status=result.governance_status,
            close_reason=result.close_reason,
            response=result.response if hasattr(result, "response") else "",
            response_source=result.response_source if hasattr(result, "response_source") else "",
            message=result.message if hasattr(result, "message") else None,
        )
```

### 6.4 路由层

```python
@public_router.post("/card-callback")
async def card_callback(
    req: CardCallbackIFrameRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    """卡片 iframe fetch POST 回调。复用 session cookie 鉴权。"""
    return await callback_service.handle_iframe_callback(
        notification_id=req.notification_id,
        response=req.response,
        user_id=ctx.user_id,
        remark=req.remark,
        repair_deadline=req.repair_deadline,
        feedback_payload=req.feedback_payload,
    )
```

### 6.5 服务层

```python
class GovernanceCardCallbackService:
    """卡片回调服务：适配 iframe fetch POST → FeedbackService.resolve()。"""

    _ERROR_STATUS_MAP: dict[str, int] = {
        "NOT_FOUND": 404,
        "NOT_OWNER": 403,
        "INVALID_RESPONSE": 400,
        "MISSING_REMARK": 400,
        "MISSING_REPAIR_DEADLINE": 400,
        "INVALID_FEEDBACK_PAYLOAD": 400,
        "ALREADY_RESOLVED": 200,
        "DB_ERROR": 500,
    }

    @inject
    def __init__(self, feedback_svc: GovernanceFeedbackService) -> None:
        self._feedback_svc = feedback_svc

    async def handle_iframe_callback(
        self,
        notification_id: str,
        response: str,
        user_id: str,
        remark: str | None = None,
        repair_deadline: str | None = None,
        feedback_payload: dict | None = None,
    ) -> ApiResponse:
        # 两阶段分流：dispute/whitelist 缺 remark → pending 态
        if response in ("dispute", "whitelist") and not remark:
            result = self._feedback_svc.mark_pending_feedback(
                notification_id=notification_id,
                pending_response=f"{response}_pending",
                user_id=user_id,
                source="card_callback",
            )
        else:
            result = self._feedback_svc.resolve(
                notification_id=notification_id,
                response=response,
                user_id=user_id,
                remark=remark,
                repair_deadline=parse_date(repair_deadline),
                feedback_payload=feedback_payload,
                source="card_callback",
            )

        if not result.success:
            http_status = self._ERROR_STATUS_MAP.get(result.error_code, 400)
            raise HTTPException(status_code=http_status, detail=result.error)

        return ApiResponse(success=True, data=CardCallbackResponse.from_result(result))
```

### 6.6 DI 绑定

```python
# economy_governance_module.py 新增

@provider.singleton
def _card_callback_service(self) -> GovernanceCardCallbackService:
    return GovernanceCardCallbackService(
        feedback_svc=self._feedback_service(),
    )
```

---

## 7. 两阶段反馈流

### 7.1 适用场景

`dispute` 和 `whitelist` 在 spec 中要求 `remark` 必填，但卡片 UX 中用户可能只点了按钮未填理由。

### 7.2 流程图

```
用户在卡片点"不认可"
  │
  ├─ 填了理由 → response='dispute' → closed → 正式闭环
  │
  └─ 未填理由 → response='dispute_pending' → 保持 open →
                    卡片提示"请前往详情页补填理由"
                                    │
                    用户在前端页面补填理由 →
                    POST /notifications/{id}/resolve
                    response='dispute' → closed → 正式闭环
```

```
用户在卡片点"申请加白"
  │
  ├─ 填了理由 → response='whitelist' → closed → 自动加白 → 正式闭环
  │
  └─ 未填理由 → response='whitelist_pending' → 保持 open →
                    卡片提示"请前往详情页补填理由"
                                    │
                    用户在前端页面补填理由 →
                    POST /notifications/{id}/resolve
                    response='whitelist' → closed → 自动加白 → 正式闭环
```

### 7.3 pending 态规则

| 规则 | 说明 |
|---|---|
| `dispute_pending` / `whitelist_pending` 不写 `cooldown_until` | pending 态不触发冷却 |
| `dispute_pending` / `whitelist_pending` 不影响 `governance_status` | 保持 `open` |
| pending 态可升级为正式态 | 第二次调用 `resolve()` 时从 pending → 正式 |
| pending 态视为"未正式反馈" | 正式 response 已存在时重复请求返回已有结果 |
| `dispute` 和 `whitelist` 在卡片中**必须展示理由输入框** | 减少两阶段流转 |

### 7.4 FeedbackService 新增方法

```python
_PENDING_MESSAGES: dict[str, str] = {
    "dispute_pending": "异议已收到，请前往详情页补填理由以完成闭环",
    "whitelist_pending": "加白申请已收到，请前往详情页补填理由以完成闭环",
}

FORMAL_RESPONSES = {"optimized", "need_time", "dispute", "whitelist"}
PENDING_RESPONSES = {"dispute_pending", "whitelist_pending"}
SYSTEM_RESPONSES = {"resolved_by_system"}

def mark_pending_feedback(
    self,
    notification_id: str,
    pending_response: str,
    user_id: str,
    *,
    source: str = "card_callback",
) -> ResolveResult:
    """写入 pending 态反馈。pending 态不改变 governance_status，不写 cooldown_until。"""
    with self._db.orm_session() as session:
        log_row = (
            session.query(GovernanceNotifyLog)
            .filter(GovernanceNotifyLog.notification_id == notification_id)
            .first()
        )
        if not log_row:
            return ResolveResult(success=False, error="Notification not found", error_code="NOT_FOUND")

        if log_row.owner_id != user_id:
            return ResolveResult(success=False, error="Not notification owner", error_code="NOT_OWNER")

        # 幂等：已有正式 response → 返回当前真实状态
        if log_row.response in _FORMAL_RESPONSES | _SYSTEM_RESPONSES:
            return _result_from_log_row(log_row)

        # 幂等：已有同类 pending → 返回当前真实状态
        if log_row.response == pending_response:
            return _result_from_log_row(log_row, message=_pending_message(pending_response))

        # 已有不同 pending → 允许覆盖
        if log_row.response is not None and log_row.response not in _PENDING_RESPONSES:
            return ResolveResult(success=False, error="Notification already has a response",
                                 error_code="INVALID_RESPONSE")

        if pending_response not in _PENDING_RESPONSES:
            return ResolveResult(success=False, error=f"Invalid pending response: {pending_response}",
                                 error_code="INVALID_RESPONSE")

        now = datetime.now()
        log_row.response = pending_response
        log_row.response_at = now
        log_row.response_source = source
        # governance_status 保持 open，不写 cooldown_until

        session.add(GovernanceCheckAudit(
            run_id="card_callback", bot_id=log_row.bot_id, owner_id=log_row.owner_id,
            check_result="actionable", action_taken="user_resolved",
            source="card_callback", dry_run=0,
        ))

        try:
            session.commit()
        except Exception:
            log.exception("[GovernanceFeedback] mark_pending commit failed")
            session.rollback()
            return ResolveResult(success=False, error="Database error", error_code="DB_ERROR")

    return _result_from_log_row(log_row, message=_pending_message(pending_response))
```

### 7.5 ResolveResult 扩展

```python
@dataclass
class ResolveResult:
    success: bool = False
    notification_id: str = ""
    governance_status: str = ""
    close_reason: str | None = None
    mute_until: datetime | None = None
    error: str | None = None
    # 卡片回调需要的字段
    response: str = ""
    response_source: str = ""
    message: str | None = None
    # 结构化错误码（路由层据此映射 HTTP 状态码）
    error_code: str | None = None
```

### 7.6 error_code 值域

| error_code | 对应 HTTP | 产生场景 |
|---|---|---|
| `NOT_FOUND` | 404 | notification_id 不存在 |
| `NOT_OWNER` | 403 | user_id 与 owner_id 不匹配 |
| `INVALID_RESPONSE` | 400 | response 值不在允许集合中 |
| `MISSING_REMARK` | 400 | dispute/whitelist 缺少 remark |
| `MISSING_REPAIR_DEADLINE` | 400 | need_time 缺少 repair_deadline |
| `INVALID_FEEDBACK_PAYLOAD` | 400 | feedback_payload 不是合法 JSON |
| `ALREADY_RESOLVED` | — | 幂等返回，success=True |
| `DB_ERROR` | 500 | 数据库写入失败 |

---

## 8. LLM 构建提示词

> 以下提示词可直接粘贴给 LLM，用于生成治理通知卡片的交互式反馈 UI 组件。

```
你是一个治理通知卡片的 UI 构建助手。你需要根据治理通知数据，构建一个可交互的反馈表单 React 组件。

---

# 一、输入数据

你会收到一个 notification 数据对象，可能来自以下两种结构之一：

**结构 A（标准格式，来自 notification_structured）**：

```json
{
  "notification_id": "uuid4",
  "schema_version": "v1",
  "title": "Bot 成本优化建议: XXX",
  "meta": {
    "owner": "负责人姓名",
    "department": "组织路径",
    "daily_tokens": "2.11 亿",
    "daily_tokens_raw": 211135729.0,
    "optimization_summary": "中等, 主要来自...",
    "hit_dimensions": ["cron_high_freq", "low_efficiency"]
  },
  "problem_summary": "问题摘要文本",
  "action_items": [
    {
      "index": 1,
      "action": "建议标题",
      "what_to_change": "具体改什么",
      "why": "为什么要改",
      "expected_effect": "预期效果",
      "needs_owner_confirm": true
    }
  ],
  "disclaimer": "免责声明"
}
```

统一读取函数 `getNoticeData()` 实现多路降级取数（见本文档第5节）。

---

# 二、你要构建的 UI 结构

整个页面分为 4 个区域：信息展示区 → 逐项反馈区 → 整体决策区 → 提交区。

## 区域 1：信息展示区

展示通知核心信息：
- 标题（title）+ 严重程度徽章（hit_dimensions_count ≥ 3 为 HIGH，2 为 MEDIUM，1 为 LOW）
- 负责人（owner）、组织（department / organization）
- 关键指标（3列网格）：日均 Token / 优化潜力 / 优化率
- 问题摘要（problem_summary / summary）

## 区域 2：逐项反馈区 ★核心区域

将 action_items 逐条展示为可勾选的反馈卡片。**每条建议必须**：

1. ★ **显示 `#index` 编号**——后端回溯的唯一 key
2. 显示建议标题 + 详情
3. 若 `needs_owner_confirm=true`，显示 ⚠️ 需确认
4. 提供 **3 个态度按钮**：✅ 接受 / 🔶 部分 / ❌ 拒绝
5. 选中"拒绝"或"部分"时，自动展开备注输入框
6. 进度统计：`已评价 X/N 项`

## 区域 3：整体决策区

4 个按钮：

| 按钮 | response 值 | 选中后需展示的条件字段 |
|------|------------|---------------------|
| ✅ 已优化 | `optimized` | 无 |
| ⏳ 需时间 | `need_time` | 修复截止日期（必填） |
| ❌ 不认可 | `dispute` | 理由（必填） |
| 📋 申请加白 | `whitelist` | 理由（必填） |

补充说明（overall_remark）：始终展示的可选文本框。

## 区域 4：提交区

校验规则：
- response 必须已选择
- dispute/whitelist：remark 必填
- need_time：repair_deadline 必填
- 逐项评价不做强制——未评价的 = skipped

---

# 三、提交数据格式

POST 到 `callbackUrl`，请求体：

```json
{
  "notification_id": "从数据中取",
  "response": "optimized | need_time | dispute | whitelist",
  "remark": "dispute/whitelist 时的理由",
  "repair_deadline": "need_time 时的 ISO 日期",
  "feedback_payload": {
    "version": 1,
    "overall_action": "accepted | rejected | partial",
    "overall_remark": "补充说明",
    "items": [
      {"index": 1, "action": "accepted", "remark": null},
      {"index": 2, "action": "rejected", "remark": "原因"}
    ]
  }
}
```

**items[].index 规则**：
- 必须与 action_items[].index 完全一致
- 只发送用户已评价的项，未评价的不出现

**overall_action 推导**：见本文档 §4.5

**提交双通道**：fetch POST 到 callbackUrl + window.aixBridge.submit()

---

# 四、提交后状态

提交成功后替换为确认卡片：
- ✅ "反馈已提交" + response 中文标签
- 逐项评价摘要：`#1 ✅ | #2 ❌ 业务需要 | #3 ✅`

---

# 五、样式要求

- 卡片圆角阴影，背景白色，padding 24px，圆角 24px
- 严重程度：HIGH=红🔴，MEDIUM=橙🟡，LOW=绿🟢
- 逐项态度：接受=绿，部分=橙，拒绝=红，未选=灰
- 整体决策：已优化=绿，需时间=蓝，不认可=红，申请加白=灰
- 响应式：适配移动端窄屏，≥4 条时详情默认折叠

---

# 六、数据获取与 URL 参数解析

见本文档第 5 节的完整实现代码。

---

# 七、测试用数据

```json
{
  "notification_id": "ntf_20260625_001",
  "schema_version": "v1",
  "title": "Bot 成本优化建议: 体验哨兵个人版",
  "meta": {
    "owner": "莺柒",
    "department": "蚂蚁集团-大安全-资金风险管理部-安全体验-统一决策中心",
    "daily_tokens": "2.11 亿",
    "daily_tokens_raw": 211135729.0,
    "optimization_summary": "中等, 主要来自单次执行和异常长尾优化",
    "hit_dimensions": ["cron_high_freq"]
  },
  "problem_summary": "日均2.1亿Token中84%来自手动Session...",
  "action_items": [
    {
      "index": 1,
      "action": "复盘44MToken超长手动Session",
      "what_to_change": "设置手动Session轮次上限或超时机制",
      "why": "采样显示存在473轮、44MToken的无产出会话",
      "expected_effect": "降低长尾Session成本",
      "needs_owner_confirm": true
    },
    {
      "index": 2,
      "action": "按需加载extreme-experience-llm SKILL文档",
      "what_to_change": "极端体验分析任务上下文加载逻辑",
      "why": "单次97K Token可能存在文档重复加载",
      "expected_effect": "减少文档加载相关Token",
      "needs_owner_confirm": false
    },
    {
      "index": 3,
      "action": "增加last_processed_time游标，只处理新增超时任务",
      "what_to_change": "HITL超时兜底Cron入口",
      "why": "当前403次/天全量扫描，大部分时段无待处理任务",
      "expected_effect": "减少空跑消耗",
      "needs_owner_confirm": false
    }
  ]
}
```

期望提交 payload 示例（用户接受 #1 和 #3，拒绝 #2，选择"需时间"）：

```json
{
  "notification_id": "ntf_20260625_001",
  "response": "need_time",
  "remark": null,
  "repair_deadline": "2026-07-15",
  "feedback_payload": {
    "version": 1,
    "overall_action": "partial",
    "overall_remark": "第2项需要评估影响",
    "repair_deadline": "2026-07-15",
    "items": [
      {"index": 1, "action": "accepted", "remark": null},
      {"index": 2, "action": "rejected", "remark": "文档加载已是最优方式"},
      {"index": 3, "action": "accepted", "remark": null}
    ]
  }
}
```
```

---

## 9. 测试文档

### 9.1 测试环境准备

```bash
# 启动后端（持久化 SQLite）
./scripts/singlebox.sh -l restart backend
```

插入测试通知：

```sql
INSERT INTO ac_governance_notify_log (
  notification_id, bot_id, bot_name, owner_id, entity_id,
  worker_id, dt_version, governance_decision, governance_cycle_id,
  governance_status, notify_status, latest_decision,
  consecutive_normal_days, remind_count, send_attempt_count,
  gmt_create
) VALUES (
  'ntf_card_test_001', 'bot-sentinel', '体验哨兵个人版', 'yangping.lyp', 'yangping.lyp',
  'yangping.lyp:bot-sentinel', '20260701', 'actionable', 'cycle-20260701',
  'open', 'sent', 'actionable',
  0, 1, 1,
  datetime('now')
);
```

验证 DB：

```bash
sqlite3 scripts/.dependences/data/backend.db \
  "SELECT notification_id, response, response_source, governance_status, close_reason, feedback_payload
   FROM ac_governance_notify_log WHERE notification_id='ntf_card_test_001';"
```

### 9.2 测试用例

#### TC-01: 已优化 → closed

```bash
curl -s -X POST http://localhost:8888/api/economy/governance/card-callback \
  -H 'Content-Type: application/json' \
  -H 'Cookie: staff_id=test_user' \
  -d '{
    "notification_id": "ntf_card_test_001",
    "response": "optimized",
    "remark": null,
    "repair_deadline": null,
    "feedback_payload": {
      "version": 1,
      "overall_action": "accepted",
      "items": [
        {"index": 1, "action": "accepted", "remark": null},
        {"index": 2, "action": "accepted", "remark": null}
      ]
    }
  }'
```

**期望**：`governance_status=closed, close_reason=user_optimized, response_source=card_callback`

#### TC-02: 需时间 → muted

```bash
curl -s -X POST http://localhost:8888/api/economy/governance/card-callback \
  -H 'Content-Type: application/json' \
  -H 'Cookie: staff_id=test_user' \
  -d '{
    "notification_id": "ntf_card_test_001",
    "response": "need_time",
    "remark": null,
    "repair_deadline": "2026-07-15",
    "feedback_payload": {
      "version": 1,
      "overall_action": "partial",
      "overall_remark": "第2项需评估",
      "repair_deadline": "2026-07-15",
      "items": [
        {"index": 1, "action": "accepted", "remark": null},
        {"index": 2, "action": "rejected", "remark": "业务需要"},
        {"index": 3, "action": "accepted", "remark": null}
      ]
    }
  }'
```

**期望**：`governance_status=muted, mute_until=2026-07-22`

#### TC-03: 不认可 + 理由 → closed

```bash
curl -s -X POST http://localhost:8888/api/economy/governance/card-callback \
  -H 'Content-Type: application/json' \
  -H 'Cookie: staff_id=test_user' \
  -d '{
    "notification_id": "ntf_card_test_001",
    "response": "dispute",
    "remark": "检测结果不准确",
    "repair_deadline": null,
    "feedback_payload": {
      "version": 1,
      "overall_action": "rejected",
      "items": [
        {"index": 1, "action": "rejected", "remark": "不需要"}
      ]
    }
  }'
```

**期望**：`governance_status=closed, close_reason=user_disputed`

#### TC-04: 不认可 无理由 → pending

```bash
curl -s -X POST http://localhost:8888/api/economy/governance/card-callback \
  -H 'Content-Type: application/json' \
  -H 'Cookie: staff_id=test_user' \
  -d '{
    "notification_id": "ntf_card_test_001",
    "response": "dispute",
    "remark": null,
    "repair_deadline": null,
    "feedback_payload": null
  }'
```

**期望**：`response=dispute_pending, governance_status=open, message="异议已收到..."`

#### TC-05: pending 重复提交 → 幂等

同 TC-04 请求，期望响应完全一致。

#### TC-06: pending → 正式升级

```bash
curl -s -X POST http://localhost:8888/api/economy/governance/notifications/ntf_card_test_001/resolve \
  -H 'Content-Type: application/json' \
  -H 'Cookie: staff_id=test_user' \
  -d '{
    "response": "dispute",
    "remark": "现在是真正的不认可理由",
    "feedback_payload": {"version": 1, "overall_action": "rejected",
      "items": [{"index": 1, "action": "rejected", "remark": "补充"}]}
  }'
```

**期望**：`response=dispute, governance_status=closed`

#### TC-07: 申请加白 无理由 → pending

同 TC-04 模式，`response=whitelist_pending`

#### TC-08: 申请加白 + 理由 → closed + 加白

**期望**：`close_reason=user_whitelisted`，`ac_bot_whitelist` 新增记录

#### TC-09: pending 互相覆盖

先 `dispute_pending`，后 `whitelist_pending`，最终 `response=whitelist_pending`

#### TC-10: need_time 缺 repair_deadline → 400

#### TC-11: 无效 response → 400

#### TC-12: 不存在的通知 → 404

#### TC-13: 无 Cookie → 401

#### TC-14: 已关闭通知重复提交 → 幂等 200

#### TC-15: feedback_payload 不可序列化 → 400

#### TC-16: 审计记录验证

```bash
sqlite3 scripts/.dependences/data/backend.db \
  "SELECT run_id, bot_id, owner_id, source, action_taken FROM ac_governance_check_audit ORDER BY id DESC LIMIT 5;"
```

**期望**：`run_id=card_callback, source=card_callback, action_taken=user_resolved`

#### TC-17: 现有 resolve 端点 error_code 改造验证

17a: `NOT_FOUND → 404`，17b: `INVALID_RESPONSE → 400`

### 9.3 推荐测试顺序

| 顺序 | 用例 | 前提 | 验证要点 |
|---|---|---|---|
| 1 | TC-04 | open 通知 | pending 写入、status 保持 open |
| 2 | TC-05 | dispute_pending | 重复提交幂等 |
| 3 | TC-06 | dispute_pending | 升级为 closed |
| 4 | 重新插入 open 通知 | — | — |
| 5 | TC-01 | open 通知 | closed + cooldown |
| 6 | TC-16 | TC-01 已执行 | 审计记录 |
| 7 | TC-14 | closed | 幂等 200 |
| 8 | 重新插入 | — | — |
| 9 | TC-02 | open 通知 | muted |
| 10 | 重新插入 | — | — |
| 11 | TC-08 | open 通知 | closed + 加白 |
| 12–20 | TC-07/09/10~13/15/17 | — | 边界/错误码 |

### 9.4 单元测试

```
tests/core/economy/governance/test_card_callback.py    # 18 个用例
tests/core/economy/governance/test_feedback.py         # 9 个用例（含 mark_pending）
```

```bash
cd src/backend && uv run pytest tests/core/economy/governance/ -v
```

覆盖率：

| 变更项 | 手工 TC | 单元测试 |
|---|---|---|
| ResolveResult 新字段 | TC-01/TC-04 | ✅ |
| `mark_pending_feedback()` | TC-04/05/07/09 | ✅ 5 个 |
| resolve() error_code | TC-10/11/12/15 | ✅ 5 个 |
| GovernanceCardCallbackService | 所有 TC | ✅ 5 个 |
| 幂等返回用 `_result_from_log_row` | TC-14 | ✅ |

> **17 个手工 TC + 18 个单元测试，完整覆盖全部变更。**

---

## 10. 完整映射表

### 10.1 LLMComponent v3 → spec resolve 参数

| LLMComponent v3 state | spec `resolve()` 参数 | 逻辑 |
|---|---|---|
| `feedback.response` | `response` | 直传 |
| `feedback.remark` | `remark` | 直传 |
| `feedback.repair_deadline` | `repair_deadline` | 直传 |
| `feedback.overall_remark` | `feedback_payload.overall_remark` | 嵌入 payload |
| `itemFeedbacks Map` → `[{index, action, remark}]` | `feedback_payload.items[]` | **index 是 key** |
| `deriveOverallAction()` | `feedback_payload.overall_action` | 自动推导 |
| 固定 `1` | `feedback_payload.version` | 固定值 |
| `notice.notification_id` | `notification_id` | 直传 |

### 10.2 所有 response × 逐条反馈组合

| response | items 状态 | overall_action | governance_status | close_reason |
|---|---|---|---|---|
| `optimized` | 全 accepted | `accepted` | `closed` | `user_optimized` |
| `optimized` | 空 | `accepted` | `closed` | `user_optimized` |
| `optimized` | 混合 | `partial` | `closed` | `user_optimized` |
| `need_time` | 全 accepted | `accepted` | `muted` | — |
| `need_time` | 空 | `accepted` | `muted` | — |
| `need_time` | 混合 | `partial` | `muted` | — |
| `dispute` | 全 rejected | `rejected` | `closed` | `user_disputed` |
| `dispute` | 空 | `rejected` | `closed` | `user_disputed` |
| `dispute`（缺 remark）| 任意 | `rejected` | `open`（pending） | — |
| `whitelist` | 空 | `rejected` | `closed` | `user_whitelisted` |
| `whitelist`（缺 remark）| 空 | `rejected` | `open`（pending） | — |

---

## 11. 验证检查清单

- [ ] TC 卡片使用 AI 工作台官方账号 (`dingyboqbe20cac7exdp`) 发送
- [ ] 治理通知模板 (`bc2d6541`) reason 支持 Markdown 渲染
- [ ] 逐条反馈：所有 `action_items` 均可独立选择态度
- [ ] overall_action 自动推导：全 accepted → accepted，混合 → partial
- [ ] response 4 按钮对齐 spec
- [ ] 条件必填校验：dispute/whitelist → remark 必填；need_time → repair_deadline 必填
- [ ] 两阶段反馈：dispute/whitelist 缺 remark → pending 态
- [ ] whitelist 正式 response 触发自动加白
- [ ] `needs_owner_confirm` 标记展示
- [ ] `notification_id` 关联正确
- [ ] `response_source` 正确写入：`card_callback`
- [ ] `feedback_payload` JSON 结构与 §3.2 一致
- [ ] 回调接口兼容现有 `GovernanceFeedbackService.resolve()`
- [ ] 双通道互换：tc_card ↔ markdown 自动降级
- [ ] 所有 17 个手工 TC 通过