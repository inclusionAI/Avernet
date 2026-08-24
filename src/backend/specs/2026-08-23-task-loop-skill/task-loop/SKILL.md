---
name: task-loop
description: 任务目标驱动执行闭环预装 skill,整合任务识别/规划/派发搜推/验收/BBS 接力/arch 场景规划变体(planning-arch)与架构师名册 mock(arch-analysis)共七段为单一 skill,预装到所有 bot 等同各段单独安装到对应 bot;各段按各自触发词自门控仅命中段执行(用户面 /task 或 [RESUME_TASK] 或副屏标签命中识别;框架 [planning] 命中规划,arch 场景含「某某某公司」命中 planning-arch 变体;框架 [search] 命中派发搜推;worker 叶子自验收命中验收;引擎 BBS 通知命中接力,其 scoped 叶子 instruction 含「某某某公司」时按 arch-analysis 产架构师名册)。
version: 1.0.0
author: avernet-task-framework
tags: [task, loop, orchestrate, task-recognition, task-planning, task-search, task-acceptance, bbs-relay, arch-analysis, task-planning-arch]
---

# task-loop

预装到所有 bot 的任务目标驱动执行闭环 skill。本 skill 内含七段,**只执行被触发词命中的那一段**,其余段不参与;给任一 bot 预装本 skill 等同把对应段单独安装到该 bot。

- 任务识别(recognition):对话 bot 用户面——`/task` 或平台 `[RESUME_TASK]` 回传或仅副屏标签
- 任务规划(planning):owner bot——框架 prompt 头部 `[planning]`(非 arch 场景)
- 任务规划·arch 场景(planning-arch):owner bot——框架 `[planning]` 且 prompt 含「某某某公司」,按根验收交付物集合 + done_children 确定式查表
- 任务派发搜推(search):owner bot——框架 prompt 头部 `[search]`
- 任务验收(acceptance):协作群 driver/owner bot——群产出后自验收并 push 上报(single_bot 叶子由框架内联 JSON 走 poll,不走本段)
- BBS 接力(bbs-relay-single-task):中继 bot——引擎主动通知
- 架构师名册 mock(arch-analysis):中继/worker bot——叶子 instruction 含「某某某公司」,返伪造架构师名册

段体取自各段真源 SKILL.md,仅标题层级统一降一级并入;各段逻辑 / 规则 / 卡片格式 / 触发与输出契约保持原样。先读下面"路由规则"确定本该执行哪段,再只跑那一段。

## 路由规则(最先读,只跑命中段)

按**本次收到的触发词 / 上下文**分流(不按 bot 身份;同一 bot 按其收到的触发执行对应段):

| 触发 / 上下文 | 命中段 | 执行要点 |
|---|---|---|
| 用户消息以 `/task` 开头;或上下文含 `[RESUME_TASK]`;或消息仅 `<AixUI type="panel" component="task-loop" ...>` 副屏标签 | 段1 任务识别 | 出 AixUI 卡片(cardId 固定 card_3e31e1f1),到 task_ready 为止;执行由平台层调 POST /api/v1/collaboration/tasks/execute |
| prompt 头部标记 `[planning]`,含目标节点 node_id 与任务态快照,**且 prompt 不含「某某某公司」**(非 arch 场景) | 段2 任务规划 | 返回 JSON 对象 {tasks: List[TaskSpec], has_gap, gap_detail};tasks 为空即 gap 闭=验收通过 |
| prompt 头部标记 `[planning]`,**且 prompt 含「某某某公司」**(arch 场景;交付物含架构师名册/技术栈概览/双视角分析等) | 段7 任务规划·arch 场景 | 同段2 输出契约;按根验收交付物集合 + done_children 确定式查表产 N_tech_stack / N_dual_view / N_architects |
| prompt 头部标记 `[search]`,含子任务需求与候选集 catalog | 段3 任务派发搜推 | 返回 4 态 JSON(HIT_SINGLE / HIT_GROUP / HIT_MULTI_BOTS / MISS) |
| 你是协作群 driver/owner bot,群已跑完叶子并产出交付物,需按其 goal.acceptances 自验收并上报(从群上下文取 {backend}/{loop_task_id}) | 段4 任务验收 | push:POST {backend}/api/v1/collaboration/tasks/callback/report {loop_task_id,result{success,data,gaps}}→on_report;single_bot 叶子不走本段(框架内联 JSON→poll) |
| 收到引擎主动发的 BBS 任务消息(含 task_id + 后端 base url + 自身 bot_id,且引擎已替你占根) | 段5 BBS 接力 | 跳过 scan / claim / 自判,直接 attach -> 执行 -> result |
| 叶子执行输入(instruction)含关键词「某某某公司」,且非框架 `[planning]`/`[search]` 头 | 段6 架构师名册 mock | 不联网,直接返 mock 伪造架构师名册 JSON(domain/architects[]/note) |

**优先级**(实际多互斥,显式定义避免歧义):段5(引擎 BBS 通知) > 段7/段2/段3(框架 `[planning]`/`[search]` 头;arch 场景「某某某公司」命中段7,优先于段2) > 段4(协作群 driver/owner 验收+push) > 段1(`/task` / `[RESUME_TASK]` / 副屏标签) > 段6(叶子 instruction 含「某某某公司」)。

**段5 ↔ 段6 并用(arch 接力链路唯一例外)**:段5 命中(BBS 通知)时,其 attach 的 scoped 叶子若 instruction 含「某某某公司」,叶子产出按**段6 arch-analysis** 规则(mock 名册),段5 仍管 attach / result 协议。除此之外严格"只跑命中段"。

**未命中任何段**:静默结束本轮——不虚构任务、不追问、不输出卡片、不乱执行(相当于 return / no-op)。反例:`[planning]` / `[search]` 是框架执行期 prompt,不是用户任务提交,不要走段1。

**只跑命中段**:命中某段即按该段全部规则执行,其余段规则不参与(段5↔段6 arch 接力并用为唯一例外)。

## 场景叠加层(泛化主干 + 演示案例优化)

本 skill 分两层,在保证泛化的同时承载演示案例的针对性优化:

- **泛化主干**:段1~段5(识别 / 规划 / 派发搜推 / 验收 / BBS 接力),默认行为,段体逐字节取自通用真源、不可改;未命中任何案例信号时即纯主干,可处理任意任务(泛化性来源)。
- **案例叠加段**:段6 arch-analysis / 段7 planning-arch,由**案例信号**(当前为 prompt/instruction 含「某某某公司」)门控,命中才激活——段7 在 arch 场景覆盖段2(同输出契约,换确定式剧本),段6 为 arch 专用叶子执行器(段5 接力时并用)。未命中案例信号 → 叠加段不参与,主干照常运行。
- **扩展契约**:新增演示案例 = 追加其叠加段 + 注册其案例信号(强特征关键词/上下文),不改段1~段5;移除全部叠加段即回退为纯泛化 task-loop。案例信号须用强特征(如「某某某公司」)以免误命中通用任务。

> 泛化性由主干保证(默认 + 不可改);针对性优化由叠加段提供(信号门控、可插拔、零侵入主干)。

> 段1 · 任务识别(recognition;触发 /task 或 [RESUME_TASK] 或仅副屏标签)

## 任务提交与任务草案生成

### 总述

本 Skill 负责：接收用户的任务提交指令，追问四要素，生成任务草案，完成任务提交。

不负责：触发环节的上游交互（按钮 UI、slash 命令解析）、自然语言任务意图识别、确认卡片 UI 展示（平台层接管）、任务执行接口调用的实际网络实现（平台层负责调用任务协作中心接口）、执行手段编排（orchestrate Skill 负责）。

---

### 调用模式

根据上下文中的标记和用户消息内容，进入不同路径：

**路径1 · 升级版 Slash 指令（最高优先级）**
用户消息以 `/task` 开头 → 直接进入任务提交流程；先做多任务识别，单任务再进入四要素追问。

升级后的 `/task` 指令支持两种消息指令：
- **动态任务消息指令**：`/task xxx需求描述`
  - `/task` 后面直接跟需求描述
  - 默认识别为动态任务
  - slash 后的需求描述作为 goal 的提取起点，并继续按四要素提取/追问
- **工作流任务消息指令**：`/task workflow_id='xxxxxxx' xxx需求描述`
  - `/task` 后第一个参数为 `workflow_id='xxxxxxx'` 时，识别为工作流任务
  - `workflow_id` 写入结构化字段 `workflow_id`，不要写入 `resources`
  - `workflow_id` 之后的文本作为需求描述，继续按四要素提取/追问
  - 工作流任务输出时必须携带 `task_type: "workflow"` 和 `workflow_id: "xxxxxxx"`；动态任务输出时携带 `task_type: "dynamic"`，不需要 `workflow_id`
- `/task` — 仅 slash 无描述，goal 留空进入 missing_fields，追问"你要做什么任务？"

指令解析规则：
- `/task` 只识别消息开头的命令前缀，不识别普通自然语言里的“发起任务/做个任务/转任务”
- 只有紧跟 `/task` 的第一个参数符合 `workflow_id='xxxxxxx'` 时，才识别为工作流任务；否则全部按动态任务描述处理
- `workflow_id` 支持单引号格式；若缺少 `workflow_id` 值或缺少后续需求描述，缺失部分进入追问，不凭空推断

**路径4 · 平台层回传暂存任务**
上下文含 `[RESUME_TASK]` 标记 + 任务信息 → 这是多任务暂存回传或用户暂存草稿回传的任务。
- 跳过判断、不输出清单
- 读取注入的任务信息，判断四要素是否完整（resources 不参与判断）：
  - **四要素全齐**（暂存草稿回传）→ 直接输出 task_ready 确认卡片，不追问
  - **四要素不全**（多任务暂存回传，只有 goal 概要）→ 走四要素追问

#### 副屏消息静默处理

当用户消息**仅包含** `<AixUI type="panel" component="task-loop" ...>` 标签（或仅有该标签 + 极少量无关空白）、无其他文本内容时，这是平台层在任务执行成功后发出的副屏展示指令，**不是用户的对话输入**。该消息可能以 inject 方式发送，也可能以普通消息方式发送。

处理规则：
- **不回复任何内容**，不输出卡片，不追问，不输出文本
- 静默结束本轮处理（相当于 `return` / no-op），不让用户感知到 bot 被触发
- 不要将此消息当作任务提交、澄清、暂存回传或任何流程的入口
- 不要对此消息做意图识别、四要素提取或多任务判断

判断方法：消息内容去掉首尾空白后，以 `<AixUI` 开头、以 `</AixUI>` 结尾，且包含 `type="panel"` 和 `component="task-loop"` → 即为副屏展示消息，静默处理。

#### 非入口说明

以下内容不再触发本 Skill 的任务提交流程：
- 平台标记 `[EXPLICIT_TASK]`
- 用户仅在自然语言中说"发起任务""做个任务""转任务"等声明性表达，但没有 `/task` 前缀

#### 优先级

若同一上下文同时出现 `/task` 和 `[RESUME_TASK]`，按优先级处理：路径1（/task）> 路径4（[RESUME_TASK]）。

---

### 四要素追问与草案生成

#### 任务四要素 + 可选关联资源

**必填四要素（缺则追问，全齐则输出 task_ready 确认卡片）**：

| 要素 | 说明 | 示例 |
|---|---|---|
| **目标（goal）** | 一句话说清要达成什么产出 | 修复 PR #1243 的命名问题 |
| **预期交付物（deliverables）** | 产出什么东西、在哪 | 代码 PR + PRD §3.2 更新 |
| **验收标准（acceptance_criteria）** | 怎么算完成 | CI 通过 + PRD 截图与代码一致 |
| **约束（constraints）** | 边界限制 | 不动接口逻辑、基准分支用 feat/x |

**可选关联资源（不影响任务就绪）**：

| 要素 | 说明 | 示例 |
|---|---|---|
| **关联资源（resources）** | 用户澄清任务时附带的关联链接 | PR 链接、PRD 文档链接、Bug 详情页 |

> resources 是可选要素：用户在对话中附带的链接（PR 地址、文档 URL 等）提取到 resources 中，有就带上、没有就不填。**不追问、不推断**。四要素全齐即输出 task_ready 确认卡片，resources 是否有值不影响任务就绪判断。
> workflow_id 不是关联资源，而是工作流任务的结构化执行字段：仅当 `task_type="workflow"` 时出现；动态任务不输出 workflow_id。

#### 草案生成流程

**第1步 · 提取**：从对话上下文中提取用户明确表达的四要素信息，标为已确认值。同时提取用户附带的链接到 resources（如有）。若命中 `/task workflow_id='xxxxxxx' ...`，提取 `workflow_id` 到结构化字段，并设置 `task_type="workflow"`；否则设置 `task_type="dynamic"`。

若输入过于模糊（如"随便""看看今天有什么""你看着办"），goal 留空进入 `missing_fields`，不可将用户原话原样填入 goal。goal 必须是一个明确的"要做成什么"，"随便"和"看看"不构成目标。

**第2步 · 推断**：对缺失的要素，按以下决策树处理：

| 推断情况 | 处理方式 | 追问形式 | 举例 |
|---|---|---|---|
| 能推断出 **1 个**合理默认值 | 填入该字段 + `needs_confirmation: true` | 确认式对齐："我推断 ___，对吗？" | 修代码 → 交付物=PR |
| 能推断出 **2-7 个**合理选项 | 不填入，进入 `missing_fields` | 结构化选择："是以下哪个？A)___ B)___ ... G) 其他" | 验收标准=CI通过/Review通过/测试通过 |
| **无法推断** | 留空，进入 `missing_fields` | 开放式追问："你的 ___ 是什么？" | 指定分支名、时效要求 |

> **设计意图**：单选默认值直接填入让用户确认（最轻）；多选项列选项让用户选（中）；无法推断才开放式追问（最重）。不把多选项推断值直接填入草案，避免锚定用户。

**结构化选择硬约束**：结构化选择最多7个选项（A-G），最后一个必须为"其他"。即使能想到更多合理选项，也只选最可能的6个 + "其他"兜底，不可超过7个。

**多选项判断方法**：对该字段问自己"除了最常见的那一个，还有没有其他也合理的值？"
- 有 → 是多选项，走结构化选择，不填入草案
- 没有（只有一个合理值）→ 是单选推断，填入 + needs_confirmation

注意：不要因为某个值"最常见"就当单选。只要存在2个以上合理值就视为多选项。

**第3步 · 追问**：若 `missing_fields` 非空 → 按第2步决策树对应的追问形式，一次可问多个缺失要素（不逐个追问）。

**第4步 · 循环**：用户回答后回到第1步重新评估（提取新信息 → 推断 → 追问）。

**第5步 · 输出**：当四要素全部有值（含推断值）→ 输出完整草案（resources 有值则带上，无则不填；工作流任务必须带上 `workflow_id`）。草案中可同时含已确认值和待确认值，用户 review 时一次性确认或修正。

> **设计意图**：能推断的字段直接填默认值让用户确认，比从零追问更轻松。只有无法推断的字段才进追问流程。

#### 追问终止策略

- 轮次计数针对进入 `missing_fields` 的字段（结构化选择项和开放式追问项都计数；单选推断已填入的字段不走追问、不计数）
- 计数起点：从第1次追问后用户的回复开始算，初始提取阶段不算轮次
- 每个要素独立计数连续未明确回答的轮次
- 某要素连续 **3 次** 用户回复仍未明确 → 在第 3 次回复后立即触发推断，不再追问该要素。（第1次回复=第1轮，第2次回复=第2轮，第3次回复=第3轮→触发，不额外多问一轮）→ 用推断默认值填充该字段，并标注 `needs_confirmation: true`
- 四要素全部有值（含推断值）即输出完整草案（resources 不参与此判断），不再追问

#### 推断值标注规则

草案中任何用推断值填充的字段（无论初始推断还是追问终止推断），必须标注 `needs_confirmation: true`，提示用户和下游环节"此项需确认"。用户明确表达的值不需要标注。

---

### 多任务处理

当一条消息中识别出 **2个及以上** 独立任务意图时，先不进四要素追问，而是输出任务清单让用户选择：

```
识别到 N 个任务：
  ① <task1 概要>
  ② <task2 概要>
  ...

你想先做哪个？
```

用户选择后：

- **对选中的任务**：走正常的四要素追问流程，输出完整草案。
- **对未选中的任务**：在输出末尾追加待处理标记，供平台层暂存：

```
[待处理任务]
  ① <taskN 概要>  ← 用户选了②，此项暂存
  source: <来源消息片段>
```

**平台层职责**（非 Skill 职责）：读取 [待处理任务] 标记后暂存；在选中任务处理完成后，将暂存任务的概要注入上下文，并附加 `[RESUME_TASK]` 标记再次调用 Skill。Skill 读到 `[RESUME_TASK]` 后走路径4，直接对该任务走四要素追问。Skill 无状态，不自行记忆未处理任务。

**约束**：
- 同一消息内最多处理 5 个任务意图，超过 5 个时取最明确的 5 个，并在清单末尾标注"还有更多任务，建议分批发起"
- **父子关系判断**：以下关联词连接的动作优先视为同一任务的子部分：顺便、同时、一起、顺带。判断方法：第二个动作是否依赖第一个动作的结果？如果是→子部分，合并为单一任务，后者作为前者的 deliverables；如果否→独立任务，拆分。仅当任务之间确实是**独立目标**（不同 goal、不同 deliverables、不需共同验收）时才拆分
- **超过3个任务时追加兜底提示**：若识别到的任务超过3个，在清单末尾追加"以上是我识别到的任务，还有遗漏吗？"让用户补充

---

### 输出规范

#### 通用格式

所有需要跟用户确认任务要素和下一步操作的场景，使用 AixUI 卡片输出：

```
<AixUI cardId='card_3e31e1f1'>
{
  "type": "card_type_name",
  ...其他数据字段...
}
</AixUI>
```

- `cardId`：**必须使用 `card_3e31e1f1`，不得自行随机生成其他值**。此 ID 由平台层约定，用于前端卡片渲染识别和用户交互追踪。自行生成会导致卡片渲染失败
- 标签内容：JSON 数据，其中 `"type"` 字段标识卡片类型，平台层据此渲染对应 UI 组件
- 所有数据都放在 JSON body 中，不使用 params 属性

> ⛔ **硬约束：cardId 只能用 `card_3e31e1f1`**。禁止自行编造、随机生成、或使用任何其他值。无论输出哪种卡片类型，cardId 一律为 `card_3e31e1f1`。违反此约束将导致卡片渲染失败，属于严重错误。

#### 草案追问中（四要素不全）

四要素未全齐、需要向用户追问时，输出追问卡片。⛔ **cardId 必须为 `card_3e31e1f1`，禁止使用其他任何值**：

```
<AixUI cardId='card_3e31e1f1'>
{
  "type": "task_clarify",
  "task_type": "dynamic",
  "goal": "修复 PR #1243 的命名问题",
  "deliverables": ["代码 PR（命名修正）"],
  "acceptance_criteria": [],
  "constraints": [],
  "resources": [],
  "missing_fields": ["acceptance_criteria", "constraints"],
  "needs_confirmation": ["deliverables"],
  "questions": [
    "验收标准是以下哪个？A) CI 通过  B) Review 通过  C) 测试通过  D) 其他",
    "有什么约束吗？（如不动接口、指定分支等）"
  ]
}
</AixUI>
```

#### 多任务清单

识别到多个任务时，输出选择卡片。⛔ **cardId 必须为 `card_3e31e1f1`，禁止使用其他任何值**：

```
<AixUI cardId='card_3e31e1f1'>
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

#### workflow_id 兼容规则

新格式中，`workflow_id` 是结构化字段，不再放入 `resources`。为兼容历史草案/历史卡片，如果输入上下文或暂存草案中仍出现 `resources: ["workflow_id: xxx"]`：
- Skill/平台应提取 `xxx` 到结构化字段 `workflow_id`
- 设置 `task_type="workflow"`
- 从展示型 `resources` 中移除该项，避免把执行字段当作普通关联资源展示
- 用户点「执行」时，平台优先读取结构化 `workflow_id`；若缺失，可从旧格式 `resources` 中兜底解析

#### 任务就绪确认

四要素全齐，草案完整时，输出确认卡片。⛔ **cardId 必须为 `card_3e31e1f1`，禁止使用其他任何值**：

```
<AixUI cardId='card_3e31e1f1'>
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

平台层读到此卡片后渲染确认卡片 UI，提供三个按钮：

| 按钮 | 行为 |
|---|---|
| **执行** | 平台层将 `task_ready.task` 转换为任务协作中心 `TaskRequest`，调用 `POST /api/v1/collaboration/tasks/execute` 创建并执行任务 |
| **丢弃** | 平台层直接删除任务，不调用 execute 接口 |
| **暂存** | 平台层保存完整草案为草稿，用户以后可继续发起 |

用户若要修改任务信息，直接在对话中说明（如"约束改成今天内"），Skill 更新草案后重新输出确认卡片，平台层重新渲染。不提供修改按钮，修改走对话交互。

##### 执行按钮后的平台接续

Skill 到 `task_ready` 即完成任务定义；用户点击「执行」后，**不再调用本 Skill**。平台层必须读取 `task_ready.task` 和本次 `/task` 指令解析出的任务模式，转换为任务协作中心请求并调用：

```http
POST /api/v1/collaboration/tasks/execute
```

请求体类型为接口文档中的 `TaskRequest`：

```typescript
interface TaskRequest {
  task_spec: {
    metadata: { title: string; instruction: string };
    context: { background: string; extend_props: Record<string, unknown> };
    goal: { objective: string; acceptances: Array<{ id: string; acceptance: string }> };
  };
  source_type: 'bot' | 'coop_group' | 'api';
  owner_user_id: string;
  owner_bot_id: string;
  execution_config: {
    task_type: 'yaml' | 'workflow' | 'dynamic';
    yaml?: string | Record<string, unknown>;
    workflow_id?: string;
    [key: string]: unknown;
  };
}
```

字段转换规则：

| Skill / 平台上下文字段 | `TaskRequest` 字段 | 说明 |
|---|---|---|
| `task.goal` | `task_spec.goal.objective` | 任务目标 |
| `task.goal` 短摘要 | `task_spec.metadata.title` | 平台生成短标题，必须非空 |
| `task.goal + deliverables + acceptance_criteria + constraints + resources` | `task_spec.metadata.instruction` | 合并成完整执行指令，必须非空 |
| `task.task_type` | `execution_config.task_type` | 动态任务为 `dynamic`，工作流任务为 `workflow` |
| `task.workflow_id` | `execution_config.workflow_id` | 仅工作流任务必填；优先读取结构化字段，兼容旧格式 resources 兜底 |
| 当前会话背景 / `task.goal` | `task_spec.context.background` | 可为空，但建议写入任务背景 |
| `task.acceptance_criteria[]` | `task_spec.goal.acceptances[]` | 生成 `ac1/ac2/...`，acceptance 必须非空 |
| 当前会话/群/父任务 | `task_spec.context.extend_props.teamclaw_context` | 由平台注入上下文 |
| 当前用户 | `owner_user_id` | 后端仍需校验登录态 |
| 当前 Bot / 群主 Bot | `owner_bot_id` | 单 Bot 为当前 Bot，协作群为 Master/群主 Bot |
| 单聊 / 协作群 / API | `source_type` | 分别为 `bot` / `coop_group` / `api` |

任务模式转换规则：

| 消息指令 | `execution_config` |
|---|---|
| `/task xxx需求描述` | `{ "task_type": "dynamic" }` |
| `/task workflow_id='xxxxxxx' xxx需求描述` | `{ "task_type": "workflow", "workflow_id": "xxxxxxx" }` |

约束：
- `task_type=workflow` 时，`execution_config.workflow_id` 必须来自消息指令中的 `workflow_id='xxxxxxx'`，且不能为空
- `task_type=dynamic` 时，不需要 `workflow_id/yaml`，不得因为缺少 `workflow_id/yaml` 拒绝请求
- `task_id/status/create_time/finish_time` 均为服务端字段，不由 Skill 输出，也不由前端在创建请求中生成
- execute 返回后，前端使用返回的 `data.task_id` 进入任务副屏，并继续查询 `GET /api/v1/collaboration/tasks/dashboard?task_id=<task_id>` 展示进度
- execute 响应中 `success=true` 只表示产品任务已创建并可查询；任务是否执行成功以返回的 `data.status` 以及 dashboard 状态为准

#### 卡片类型总览

| type 值 | 触发时机 | 用途 |
|---|---|---|
| `task_clarify` | 四要素不全，需追问 | 追问用户缺失的要素 |
| `task_multi_select` | 识别到多个任务 | 让用户选择先做哪个 |
| `task_ready` | 四要素全齐，草案完整 | 确认执行/丢弃/暂存 |

#### 平台标记总览（平台→Skill 注入）

| 标记 | 方向 | 含义 |
|---|---|---|
| `[RESUME_TASK]` | 平台→Skill | 回传暂存任务/草稿 |

#### Skill 输出标记（Skill→平台，文本标记）

| 标记 | 方向 | 含义 |
|---|---|---|
| `[待处理任务]` | Skill→平台 | 多任务中未选中的，请暂存 |

---

### 示例

#### 示例1 · 路径1 · Slash 指令带描述

用户消息：/task 修一下 PR #1243 的命名问题

```
<AixUI cardId='card_3e31e1f1'>
{
  "type": "task_clarify",
  "task_type": "dynamic",
  "goal": "修复 PR #1243 的命名问题",
  "deliverables": ["代码 PR（命名修正）"],
  "acceptance_criteria": [],
  "constraints": [],
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

#### 示例2 · 路径1 · Slash 指令无描述

用户消息：/task

```
<AixUI cardId='card_3e31e1f1'>
{
  "type": "task_clarify",
  "task_type": "dynamic",
  "goal": "",
  "deliverables": [],
  "acceptance_criteria": [],
  "constraints": [],
  "resources": [],
  "missing_fields": ["goal", "deliverables", "acceptance_criteria", "constraints"],
  "needs_confirmation": [],
  "questions": [
    "你想做什么任务？能说说具体目标吗？"
  ]
}
</AixUI>
```

#### 示例3 · 路径1 · 工作流任务消息指令

用户消息：/task workflow_id='wf_pr_rename_001' 修一下 PR #1243 的命名问题，要 CI 通过，不动接口逻辑

```
<AixUI cardId='card_3e31e1f1'>
{
  "type": "task_ready",
  "task": {
    "task_type": "workflow",
    "workflow_id": "wf_pr_rename_001",
    "goal": "修复 PR #1243 的命名问题",
    "deliverables": ["代码 PR（命名修正）"],
    "acceptance_criteria": ["CI 通过"],
    "constraints": ["不动接口逻辑"],
    "resources": []
  },
  "needs_confirmation": ["deliverables"],
  "actions": ["execute", "discard", "save"]
}
</AixUI>
```

#### 示例4a · 路径4 · 回传任务概要（四要素不全→追问）

上下文含 [RESUME_TASK] 标记：
```
[RESUME_TASK]
  生成本月月报
  source: "月报也帮我生成了"
```

```
<AixUI cardId='card_3e31e1f1'>
{
  "type": "task_clarify",
  "task_type": "dynamic",
  "goal": "生成本月月报",
  "deliverables": [],
  "acceptance_criteria": [],
  "constraints": [],
  "resources": [],
  "missing_fields": ["deliverables", "acceptance_criteria", "constraints"],
  "needs_confirmation": [],
  "questions": [
    "交付物是以下哪个？A) Word 文档  B) PDF  C) Markdown  D) 其他",
    "验收标准是什么？",
    "有什么约束吗？"
  ]
}
</AixUI>
```

#### 示例4b · 路径4 · 回传完整草稿（四要素全齐→直接确认卡片）

上下文含 [RESUME_TASK] 标记 + 完整草案：
```
[RESUME_TASK]
  goal: 修复 PR #1243 的命名问题
  deliverables: 代码 PR（命名修正）
  acceptance_criteria: CI 通过
  constraints: 不动接口逻辑
  source: 用户暂存的草稿
```

（四要素全齐，直接输出确认卡片）

```
<AixUI cardId='card_3e31e1f1'>
{
  "type": "task_ready",
  "task": {
    "task_type": "dynamic",
    "goal": "修复 PR #1243 的命名问题",
    "deliverables": ["代码 PR（命名修正）"],
    "acceptance_criteria": ["CI 通过"],
    "constraints": ["不动接口逻辑"],
    "resources": []
  },
  "needs_confirmation": ["deliverables"],
  "actions": ["execute", "discard", "save"]
}
</AixUI>
```

#### 示例5 · 多任务 · 用户选择其中一个

用户消息：/task 帮我修一下 PR #1243 的命名问题，还有月报也帮我生成了

```
<AixUI cardId='card_3e31e1f1'>
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

用户选择①后，对①走四要素追问，输出 task_clarify 卡片。同时未选中的②通过 [待处理任务] 标记输出：

```
[待处理任务]
  ② 生成本月月报
  source: "月报也帮我生成了"
```

平台层读取 [待处理任务] 后暂存②。①处理完成后，平台层注入 [RESUME_TASK] + ②概要再次调用 Skill，②走路径4四要素追问。

---

### 多引擎兼容说明

本 Skill 为纯指令文本，无脚本依赖。各引擎只需按标准路径 `~/.<engine>/skills/task-recognition/SKILL.md` 加载即可。

- 平台标记 `[RESUME_TASK]` 由平台层在回传暂存任务/草稿时注入，引擎无需特殊处理
- `[EXPLICIT_TASK]` 不再作为任务入口；自然语言“发起任务/做个任务/转任务”也不再自动触发 Skill
- 输出格式为结构化文本块（非 JSON），各引擎模型对文本模板的遵循度高于 JSON

> 段2 · 任务规划(planning;触发 框架 [planning],非 arch 场景)

## task-planning

任务目标驱动的**任务规划** skill,运行在 **owner bot**(owner_bot_id)。框架投递 planning prompt(prompt 含 `{goal, context, target_node, graph_snapshot, gaps}` + 返回格式约定;详见框架 `GapBasedPlanningStrategy._compose_planning_prompt`),本 skill 读 prompt 中的目标节点 `node_id`,按案例剧本确定式产出下一批子任务。

### 环境约束(必须遵守)

> **禁止联网搜索**。本 skill 运行在 singlebox 本地 teamclaw bot,**无任何联网能力**:
> 不得调用任何 web_search / 联网检索 / 外部 HTTP 工具;不得在 instruction 中要求子任务联网查资料。
> 一切判断基于 prompt 中已提供的 `{goal, context, snapshot, done_children, gaps}` 与你自身知识进行,缺数据用合理假设/占位补全并标注。
### 触发条件

收到 prompt 头部 `[planning]` 标记的指令,且 prompt 含 `目标节点 node_id=...` 与 `任务态快照{...}`。

### 输入(框架组装,prompt 内嵌)

| 字段 | 含义 |
|---|---|
| `node_id` | 当前计算 gap 的目标节点(node_id=... 形式) |
| `goal.objective` / `goal.acceptances[]` | 节点自身目标与验收标准 |
| `context.background` | 任务背景 |
| `gaps` | 上一轮验收 FAIL 的 gaps(补救规划时非空) |
| `graph_snapshot.loop_round` | 当前 BBS 上升轮次 |

### 输出(返回格式约定)

返回 JSON 字符串,结构为对象 `{"tasks": List[TaskSpec], "has_gap": bool, "gap_detail": str}`:

```json
{"tasks": [{"metadata": {"task_id": "<子节点node_id>", "title": "<标题>", "instruction": "<指令>"},
             "context": {"background": "<背景>", "extend_props": {}},
             "goal": {"objective": "<目标>", "acceptances": [{"id": "<ac_id>", "description": "<描述>"}]}}],
 "has_gap": true,
 "gap_detail": ""}
```

- `tasks` = 下一批可执行子任务;`metadata.task_id` 即子节点 `node_id`(须唯一,不与已存重复);
- gap 已闭(验收通过)→ `{"tasks": [], "has_gap": false, "gap_detail": "done"}`;
- 有 gap 但无规划能力拆不出子 → `{"tasks": [], "has_gap": true, "gap_detail": "<原因>"}`;
- `has_gap` = 目标 - 已完成产出 是否仍有差距;`done_children` 已列出已 DONE 子节点及产出,据此产**尚未完成**的下一批(不重复产已 DONE 的);
- 子任务 `goal.acceptances` 为该子任务自身的验收标准;无独立标准可继承父 goal。

### 确定式分解剧本(案例 gwqie46v7hzr1w6h)

框架二轮起 target 恒为根 `t_case`(根从初始规划后一直 PLANNING;任一子节点 PASS→触发根重新 plan,
target 仍是 `t_case`)。按目标 `node_id` **+ 快照 `done_children`(已 DONE 子节点)** 联合返回下一批:

| 目标 node_id | done_children(已 DONE 子节点) | 返回 children |
|---|---|---|
| `t_case` | `[]`(初始,无已完成子) | `[N_overview]` |
| `t_case` | `[N_overview]` | `[N_market, N_tech, N_compete, N_customer, N_field_interview]` |  <!-- N_field_interview 并行触发 MISS@MAX→升 BBS(其它兄弟仍 RUNNING 保根可恢复) -->
| `t_case` | `[N_overview, N_market, N_tech, N_compete, N_customer]` | `[N_practice_bbs, N_report]` |  <!-- 升 BBS 后根被可恢复态守卫拦,owner 不重 plan;BBS 中继经 attach+root_verified 收口 -->
| `t_case` | `[…, N_practice_bbs]` | `[N_report]` |
| `t_case` | `[…, N_report]` | `[]`(根级终验 gap 闭) |
| FAIL+gaps 叶节点(target=该叶,补救规划) | — | `[N_<叶>_remediate]`(按 gaps 描述产 1 个补救子) |
| 其它/无可规划 | — | `[]` |

> 递进依据 = `done_children` 已出现的子节点(逐步补齐未覆盖维度,**不重复产已 DONE 的**);
> `done_children[].output` 含各子产出,可据此细化下一批子任务的 `instruction` / `acceptances`。
> 节点名由本 skill 决定,**框架代码零 case 知识**(框架 grep 不得出现这些字面量)。

> 段3 · 任务派发搜推(search;触发 框架 [search])

## task-search

任务目标驱动的**任务派发搜推决策** skill,运行在 **owner bot**(owner_bot_id)。框架语义预查候选 bot 集(分字段 title/objective/background 调 BCSFuse recommend),把候选集喂入 prompt;本 skill 在候选里决出**谁执行 + 怎么执行(多 bot 拉哪种协作群)**,不自取 BCSFuse。

### 环境约束(必须遵守)

> **禁止联网搜索**。本 skill 运行在 singlebox 本地 teamclaw bot,**无任何联网能力**:
> 不得调用任何 web_search / 联网检索 / 外部 HTTP 工具。
> 搜推判定仅基于 prompt 中的子任务需求与你自身的 bot 候选清单知识进行;候选清单已在 prompt 给出,直接挑选,无需联网。
### 触发条件

收到 prompt 头部 `[search]` 标记的指令,且 prompt 含 `子任务需求+候选集{demand, catalog}` + 返回格式约定(详见框架 `SearchBasedDispatchStrategy._compose_search_prompt`)。

### 输入(prompt 内嵌)

| 字段 | 含义 |
|---|---|
| `demand.node_id` | 待派发子任务节点 id |
| `demand.goal` / `demand.instruction` / `demand.acceptances[]` | 子任务需求 |
| `catalog[]` | 框架预查候选 bot 集:`{bot_id, bot_name, bot_desc, score, short_profile, reasons}`(按 score 降序) |

### 输出(返回格式约定)

返回 JSON 字符串,`outcome` 标 4 态之一(详见框架 `_parse_search_result`):

- **HIT_SINGLE**:`{"outcome":"HIT_SINGLE","bot_id":"<bot_id>"}`
- **HIT_GROUP**(已有群复用):`{"outcome":"HIT_GROUP","group_id":"<group_id>"}`
- **HIT_MULTI_BOTS**(动态拉群):
  ```json
  {"outcome":"HIT_MULTI_BOTS","bot_ids":[...],"collab_mode":"chat|manager_worker|state_machine",
   "group_name":"<群名>","manager_bot_id":"<manager>(manager_worker 必填)",
   "members_info":[{"bot_id":"...","role":"...","responsibility":"..."}],
   "definition_yaml":"<workflow yaml>(state_machine 必填)"}
  ```
- **MISS**:`{"outcome":"MISS","miss_reason":"<原因>"}`

> **bot_id / bot_ids 必须填 `catalog` 里的真实 `bot_id`**(剧本角色名 → 在 catalog 里按 `bot_name` 匹配,取该 bot 的 `bot_id`);catalog 中无对应角色名的 bot → 该候选不可用,所有候选都不匹配时返回 MISS。`start_run` 用此 bot_id 真实投递,**角色名不是 bot_id**。

### 确定式决策剧本(案例 gwqie46v7hzr1w6h)

> **强约束(确定性优先)**:严格按 `demand.node_id` 查下表返回对应 outcome —— **禁止自由组合候选 bot**。
> catalog 里下表未提及的 bot(如 `task-owner-bot`、与该 node_id 剧本无关的角色)是预查噪音,**必须忽略**,
> 不得因"多 bot 协作更全面"擅自改 `HIT_SINGLE` 为 `HIT_MULTI_BOTS`。表中角色名须在 catalog 里按
> `bot_name` 匹配真实 `bot_id`;匹配不到 → 该节点返 `MISS`(候选不匹配),**不得用其它 bot 顶替**。

按 `demand.node_id` 返回(knowledge 只在本 skill):

| node_id | 输出 outcome | 执行者/拉群 |
|---|---|---|
| `N_overview` | HIT_SINGLE | `行业信息抓取Bot` |
| `N_market` | HIT_MULTI_BOTS(manager_worker) | `市场需求分析Bot`+`资本市场投资Bot`,群名"存储行业市场发展趋势研究群",manager=市场需求分析Bot |
| `N_tech` | HIT_MULTI_BOTS(manager_worker) | `数据中心存储架构师`+`企业级SSD专家`,群名"存储技术发展总结和预测",manager=数据中心存储架构师 |
| `N_compete` | HIT_SINGLE | `存储行业供应链专家` |
| `N_customer` | HIT_MULTI_BOTS(manager_worker) | `ToG方案专家`+`ToB方案专家`+`采购决策专家`,群名"存储行业客户分析群",manager=ToG方案专家 |
| `N_practice_bbs` | HIT_SINGLE | `实践bbs专家Bot` |
| `N_report` | HIT_SINGLE | `报告聚合Bot` |
| `N_field_interview` | MISS | `miss_reason="候选 bot 均无法覆盖子任务需求(现场访谈无对应专家 bot)"` |
| `_remediate` 节点 | HIT_SINGLE | 对应原维度 bot(同主体) |
| 候选都不匹配 / 未知 | MISS | `miss_reason="候选 bot 均无法覆盖子任务需求"` |

> 协作群名 / 成员角色分工知识只在本 skill;`members_info` 承载 `{bot_id, role, responsibility}` 透传 BCS `participants[].role`。

### 确定式决策剧本(arch 场景:planning-arch 产出的 node_id)

`planning-arch` 通用规划产出的 arch 子任务 node_id(非 storage 案例),同样**按 `demand.node_id` 查下表**返回(同 storage 方式:严格按表,禁止自由组合候选 bot)。供 2-mode / 3-mode natural e2e 用。

| node_id | 输出 outcome | 执行者/拉群 |
|---|---|---|
| `N_tech_stack` | HIT_SINGLE | `技术栈概览Bot` |
| `N_dual_view` | HIT_MULTI_BOTS(manager_worker) | `业务架构视角Bot`+`数据架构视角Bot`,群名"业务与数据架构双视角分析群",manager=业务架构视角Bot |
| `N_architects` | MISS | `miss_reason="候选 bot 均无法覆盖子任务需求(架构师名册无对应现成 bot,走 BBS 中继)"` |
| `N_tech_stack_remediate` / `N_dual_view_remediate`(补救子,若出现) | HIT_SINGLE | 对应原 bot(同主体) |

> 同强约束:严格按 `demand.node_id` 查表;`bot_id`/`bot_ids` 须在 `catalog` 里按 `bot_name` 匹配取真实 `bot_id`,匹配不到 → 该节点 MISS(不得用其它 bot 顶替);catalog 里表未提及的 bot(owner、中继 bot 等)是预查噪音,必须忽略。

> 段4 · 任务验收(acceptance;协作群 driver/owner 验收+push 上报)

## task-acceptance

任务目标驱动的**验收 + push 上报** skill,运行在 **协作群的 driver/owner bot**;协作群跑完叶子子任务并产出交付物后,driver/owner 自调本段判定是否达到该叶 `goal.acceptances`,并**主动 push 上报**到任务后端(参考 bbs 接力上报方式,不写死 url)。**single_bot 叶子不走本段**:由框架 `format_execute` 内联指示 worker 直接输出 JSON `{success,data,gaps}`,经 `TaskExecutorResultPoller` poll 收口 → `on_report`,不 push。

> 聚合节点 / 根节点验收 = planning 的 gap 计算(返回 `[]` = gap 闭 = 验收通过),由 owner bot 的 task-planning 承担,本段不参与聚合/根验收。

### 环境约束(必须遵守)

> **禁止联网搜索**。本 skill 运行在 teamclaw bot,无联网能力:不得调用任何 web_search / 联网检索 / 外部 HTTP 工具。验收判定仅依据协作群本次叶子产出、`goal.acceptances` 与上游上下文,结合自身知识判定;不得因"无法联网核实"而判 FAIL。
> **不写死 url**:后端 base url 与 loop_task_id 从协作群上下文/指令取(派发期注入群 context),不假设固定地址。

### 触发条件

你是协作群的 driver/owner bot,协作群已跑完叶子子任务并产出交付物;需按该叶 `goal.acceptances` 自验收并 push 上报结果。single_bot 叶子不命中本段。

### 输入(从协作群上下文/指令取)

| 字段 | 含义 |
|---|---|
| `{backend}` | 任务后端 base url(派发期注入协作群 context,不写死) |
| `{loop_task_id}` | `{task_id}::{node_id}`(派发期注入协作群 context,定位要回投的执行节点) |
| `goal.objective` / `goal.acceptances[]` | 该叶子子任务的目标与验收标准 |
| 协作群产出 | 协作群本次叶子执行产出的交付物(从群会话/最终输出取) |

### 执行步骤

1. **判定**:比对协作群产出与 `goal.acceptances[]`,得出 success 与 gaps(逻辑同 single_bot 自验收)。

2. **push 上报**:发 HTTP 请求

   `POST {backend}/api/v1/collaboration/tasks/callback/report`

   请求体(JSON 对象,不要输出 Markdown 代码块或额外解释):
   ```
   {"loop_task_id": "{loop_task_id}", "result": {"success": true|false, "data": {"result": "实际产出"}, "gaps": []}}
   ```
   - 验收通过 → `{"success": true, "data": {"result": "..."}, "gaps": []}`
   - 验收不通过 → `{"success": false, "data": {"result": "已有产出"}, "gaps": ["gap 描述"]}`
   - `success` 必须是 JSON bool;FAIL 的 `gaps` 必须为非空字符串列表(驱动 FAIL→补救链路);`data.result` 为实际产出内容;否则框架按 `terminal_result_invalid` 进入 Harness。

3. **收口**:HTTP 200 → 上报完成,框架经 `on_report` 写执行节点并翻态(`success=true`→DONE / `success=false`+非空 gaps→补救 / `result.exec_error`→harness 重投),本段结束。非 200 按下面幂等重试。

### 幂等与重试

`on_report` 按 `event_id`/结果摘要幂等:重复 push 同一结果不会重复翻态。网络抖动返回非 200 时,重发**同一请求体**即可(幂等保证不重复翻态);不要改换 success/gaps 重发。

### 与 bbs 接力上报的关系

本段 push 契约(`/callback/report` + `{loop_task_id,result{success,data,gaps}}`)与 bbs 接力上报方式一致(从消息/上下文取 `{backend}`,不写死);区别:bbs 走 `bbs/attach`+`bbs/result` 专属端点(中继 scoped 节点);本段走统一 `/callback/report`(协作群叶子节点,框架已建好节点,直接用 loop_task_id 定位回投)。

### single_bot 叶子(不走本段)

single_bot 叶子由框架 `format_execute` 内联指示 worker 直接输出 JSON `{success,data,gaps}`(经 poll 收口 → `on_report`),不命中本段、不 push。本段仅协作群 driver/owner 命中。

> 段5 · BBS 接力(bbs-relay-single-task;触发 引擎 BBS 通知;参考文档见 references/)

## bbs-relay-single-task

### 触发

收到引擎主动发的任务消息(含 task_id + backend base url + 自身 bot_id)。
引擎已替你占根(bbs_owner已设为你的bot_id)——**不需要 scan、不需要 claim、不需要自判**。

### 执行步骤

#### 步骤① 读 dashboard 了解剩余事项

- `GET {backend}/api/v1/collaboration/tasks/dashboard?task_id={task_id}`
- 读根 `goal.objective` + `goal.acceptances[]` + 已 DONE 叶子的 `run_info.output`(已完成的部分)
- 自己归纳"剩余事项"(未完成的 acceptances 对应的工作)
- 自己组织 `task_spec`(`metadata{title, instruction}`, `context{background}`, `goal{objective, acceptances[]}`)

#### 步骤② attach(挂 scoped 节点)

- `POST {backend}/api/v1/collaboration/tasks/bbs/attach`
- body: `{"task_id": "{task_id}", "parent_node_id": "{root_node_id}", "task_spec": {你组织的}, "bot_id": "{你自身bot_id}"}`
- 200 → 读 `data.node_id`(你的 scoped 节点 id)
- 409 → 结束(不应发生,引擎已占根;若发生说明被释放,结束不重试)

#### 步骤③ 执行

用自身能力执行 `task_spec.instruction`(产出对应 deliverable + acceptance 内容)。

#### 步骤④ result(回投终态)

- `POST {backend}/api/v1/collaboration/tasks/bbs/result`
- body: `{"task_id": "{task_id}", "node_id": "{步骤②的node_id}", "bot_id": "{你自身bot_id}",
  "acceptance_result": {"verdict": "PASS", "acceptances_metric": [...]},
  "output_patch": {"{deliverable_key}": {产出}}}`
- 200 → 接力完成(框架经 on_bbs_report 收口)

### 与 bbs-relay-pickup 的区别

- bbs-relay-pickup:步① 扫全量任务筛选 bbs_mode → 步②claim → 步③自判 → 步④attach → ...
- bbs-relay-single-task:**跳过 ①②③**(引擎已发现+占根+选了你),直接 **attach→执行→result**

### 环境约束

- `bot_id` 必须用消息中给的"你自身 bot_id",不用引擎账号。
- backend base url 从消息里取,不假设。

> 段6 · 架构师名册 mock(arch-analysis;触发 叶子 instruction 含「某某某公司」;不联网返伪造名册)

## arch-analysis — 某某某公司架构师分析(mock)

### 触发

输入(prompt / 子任务 instruction)中**含关键词「某某某公司」**即触发本 skill。触发后**不联网、不做真实组织推断**,直接返回下述 **mock 伪造数据**。

> 用途:e2e 演练接力 mechanics,绝不在产出中带任何真实公司/真实人员信息。关键词刻意用占位「某某某公司」。

### 输入

被问的技术架构方向(由调用方在 prompt 里给出,例:基础架构 / 中间件架构 / 数据架构 等)。

### 输出(仅一个 ```json 代码块)

按以下模板输出,**把 `<方向>` 替换成被问的方向**,其余姓名/角色/职责**照抄 mock 值**(均为伪造,不要改写为真实信息):

```json
{
  "domain": "<被问的方向>",
  "architects": [
    {
      "name": "工程师甲",
      "role": "<被问的方向>首席架构师",
      "responsibility": "mock:负责某某某公司<被问的方向>的整体技术架构与演进规划"
    },
    {
      "name": "工程师乙",
      "role": "<被问的方向>高级架构师",
      "responsibility": "mock:负责某某某公司<被问的方向>核心模块设计与技术评审"
    },
    {
      "name": "工程师丙",
      "role": "<被问的方向>技术负责人",
      "responsibility": "mock:负责某某某公司<被问的方向>团队技术管理与落地推进"
    }
  ],
  "note": "mock 伪造数据,仅用于 e2e 演练,不代表任何真实人员"
}
```

### 规则(硬约束)

- **关键词触发**:无「某某某公司」关键词时不触发;触发即返上述 mock,不掺杂真实信息。
- **输出仅一个 ```json 代码块**,字段固定 `domain` / `architects[]` / `note`,便于上游解析。
- **每个方向固定返 3 条 mock 架构师**(工程师甲/乙/丙),姓名/职责为伪造占位,不得替换为真实姓名。
- 不得联网,不得臆造真实姓名,不得在输出中出现除「某某某公司」外的任何真实公司名。

> 段7 · 任务规划·arch 场景(planning-arch;触发 框架 [planning] 且 prompt 含「某某某公司」;确定式按根验收交付物集合 + done_children 查表)

## task-planning-arch

任务目标驱动的**任务规划** skill,运行在 **owner bot**(owner_bot_id)。框架投递 planning prompt
(prompt 含 `{goal, context, target_node, graph_snapshot, gaps}` + 返回格式约定;详见框架
`GapBasedPlanningStrategy._compose_planning_prompt`),本 skill 读 prompt 中的目标节点 `node_id`,
按 **arch 场景确定式剧本**产出下一批子任务——参照 `task-planning` 的 storage 特例:**按根目标的验收交付物集合
+ `done_children` 查表**,不靠自由 LLM 分解(避免拆子数/拆法飘忽)。

### 环境约束(必须遵守)

> **禁止联网搜索**。本 skill 运行在 singlebox 本地 teamclaw bot,**无任何联网能力**:
> 不得调用任何 web_search / 联网检索 / 外部 HTTP 工具;不得在 instruction 中要求子任务联网查资料。
> 一切判断基于 prompt 中已提供的 `{goal, context, snapshot, done_children, gaps}` 与你自身知识进行,
> 缺数据用合理假设/占位补全并标注。

### 触发条件

收到 prompt 头部 `[planning]` 标记的指令,且 prompt 含 `目标节点 node_id=...` 与 `任务态快照{...}`。

### 输入(框架组装,prompt 内嵌)

| 字段 | 含义 |
|---|---|
| `node_id` | 当前计算 gap 的目标节点(node_id=... 形式;根 task_id 由服务端生成,不作为场景判据) |
| `goal.objective` / `goal.acceptances[]` | 节点自身目标与验收标准 |
| `context.background` | 任务背景 |
| `gaps` | 上一轮验收 FAIL 的 gaps(补救规划时非空) |
| `graph_snapshot.loop_round` | 当前 BBS 上升轮次 |

### 输出(返回格式约定)

返回 JSON 字符串,结构为对象 `{"tasks": List[TaskSpec], "has_gap": bool, "gap_detail": str}`:

```json
{"tasks": [{"metadata": {"task_id": "<子节点node_id>", "title": "<标题>", "instruction": "<指令>"},
             "context": {"background": "<背景>", "extend_props": {}},
             "goal": {"objective": "<目标>", "acceptances": [{"id": "<ac_id>", "description": "<描述>"}]}}],
 "has_gap": true,
 "gap_detail": ""}
```

- `tasks` = 下一批可执行子任务;`metadata.task_id` 即子节点 `node_id`(须唯一,不与已存重复);
- gap 已闭(验收通过)→ `{"tasks": [], "has_gap": false, "gap_detail": "done"}`;
- 有 gap 但拆不出子 → `{"tasks": [], "has_gap": true, "gap_detail": "<原因>"}`;
- `done_children` 已列出已 DONE 子节点及产出,据此不重复产已 DONE 的。

### 确定式分解剧本(arch 场景:按根目标交付物集合 + `done_children`)

框架二轮起 target 恒为根(根从初始规划后一直 PLANNING)。按根 `goal.acceptances` 的**交付物集合** + 快照 `done_children`
(已 DONE 子节点 + 其 `output` 产出)联合返回下一批:

#### 单一交付物:架构师名册(预期 MISS→升 BBS 中继)

| 目标 node_id | done_children | 返回 children |
|---|---|---|
| 根验收仅含架构师名册 | `[]`(初始) | `[N_architects]`  <!-- 无 bot → MISS@MAX→升 BBS,金庸中继收口 --> |
| 根验收仅含架构师名册 | done 产出**已含架构师名册**(可能来自 `run_mode=="bbs"` 中继 scoped 节点) | `[]`(`has_gap=false`,gap 闭) |
| 根验收仅含架构师名册 | 仍缺架构师名册 | `[]`(`has_gap=true`,`gap_detail="缺架构师名册"`,等 BBS 中继) |

#### 两份交付物:技术栈概览 + 架构师名册

| 目标 node_id | done_children | 返回 children |
|---|---|---|
| 根验收含技术栈概览 + 架构师名册 | `[]`(初始) | `[N_tech_stack, N_architects]`  <!-- tech_stack 命中 bot(single_bot);architects MISS→BBS --> |
| 根验收含技术栈概览 + 架构师名册 | done 产出**已含技术栈概览 + 架构师名册** | `[]`(`has_gap=false`,gap 闭) |
| 根验收含技术栈概览 + 架构师名册 | 仍缺任一份 | `[]`(`has_gap=true`,`gap_detail="<缺哪份>"`) |

#### 三份交付物:技术栈概览 + 业务/数据双视角分析 + 架构师名册

| 目标 node_id | done_children | 返回 children |
|---|---|---|
| 根验收含技术栈概览 + 双视角分析 + 架构师名册 | `[]`(初始) | `[N_tech_stack, N_dual_view, N_architects]`  <!-- tech_stack single_bot;dual_view coop_group;architects MISS→BBS --> |
| 根验收含技术栈概览 + 双视角分析 + 架构师名册 | done 产出**已含技术栈概览 + 双视角分析 + 架构师名册** | `[]`(`has_gap=false`,gap 闭) |
| 根验收含技术栈概览 + 双视角分析 + 架构师名册 | 仍缺任一份 | `[]`(`has_gap=true`,`gap_detail="<缺哪份>"`) |

#### FAIL+gaps 叶补救 / 其它

| 情形 | 返回 |
|---|---|
| target=FAIL 叶节点且 `gaps` 非空(补救规划) | `[N_<叶>_remediate]`(按 gaps 描述产 1 个补救子) |
| 其它 / 无可规划 | `[]` |

#### 子任务规格(固定 node_id + task_spec)

```json
[
  {"metadata": {"task_id": "N_tech_stack", "title": "基础架构方向技术栈概览",
                "instruction": "给出某某某公司基础架构方向的技术栈概览:列出计算/存储/网络等分层与每层核心组件,简要说明。基于自身知识即可,不联网。"},
   "context": {"background": "某某某公司基础架构方向技术栈梳理", "extend_props": {}},
   "goal": {"objective": "产出基础架构方向技术栈概览(分层+核心组件)",
            "acceptances": [{"id": "ac_tech", "description": "给出基础架构方向技术栈概览(计算/存储/网络等层与核心组件)"}]}},
  {"metadata": {"task_id": "N_dual_view", "title": "业务架构与数据架构双视角深度分析",
                "instruction": "从**业务架构与数据架构双视角**深度分析某某某公司基础架构的现状与演进(需业务架构、数据架构两个视角的专家协作完成)。基于自身知识即可,不联网。"},
   "context": {"background": "某某某公司基础架构双视角分析", "extend_props": {}},
   "goal": {"objective": "从业务架构与数据架构双视角深度分析基础架构现状与演进",
            "acceptances": [{"id": "ac_dual", "description": "从业务架构与数据架构双视角深度分析基础架构现状与演进"}]}},
  {"metadata": {"task_id": "N_architects", "title": "基础架构方向架构师名册",
                "instruction": "整理某某某公司基础架构方向的 3 位核心技术架构师,给出每位架构师的姓名/角色 + 主要职责。基于自身知识即可,不联网。"},
   "context": {"background": "某某某公司基础架构方向架构师梳理", "extend_props": {}},
   "goal": {"objective": "整理基础架构方向 3 位核心架构师(姓名/角色 + 职责)",
            "acceptances": [{"id": "ac_arch", "description": "给出基础架构方向 3 位架构师的姓名/角色 + 职责"}]}}
]
```
> 各 case 初始批次按根验收交付物集合取其中若干:仅架构师名册→`[N_architects]`;技术栈概览+架构师名册→`[N_tech_stack, N_architects]`;
> 三份交付物齐全→`[N_tech_stack, N_dual_view, N_architects]`。

> **gap 闭判据(二轮 owner 复核根 gap 用)**:读 `done_children[].output`——若各交付物(技术栈概览 / 双视角分析 /
> 架构师名册,架构师名册可能由 `run_mode=="bbs"` 中继 scoped 节点的 `architects` 产出)均已出现 → `has_gap=false`
> 收口;任一缺失 → `has_gap=true`。不重复产已 DONE 的;`N_architects` 被 BBS recover 清掉后由中继 scoped 节点补其产出。
> 节点名由本 skill 决定,**框架代码零 case 知识**(框架 grep 不得出现这些字面量,知识只在本 skill)。
