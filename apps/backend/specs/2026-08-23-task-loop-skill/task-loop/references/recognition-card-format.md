# 任务卡片 · 数据格式

> Skill 输出统一走 AixUI 卡片格式，平台层通过 JSON body 中的 "type" 字段渲染对应 UI 组件。
> 通用格式：`<AixUI cardId='card_xxxxx'>JSON数据（含"type"字段）</AixUI>`

---

## 一、三种卡片总览

| type 值 | 触发时机 | 用途 |
|---|---|---|
| `task_clarify` | 四要素不全，需追问 | 追问用户缺失的要素 |
| `task_multi_select` | 识别到多个任务 | 让用户选择先做哪个 |
| `task_ready` | 四要素全齐，草案完整 | 确认执行/丢弃/暂存 |

---

## 二、task_clarify · 追问卡片

### 输出格式

```
<AixUI cardId='card_a1b2c3'>
{
  "type": "task_clarify",
  "goal": "修复 PR #1243 的命名问题",
  "deliverables": ["代码 PR（命名修正）"],
  "acceptance_criteria": [],
  "constraints": [],
  "task_type": "dynamic",
  "resources": [],
  "missing_fields": ["acceptance_criteria", "constraints"],
  "needs_confirmation": ["deliverables"],
  "questions": [
    "我推断交付物是代码 PR，对吗？",
    "验收标准是以下哪个？A) CI 通过  B) Review 通过  C) 测试通过  D) 其他",
    "有什么约束吗？（如不动接口、指定分支等）"
  ]
}
</AixUI>
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | string | ✅ | 固定 `"task_clarify"` |
| `goal` | string | ✅ | 当前已提取的目标，可能为空 `""` |
| `deliverables` | string[] | ✅ | 当前提取/推断的交付物，空为 `[]` |
| `acceptance_criteria` | string[] | ✅ | 当前提取/推断的验收标准，空为 `[]` |
| `constraints` | string[] | ✅ | 当前提取/推断的约束，空为 `[]` |
| `task_type` | string | ✅ | 任务类型：`"dynamic"` 或 `"workflow"` |
| `workflow_id` | string | 条件必填 | 仅 `task_type="workflow"` 时必填；动态任务不输出 |
| `resources` | string[] | ❌ | 用户附带的关联资源链接/文档/PR/Bug 等；不放 `workflow_id`，无则 `[]` |
| `missing_fields` | string[] | ✅ | 仍缺失需追问的要素名 |
| `needs_confirmation` | string[] | ✅ | 推断值需用户确认的要素名，无则 `[]` |
| `questions` | string[] | ✅ | 向用户提出的问题列表 |

### 卡片 UI 布局

```
┌──────────────────────────────────────────────────────┐
│                                                       │
│  📋 任务创建中                                        │
│                                                       │
│  目标                                                 │
│  修复 PR #1243 的命名问题                             │
│                                                       │
│  📌 交付物                                            │
│  • 代码 PR（命名修正）  💡推断待确认                  │
│                                                       │
│  ❓ 需要确认                                          │
│  我推断交付物是代码 PR，对吗？                        │
│  验收标准是以下哪个？                                 │
│    A) CI 通过  B) Review 通过  C) 测试通过  D) 其他   │
│  有什么约束吗？（如不动接口、指定分支等）             │
│                                                       │
│  （用户在对话中回答即可）                             │
│                                                       │
└──────────────────────────────────────────────────────┘
```

---

## 三、task_multi_select · 多任务选择卡片

### 输出格式

```
<AixUI cardId='card_d4e5f6'>
{
  "type": "task_multi_select",
  "tasks": [
    {"index": 1, "summary": "修复 PR #1243 的命名问题"},
    {"index": 2, "summary": "生成本月月报"}
  ],
  "prompt": "你想先做哪个？"
}
</AixUI>
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | string | ✅ | 固定 `"task_multi_select"` |
| `tasks` | object[] | ✅ | 任务列表 |
| `tasks[].index` | number | ✅ | 序号 |
| `tasks[].summary` | string | ✅ | 任务概要（一句话） |
| `prompt` | string | ✅ | 选择提示语 |

### 卡片 UI 布局

```
┌──────────────────────────────────────────────────────┐
│                                                       │
│  📋 识别到 2 个任务                                   │
│                                                       │
│  ① 修复 PR #1243 的命名问题        [选择]             │
│  ② 生成本月月报                    [选择]             │
│                                                       │
│  你想先做哪个？                                       │
│                                                       │
│  还有更多任务，建议分批发起。（超5个时显示）         │
│  以上是我识别到的任务，还有遗漏吗？（超3个时显示）   │
│                                                       │
└──────────────────────────────────────────────────────┘
```

---

## 四、task_ready · 任务就绪确认卡片

### 输出格式

```
<AixUI cardId='card_g7h8i9'>
{
  "type": "task_ready",
  "task": {
    "task_type": "dynamic",
    "goal": "修复 PR #1243 的命名问题",
    "deliverables": ["代码 PR（命名修正）"],
    "acceptance_criteria": ["CI 通过"],
    "constraints": ["不动接口逻辑"],
    "resources": [
      "https://git.example.com/pr/1243",
      "https://doc.example.com/prd#3.2"
    ]
  },
  "needs_confirmation": ["deliverables"],
  "actions": ["execute", "discard", "save"]
}
</AixUI>
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | string | ✅ | 固定 `"task_ready"` |
| `task.task_type` | string | ✅ | 任务类型：`"dynamic"` 或 `"workflow"` |
| `task.workflow_id` | string | 条件必填 | 仅 `task.task_type="workflow"` 时必填；动态任务不输出 |
| `task.goal` | string | ✅ | 一句话目标，卡片标题 |
| `task.deliverables` | string[] | ✅ | 交付物列表 |
| `task.acceptance_criteria` | string[] | ✅ | 验收标准列表 |
| `task.constraints` | string[] | ✅ | 约束列表 |
| `task.resources` | string[] | ❌ | 关联资源链接/文档/PR/Bug 等；不放 `workflow_id`，无则 `[]` |
| `needs_confirmation` | string[] | ✅ | 推断值需确认的要素名，无则 `[]` |
| `actions` | string[] | ✅ | 固定 `["execute", "discard", "save"]` |

### needs_confirmation 取值

可选值：`"goal"` / `"deliverables"` / `"acceptance_criteria"` / `"constraints"`。resources 永远不出现。

### 卡片 UI 布局

#### 有 resources + 有 needs_confirmation

```
┌──────────────────────────────────────────────────────┐
│                                                       │
│  📋 任务已就绪                                        │
│                                                       │
│  目标                                                 │
│  修复 PR #1243 的命名问题                             │
│                                                       │
│  📌 交付物                                            │
│  • 代码 PR（命名修正）  💡推断待确认                  │
│                                                       │
│  ✅ 验收标准                                          │
│  • CI 通过                                            │
│                                                       │
│  ⚠️ 约束                                              │
│  • 不动接口逻辑                                       │
│                                                       │
│  🔗 关联资源                                          │
│  • https://git.example.com/pr/1243   ↗               │
│  • https://doc.example.com/prd#3.2   ↗               │
│                                                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐           │
│  │   执行    │  │   丢弃    │  │   暂存    │           │
│  └──────────┘  └──────────┘  └──────────┘           │
│                                                       │
│  💡 需要修改？直接在对话中告诉我，如"约束改成今天内"  │
│                                                       │
└──────────────────────────────────────────────────────┘
```

#### 无 resources

🔗 关联资源 整块不展示。

#### 无 needs_confirmation

所有字段无 💡 标注，干净展示。

### workflow_id 兼容规则

新格式中，`workflow_id` 是结构化字段，不再放入 `resources`。为兼容历史草稿/历史卡片：

- 若读取到旧格式 `resources: ["workflow_id: wf_xxx"]`，平台应提取 `wf_xxx` 到 `task.workflow_id`
- 同时将 `task.task_type` 归一化为 `"workflow"`
- 从展示型 `resources` 中移除该项，避免把 workflow_id 当关联资源展示
- 用户点击「执行」时，优先读取 `task.workflow_id`；只有结构化字段缺失时才从旧格式 `resources` 兜底解析

---

## 五、各区域样式说明

### 目标区
- 位置：卡片顶部
- 样式：加粗，较大字号

### 交付物 / 验收标准 / 约束区
- 前缀图标：📌 / ✅ / ⚠️
- 多项：每项一行，前面带 `•`
- 💡 标注：在 needs_confirmation 中的要素，每项后加 `💡推断待确认` 小字 + 浅黄背景

### 关联资源区
- 显示条件：`resources` 非空时展示，为空整块不显示
- 前缀图标：🔗
- URL 链接可点击，末尾加 ↗ 表示可跳转；非 URL 资源按普通文本展示
- `workflow_id` 不是关联资源，不在本区展示；它作为工作流任务的结构化字段随卡片数据传递

### 按钮区（仅 task_ready）
| 按钮 | 样式 | 行为 |
|---|---|---|
| 执行 | 主色填充 | 调用 `POST /api/v1/collaboration/tasks/execute` 创建并执行任务 |
| 丢弃 | 灰色描边 | 删除任务 |
| 暂存 | 灰色描边 | 保存完整草稿 |

### 修改提示（仅 task_ready）
卡片底部：`💡 需要修改？直接在对话中告诉我，如"约束改成今天内"`

---

## 六、用户操作回传

| 操作 | 触发方式 | 适用卡片 | 平台层行为 | 是否调用 Skill |
|---|---|---|---|---|
| 回答追问 | 在对话中回答 | task_clarify | 传给 Skill 更新草案 | 是 |
| 选择任务 | 点任务项 | task_multi_select | 对选中任务调 Skill 走追问 | 是 |
| 执行 | 点「执行」按钮 | task_ready | 平台转换为 `TaskRequest` 并调用 `POST /api/v1/collaboration/tasks/execute` | 否 |
| 丢弃 | 点「丢弃」按钮 | task_ready | 删除任务 | 否 |
| 暂存 | 点「暂存」按钮 | task_ready | 保存完整草稿 | 否（回传时才调） |
| 修改 | 在对话中说明 | task_ready | 传给 Skill 更新草案 → 重新输出卡片 | 是 |
| 继续暂存 | 用户回来说"继续" | — | 注入 [RESUME_TASK] + 完整草稿 → Skill → task_ready | 是 |

### 执行按钮接口调用

`task_ready` 卡片中用户点击「执行」后，平台层不再调用 Skill，而是将 `task_ready.task` 转换为接口文档中的 `TaskRequest`，调用：

```http
POST /api/v1/collaboration/tasks/execute
```

- 动态任务 `/task xxx需求描述`：`execution_config.task_type = "dynamic"`
- 工作流任务 `/task workflow_id='xxxxxxx' xxx需求描述`：`task.task_type = "workflow"`，`task.workflow_id = "xxxxxxx"`；执行时设置 `execution_config.task_type = "workflow"`、`execution_config.workflow_id = task.workflow_id`
- execute 成功返回后，使用 `data.task_id` 打开任务副屏，并查询 `GET /api/v1/collaboration/tasks/dashboard?task_id=<task_id>` 更新进度
