# Workflow Editor AI 草稿：本地验证记录

日期：2026-11-13（Asia/Shanghai）

## 范围与结论

完成 ClawWeb `@avernet/workflow` 现有 Editor 的 AI 生成 / 修改入口、嵌入式面板、
真实异步生成 Service API 消费、校验、差异审阅和内存应用。
HTML Demo 保留为静态交互评审材料，不用它代替实际集成。

本次不新增模型提供方、不修改后端协议、存储、部署或发布链路。
**代码已在本地验证；没有提交、推送或部署到预发，未执行真实模型联调。**

## 自动化验证

在 `apps/evolverun/clawweb` 执行：

```bash
npm run check --workspace @avernet/workflow
npm test --workspace @avernet/workflow -- --run web --maxWorkers=2
npm run build --workspace @avernet/workflow
npm run build --workspace @avernet/clawinsight
npm run build --workspace @avernet/clawweb
```

| 验证 | 结果 |
| --- | --- |
| workflow TypeScript check | 通过 |
| workflow 前端测试 | AI 定向回归 6 个文件、77 个测试通过；全量 web 运行 185 个文件 / 2572 个测试，其中 183 个文件通过、3 个既有 SQLite 集成测试因 Node 23 的 better-sqlite3 原生 binding 缺失失败 |
| AI 服务测试 | 31 个测试通过 |
| AI 请求生命周期 Hook | 13 个测试通过 |
| AI Drawer | 16 个测试通过 |
| AI 差异预览 | 9 个测试通过 |
| Editor 集成及保存并发保护 | 7 个测试通过 |
| Editor / Drawer / Hook 的真实模式会话集成 | 4 个测试通过 |
| workflow / clawinsight / clawweb 构建 | 通过 |
| `git diff --check` | 通过 |
| 新增 / 修改源码行数 | 全部小于 1,000 行 |

前端测试中现有 jsdom `window.scrollTo` 提示、构建中大于 500 kB 的 chunk 提示
不影响上述结果；未为通过验证而放宽任何门禁。

覆盖的关键路径：

- 实际需求和完整当前 YAML 的生成请求、pending/running 轮询、completed/failed。
- 15 分钟超时对提交、轮询、校验三个请求阶段均生效，并传递同一个 AbortSignal。
- 请求取消、非协作传输返回旧响应、关闭/切换模式/需求/工作流后的旧请求隔离。
- 无效 YAML、重复节点、未知依赖、依赖环、跨 ID / 版本和 normalizedSpec 身份变化拒绝。
- 保留原有对象形式 `onResult`，由服务端裁决其协议；不抹掉人工确认等既有配置。
- 从最终 normalizedSpec 计算节点和元数据差异；删除 / 顶层配置变化需要确认。
- 元数据逐字段前后值、缺失字段、null / false / 0 的正确显示。
- 未显式应用不改变编辑器；应用仅标记 dirty，不触发保存 / 部署。
- 未应用的手工 YAML、已过期草稿、正在进行的保存都阻止 AI 覆盖。
- 旧保存请求完成后不清除后续修改的 dirty；精确提交快照保存仍正常清除 dirty。
- 工具栏重复选择模式会开启新会话、取消旧请求；面板模式选择与下一次生成的实际 mode 一致。
- 未提供生成服务的环境明确失败，不返回固定节点作为伪生成结果。

## 真实编译页面的浏览器验收

使用构建后的 ClawWeb 页面和独立的本地 HTTP 测试夹具；夹具位于临时目录，
不进入产品源码，也不连接真实模型或业务工作流。
工作流 ID 为 `ai-editor-fixture`，页面明确标注为本地合成验收数据。

已观察到：

1. 现有 Editor 工具栏出现 AI 生成 / AI 修改，AI 面板与画布共存。
2. 生成中有取消状态；异步结果显示节点新增和修改，未应用时原图仍为 3 个节点。
3. 面板打开后手工修改 YAML，生成和 AI 应用按钮均禁用，并显示冲突原因。
4. 手工 YAML 应用改变原编排后，旧 AI 预览显示已过期，不能应用。
5. 显式应用新草稿后画布 / YAML 增至 4 个节点，并显示“未保存”和“尚未保存或部署”。
6. 当前默认窄窗口下收起手工节点库、限制 AI 面板宽度，保留可操作画布；未宣称执行 1440px 等其他尺寸的视觉验收。
7. 本地请求记录仅包含读取、生成、轮询和校验，无工作流保存、部署或执行请求。

截图在本次 Codex 可视化目录：

- `workflow-ai-editor-preview.png`：画布与 AI 节点差异预览。
- `workflow-ai-applied.png`：应用后未保存状态。

## 独立审查

作者与审查分别进行。首轮发现对象分支协议兼容、保存请求交错两个高优先级问题，
已修复并加入回归测试。随后发现的工具栏 / 面板模式同步问题也已修复，并以真实组件集成测试闭环。
独立 reviewer 最终结论为 APPROVE：原 2 个 HIGH、1 个 MEDIUM 均关闭，无未解决发现；
其独立复跑了相关回归用例，其中最后一轮模式会话集成 4 / 4 通过。

## 未执行的验证及发布条件

- 未向预发生成服务发送真实业务 YAML，未检验真实模型输出质量、权限及长耗时表现。
- 公开 bootstrap 不注册智能接入生成服务；该环境中明确报服务未启用。
  使用本功能的实际部署必须已配置既有智能接入生成 API。
- 没有修改后端，因此未运行后端 / BCS / Singlebox 全量测试；本记录不宣称仓库全量 CI 通过。全量 web 的 3 个失败均为既有 better-sqlite3 原生 binding 环境问题，不涉及本次 AI 功能。
- 合并发布后仍需在授权环境验收智能接入服务可用性、编辑权限、模型返回结构和原有保存 / 部署链路。
