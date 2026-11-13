# Workflow YAML 的 AI 智能创建 / 修改 Demo 方案

> 目标：在 `workflows/workspace?workflowId=...&tab=editor` 的 YAML 编辑器内，提供类似 smart-onboarding 的“先描述目标，再由 AI 生成可审阅草稿”的工作流配置体验。
>
> 本文对应的静态交互 Demo：`docs/demos/ai-workflow-yaml-demo.html`。

> 开发进展（2026-11-13）：本文及 HTML 保留为交互评审 Demo，不代表真实 AI 输出。
> 实际 Editor 集成已进入本地开发验证，使用现有 `/api/smart-onboarding/generate`、
> 异步轮询和 `/api/workflows/validate`，不新增下面早期构想中的模型服务。
> 开发边界以 `apps/evolverun/clawweb/public/modules/workflow/docs/specs/2026-11-13-workflow-ai-draft-editor.md` 为准；尚未部署预发。

## 1. 先做 Demo，不直接改线上编辑器

Demo 先验证三个关键问题：

1. **用户是否愿意用自然语言描述工作流**，而不是直接手写 YAML。
2. **用户是否能快速理解 AI 改了什么**，并且敢于点击“应用修改”。
3. **AI 生成的 YAML 是否能被现有校验、可视化预览和保存链路复用**。

Demo 不接真实 AI、后端和登录态，所有生成结果是可重复的本地模拟数据；它只用于评审交互和页面布局。

## 2. 推荐的产品形态

### 2.1 两个入口

| 入口 | 默认模式 | 适合场景 |
| --- | --- | --- |
| 编辑器顶部「AI 创建」 | 创建 | 从一句话生成全新的工作流 YAML |
| 编辑器顶部「AI 修改」/ 选中 YAML 后的快捷操作 | 修改 | 在当前 YAML 基础上增删节点、调整依赖或修改提示词 |

右侧固定 AI 助手面板，不跳离工作流编辑器；用户始终能同时看到当前 YAML、AI 解释和差异预览。

### 2.2 类 smart-onboarding 的四步体验

1. **描述目标**：输入“我要做什么”，可补充参与 Bot、触发方式、人工审核要求。
2. **AI 拆解方案**：展示识别出的节点、依赖关系、风险提示和必要的澄清问题。
3. **生成并预览**：生成 YAML 草稿；左侧展示统一 diff，右侧展示节点图和变更摘要。
4. **应用并校验**：用户点击“应用修改”，再执行现有 YAML 校验；校验通过后才允许保存。

> 关键原则：AI 只生成草稿，不自动保存、不自动发布、不自动执行工作流。

## 3. Demo 里的可演示路径

### 路径 A：AI 创建

1. 点击顶部「AI 创建」。
2. 点击示例需求“创建一个客服问题分流流程”。
3. 点击「生成 YAML 草稿」。
4. 查看 AI 总结：识别出分流、知识库检索、人工升级、回复用户等节点。
5. 查看“修改预览”中的新增行和“流程预览”中的节点图。
6. 点击「应用到编辑器」→「校验 YAML」→「保存工作流」。

### 路径 B：AI 修改

1. 点击顶部「AI 修改」。
2. 输入“增加人工审核节点，只有审核通过后才发送最终回复”。
3. 生成修改草稿。
4. 查看删除/新增/调整依赖的差异解释。
5. 应用后重新校验并保存。

## 4. 和现有前端实现的衔接点

当前代码已经具备可以复用的能力：

- YAML 编辑器：`apps/frontend-nextgen/src/pages/Workspace/components/Modals/YamlEditor/YamlCodeEditor.tsx`
- YAML 校验 Hook：`apps/frontend-nextgen/src/pages/Workspace/hooks/useYamlValidation.ts`
- 工作流列表 / 详情 Controller：`apps/frontend-nextgen/src/services/backendApi/workflows/workflowController.ts`
- 从 YAML 创建工作流：`apps/frontend-nextgen/src/components/BotWorkshop/TaskEscortFlowConfig/CreateWorkflowFromYamlModal.tsx`
- DAG 预览：`apps/frontend-nextgen/src/components/BotWorkshop/TaskEscortFlowConfig/WorkflowDagView.tsx`

生产实现建议在这些能力之上新增一层 `AI Workflow Draft`，不要让 AI 直接绕过现有校验和保存链路。

## 5. 生产版接口建议

### 5.1 生成草稿

```http
POST /api/ai/workflows/draft
Content-Type: application/json
```

```json
{
  "workflow_id": "cct-emulation",
  "mode": "create",
  "prompt": "创建一个客服问题分流流程，低置信度时转人工",
  "current_yaml": "",
  "selection": null,
  "context": {
    "available_bots": ["客服助手", "知识库助手", "人工坐席"],
    "available_tools": ["知识库检索", "工单创建"],
    "schema_version": "v1"
  }
}
```

```json
{
  "draft_id": "draft_xxx",
  "summary": "新增 4 个节点，并在低置信度分支增加人工升级",
  "proposed_yaml": "...",
  "changes": [
    {"kind": "add", "path": "runtime.nodes.route", "reason": "根据问题类型分流"}
  ],
  "validation": {"valid": true, "errors": [], "warnings": []},
  "risk_flags": [],
  "graph": {"nodes": [], "edges": []}
}
```

### 5.2 保存边界

- 前端先展示 `proposed_yaml` 和 diff。
- 用户点击“应用修改”后，前端把 YAML 写入编辑器状态。
- 复用现有校验接口 / `useYamlValidation`。
- 用户再次点击“保存”才调用现有工作流写入接口。
- 生成、应用、保存均记录 `draft_id`，便于审计和问题回放。

## 6. 安全与质量门禁

1. **结构化输出**：模型返回 JSON，不直接信任自然语言中的 YAML 片段。
2. **服务端解析**：使用 YAML parser + WorkflowSpec schema + DAG 环检测。
3. **能力白名单**：Bot、工具、MCP、触发器都从当前工作区可用资源中选择，拒绝模型凭空创建外部连接。
4. **敏感信息保护**：发送给模型前脱敏 token、Cookie、私有 URL 和环境变量值。
5. **高风险动作二次确认**：删除节点、修改外部调用、增加自动发布、改变人工审核策略时，要求显式确认。
6. **可回滚**：保存前保留上一版 YAML；应用修改只是编辑器状态变化，不覆盖服务端版本。
7. **失败可恢复**：模型超时或返回非法结构时，保留原 YAML，不阻塞普通手工编辑。

## 7. 分阶段落地

### P0：交互验证

- 上线本 Demo 评审布局和流程。
- 使用固定 mock 结果。
- 验证创建 / 修改 / diff / 应用 / 校验 / 保存的心智模型。

### P1：接入真实生成接口

- 复用 `POST /api/smart-onboarding/generate` 和现有异步任务轮询，不新建模型服务。
- 复用现有 YAML 校验和 DAG 预览。
- 增加生成中、错误、重试、撤销应用状态。

### P2：智能增强

- 支持选中 YAML 片段后局部修改。
- 支持澄清问题卡片，而不是一次性生成。
- 支持工作流历史版本和“一键恢复”。
- 根据运行失败日志提供“修复 YAML”建议，但仍需用户审阅后应用。

## 8. 验收标准

- 用户可以在不离开编辑器的情况下，从一句自然语言生成一份可审阅 YAML。
- 用户可以明确看到 AI 的变更摘要、diff 和流程图。
- 未经过用户点击“应用修改”，原 YAML 不发生改变。
- 未通过 YAML 校验，保存按钮不可用或明确阻止保存。
- AI 失败时，原 YAML、手工编辑和普通保存能力不受影响。
- Demo 在无网络、无后端、无依赖安装的情况下可直接打开。

## 9. 本次 Demo 的融合方式（对应当前 Editor 页面）

Demo 已调整为“现有编辑器 + AI Drawer”，而不是另起一个独立的 AI 页面：

- 保留原有工作流标题、YAML 编辑器、DAG 预览、Diff 预览、校验和保存区域。
- 在 Editor 工具栏新增「✦ AI 助手」入口；页面顶部保留「AI 创建 / AI 修改」快捷入口。
- AI 面板作为右侧 Drawer 挂载，收起后编辑器自动恢复单栏宽度，不影响手工编辑。
- AI 生成结果只写入草稿态；「应用到编辑器」才会更新 textarea，「保存工作流」仍走原有保存边界。
- 面板上下文明确显示当前 `workflow.yaml · Editor / YAML`，强调 AI 读取的是当前编辑器内容，而不是另一个配置空间。

生产接入时可以按同样的组件边界拆分：

```text
TaskEscortFlowConfig / WorkflowEditor
├── existing YAML / DAG view
├── existing validation + save
└── WorkflowAiDrawer
    ├── mode: create | modify
    ├── prompt + clarification
    ├── draft summary
    ├── diff preview
    └── apply draft → existing editor state
```

## 10. 目标页面：`perception-ticket-realtime`

当前 Demo 已按这个具体 Editor 场景调整：

- 工作流：`perception-ticket-realtime`
- 标题：感知工单实时分析与处置
- 现状提示：41 个节点、67 条连线、运行中、存在配置问题提示
- AI 生成示例：在 `LLM 双层推理` 后插入 `AI 风险复核`，并在 S2/S3 路径增加 `HITL 人工确认`，最后接回 `分流路由`
- Demo 只展示“局部生成”，不会把整份 41 节点流程替换掉；实际开发复用生成服务的完整 YAML 返回契约，由客户端计算差异并通过现有服务端校验，再由用户明确应用。
