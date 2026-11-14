# Workflow Editor：AI 生成与修改 YAML

## 状态与边界

用户已要求从 HTML Demo 进入开发。归属模块为 `@avernet/workflow`，实际入口是
`WorkflowWorkspace → EditorTab → pages/Editor.tsx`，适用于所有可编辑工作流，
不为某个工作流 ID 硬编码节点、运行次数或审核人。

原始实现沿用智能接入的异步生成 Service API；该传输已由下方
「2026-11-14 AutoHeal/BaaS 传输更正」替代。当前实现使用独立的 Workflow AI draft
API，后端复用 AutoHeal 的有效 `autoHeal.botId` 与现有 BaaS，不猜测模型接口或使用固定模板。
遵循根架构规则 `docs/arch/arch.rules.md`、`docs/arch/ci.enforce.md`；不改变
工作流协议、后端存储、DB/Git 保存边界或部署语义。

## 用户路径

1. 在现有「画布 / 对照 / YAML」工具栏点击「AI 生成」或「AI 修改」开始对应模式的新助手会话（清空旧需求和预览）；面板内切换模式则保留需求文字。
2. 右侧 AI 面板替代属性面板，暂时收起节点库以给画布留出空间，关闭后恢复。生成模式表示在当前流程增加能力，修改模式表示定向调整；均非另建或整份覆盖的快捷操作。
3. 输入自然语言需求，发送当前已应用的 WorkflowSpec 作为上下文。
4. 展示真实生成摘要、节点增删改、前后配置和候选 YAML。结果必须经过本地结构检查及现有服务端校验。
5. 删除节点或变更工作流顶层配置必须显式确认，再点击「应用草稿到当前 Editor」。
6. 应用仅更新内存编排并标记 dirty，切换「对照」供审阅。用户仍需单独点击原有保存/部署。

## 原始 Service API（历史，不是当前传输）

本节保留最初实现意图供追溯；当前端点和提供方契约见文末更正及
[Workflow AI draft 契约](../workflow-ai-drafts.md)。

- `POST /api/smart-onboarding/generate`
  - body: `{ prompt, context: { existingYaml } }`，使用现有共享类型。
  - prompt 包含模式和真实用户需求；existingYaml 包含当前完整编排，不含画布坐标。
  - 沿用共享 `fetchJson` 的登录信息及大请求压缩，不引入私有 URL、密钥或新依赖。
- `GET /api/smart-onboarding/generations/:generationId`
  - 轮询 pending/running，completed 读取 `{ yaml, summary }`；failed 显示错误。
  - 3 秒间隔，最多 15 分钟；支持 AbortSignal。关闭或上下文变化停止客户端请求，
    不宣称取消了远端已启动的生成任务。
- `POST /api/workflows/validate`
  - body: `{ spec }`。只有 valid 且返回有效 normalizedSpec 时才可审阅应用。
  - 与现有 YAML Apply 使用相同校验入口，不新增旁路保存。

公开 runner 当前不注册智能接入生成后端；在未提供该服务的环境中明确报服务未启用，
不返回模拟成功或偷偷回退到固定节点。预发的真实模型、权限和服务可用性需部署后联调确认。

## 安全与一致性

- 不自动保存、部署或执行，不调用工作流写接口。
- 保留工作流 id 和 version，模型试图修改时拒绝；保留已有节点位置，不采纳模型生成的坐标。
- 检查 YAML 语法、别名展开上限、JSON 可表示性、重复 ID、依赖引用、已知数组形式的结果分支引用和依赖环；对象形式的 `onResult` 及扩展字段原样交给权威服务端校验，不自行收窄运行时协议。
- 节点增删改、顶层字段变化均从最终 normalizedSpec 与原编排比较得出，不相信模型自报的变更范围。
- 候选规范带原始文档签名，生成期间或应用前内容、节点位置、工作流发生变化时阻止旧结果覆盖。
- 手工 YAML 存在未应用内容时禁用 AI 入口，要求先应用或重置。面板打开后才手工改动也不能应用旧草稿。
- 保存请求进行中禁止生成或应用 AI 草稿。保存完成只确认提交时快照，后续编辑不得被旧请求误标为已保存。
- 新需求或模式切换清空先前结果；请求取消、异常、过期响应不更新编排。
- 错误、超时、服务未启用时保留手工编辑和普通保存能力。原 YAML 不截断发送。
- 生成前 UI 明确提示会把当前编排和需求交给服务端配置的 AutoHeal Bot（经现有 BaaS）；不在本地验证时发送真实业务编排。

## 验收与测试

- [x] 两个工具栏入口打开嵌入式 AI 面板并显示真实 workflow id / 节点数。
- [x] 请求携带用户需求、当前完整 YAML；轮询成功、失败、超时和取消均有测试。
- [x] 无效 YAML、重复节点、依赖环、跨 workflow id/版本、服务端校验失败均拒绝。
- [x] 已有 `hitl-confirm` 等节点不会被固定模板替换；未知字段和外部配置的变更都可被审阅。
- [x] 无显式应用时编辑器不变；应用只标记 dirty，不调用保存/部署。
- [x] 手工 YAML、切换工作流、变更节点后旧草稿不可应用。
- [x] 组件/Hook 的关闭、模式变化、需求变化、删除确认有回归测试。
- [x] workflow 模块类型检查、前端测试和应用构建通过；环境限制如实记录。

原始实现的验证结果记录在本目录 `2026-11-13-workflow-ai-draft-validation.md`；该历史证据不代表当前提供方或部署已验证。

## 2026-11-14 AutoHeal/BaaS 传输更正

此前把智能接入传输替换为 AIStudio/Snapshot/Git 的 supersession 说明是错误方向，
不是当前需求，也不是发布前置条件。当前需求是 **Editor AI 与 AutoHeal 使用同一个
服务端配置的 Bot，通过现有 BaaS 生成草稿**。以
[当前 Workflow AI draft 契约](../workflow-ai-drafts.md) 和服务端端口声明为准；
不依赖已删除的实验迁移文档或共享 fixture。

- 浏览器继续使用 `GET /api/workflows/:workflowId/ai-drafts/capabilities`、
  `POST /api/workflows/:workflowId/ai-drafts`、
  `GET /api/workflows/:workflowId/ai-drafts/:draftId`、
  `POST /api/workflows/:workflowId/ai-drafts/:draftId/cancel`。
  请求仍为 `{ requestId, mode, prompt, currentYaml }`，提交/取消保留现有 CSRF 标记，
  结果仍为完整 `{ yaml, summary }`，并沿用原有只读校验接口。
- 后端端口为 `resolveTarget() -> { botId }`、`dispatch(...) -> { jobId }`、
  `poll(...) -> running | failed | completed`。`jobId` 是 BaaS 消息 ID。
  只解析有效 AutoHeal 配置中的 `autoHeal.botId`，不得选用智能接入默认 Bot 或浏览器 Bot；
  原样传递已配置 ID，不拼接当前用户后缀。目标绑定随请求持久化，恢复不得偷偷换 Bot。
- 不要求 AIStudio、Snapshot、Git/Pack 基线、隔离分支、结果提交或 taskguard 发布。
  缺少 AutoHeal Bot/BaaS/持久化配置时明确不可用；能力检查网络失败不等于配置缺失。
- 保留持久化幂等 requestId、精确 UTF-8 YAML SHA-256、请求恢复、上下文/迟到响应隔离、
  stale/确认/Apply 防护。`dispatch_uncertain` 表示 BaaS 可能已接收，远端任务仍可能继续；
  只恢复原请求/原任务，不自动再次派发，也不能承诺找回没有消息 ID 的远端结果。
- 取消/关闭停止本地等待并忽略迟到结果，已知 draftId 时尽力写本地服务端取消状态。
  BaaS 无受支持的远端停止契约；取消失败或回执丢失不能声称服务端已取消，更不能保证
  远端 Bot 已停止。所有生成/预览/应用路径均不自动保存、部署或运行工作流。

原始 UI/验证意图仍有参考价值；旧测试通过记录不证明此次提供方替换、真实 Bot、
BaaS 凭证、宿主接入、模型质量或线上部署成功。本次聚焦/完整 web 测试结果与剩余
环境验证应单独报告，不改写历史验证报告或 `docs/demos`。

### AutoHeal/BaaS 错误语义补充

能力不可用区分 `AUTO_HEAL_BAAS_NOT_CONFIGURED`（服务端配置缺失/无效）与
`AUTO_HEAL_BOT_UNAVAILABLE`（已配置 Bot 的 BaaS 绑定无效）。派发已知拒绝使用
`AUTO_HEAL_BOT_UNAVAILABLE`、`BAAS_AUTH_FAILED`、`BAAS_REQUEST_REJECTED`；
轮询失败使用 `BAAS_GENERATION_FAILED` / `BAAS_PROVIDER_REJECTED`，无效结果由
core 统一返回 `INVALID_GENERATION_RESULT`（包括解析及 schema 校验失败）。前端展示对应可操作文案，不要求用户理解裸错误码。
`BAAS_AUTH_FAILED` 也可能在轮询阶段出现，不能据此保证未派发或远端已停止。
服务端期限分别为 `EXECUTION_TIMEOUT` 和 `DISPATCH_UNCERTAIN_TIMEOUT`，编辑权限撤销为
`EDIT_PERMISSION_REVOKED`；均不能当作远端已停止或安全再次派发的证明。
未知错误保留有用的服务端原因；能力检查失败仍为未确认，不伪装为配置缺失。
`resolveTarget` 同时校验 AutoHeal 绑定和 BaaS 传输就绪情况，但只返回 `{ botId }`。
轮询只跟随已确认并冻结的 BaaS 消息，检查传输而不重新依赖当前 AutoHeal Bot 设置；
配置中的当前 Bot 改变或缺失，不得导致旧任务被换目标或无法轮询。生成全过程不访问 Git。

### 本次本地验证（2026-11-14）

工作目录为 `apps/evolverun/clawweb/public/modules/workflow`：

```sh
npx vitest run web/api/__tests__/workflow-ai*.ts web/services/workflow-ai*.test.ts web/hooks/__tests__/useWorkflowAi*.tsx web/components/__tests__/WorkflowAi*.tsx web/pages/__tests__/EditorAi*.tsx
npx vitest run web
npx tsc -p tsconfig.json --noEmit
```

- 聚焦 AI web 测试：12 个文件、164 个测试通过。
- 完整 workflow web 测试：191 个文件、2,659 个测试通过。运行中有非致命的 React
  `act`、jsdom `scrollTo` 及 Vitest 配置弃用警告；没有因此修改无关测试或配置。
- 模块类型检查通过；文档链接和拥有路径的 `git diff --check` 通过。
- 本次修改文件均不超过 1,000 行；`Editor.tsx` 相对进入任务时未改动，仍为 999 行。
  只修改拥有的 web/docs 路径，保留其他人的既有修改，未修改 `docs/demos`。
- 未验证真实 BaaS、凭证、模型质量、部署或其他宿主；以上结果不作远端停止保证。

### 后续修正：操作性失败不得丢失已提交请求身份

独立 main review 发现原 164 个聚焦测试未覆盖两条重复 BaaS 风险路径：

1. 提交回执丢失后，恢复过程的能力检查失败或返回不可用；旧实现清空 requestId，
   下次重试会创建新任务。恢复中的操作性检查必须保留原 requestId 和完整请求内容。
2. 已完成草稿的只读校验返回 HTTP 401/403/429；旧实现把所有 4xx 当作候选无效，
   丢弃 draftId 后重新生成。认证、权限、限流及裸 HTTP 错误不是确定性候选拒绝，
   必须查询原草稿并重试校验，不再次 POST 生成。

回归必须贯穿真实 Hook → service → API → HTTP 边界，验证中断恢复后多次显式重试仍
复用同一 requestId，已知完成草稿只派发一次生成，并保留真正的 YAML/schema/identity/
normalizedSpec/`valid: false` 拒绝。显式取消、需求或编辑上下文变化继续失效旧请求；
不得修改核心端口、ACL/deadline 或浏览器传输格式，错误码仍为 `INVALID_GENERATION_RESULT`。
本次修正的聚焦及完整 web 验证结果另附，不以旧 164 个通过记录代替新增回归证据。
