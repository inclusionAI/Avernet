---
status: implemented
date: 2026-09-23
updated: 2026-10-27
owners: [bcs]
---

# BCS 自定义协作定义副屏预览

本文描述当前 BCS Panel 的只读预览，以及仍需兼容的 schema 1 内联快照。新的助手交付使用 [Draft 固定版本 spec](../2026-09-24-collaboration-definition-draft/spec.md) 的 schema 2，不再让模型转抄完整 YAML。实现、验收及发布状态分别记录在 [tasks](./tasks.md) 和 [Draft 验收记录](../2026-09-24-collaboration-definition-draft/verification.md)。

2026-10-27 将文档从仓库根 `docs/specs/` 迁入 BCS，按现有实现修订；本次只调整文档和通用 Skill，不重新实现预览，也不把助手 profile 的本地内容视为本次已提交或部署的交付。

## 1. 背景与职责

用户需要从助手生成的协作定义查看流程图、逻辑参与者和诊断。早期 CLI 可以精确序列化完整 YAML，但实际模型复制 AixUI 时仍会改变缩进：现场文件 2174 字节且校验通过，消息中的 YAML 变成 2188 字节、14 行缩进变化。继续要求模型“原样复制”不能保证原文交付。

当前分工：

| 模块 | 职责 |
| --- | --- |
| BCS CLI | 保留离线 schema 1 producer；Draft 命令直接传输文件，协议见增量 spec |
| BCS Panel | 发布 `CollaborationDefinitionCard` / `CollaborationDefinitionPreview`，解码两种载荷、校验与展示 |
| BCS 校验服务 | YAML 解析、领域规则、编译和图数据的权威来源 |
| 宿主 | UMD 加载、消息身份、Tab 生命周期、可信 API base；可选建群回调 |
| 协作助手专用 Skill | 编写文件并交付固定 Draft 版本卡片；普通 Bot 不默认使用此流程 |

预览业务代码在 `apps/bcs/assets/panel/`，不导入 frontend 的组件或 Hook。后续已接入的“发起协作”通过宿主打开主屏现有建群浮层，独立契约见 [建群 spec](../2026-10-22-collaboration-draft-group-launch/spec.md)。预览组件自身不创建群或启动 Run。

依据：[BCS 规则](../../AGENTS.md)、[BCS 分层](../../CLAUDE.md)、[架构约束](../../../../docs/arch/arch.rules.md)、[CI 约束](../../../../docs/arch/ci.enforce.md)、[Panel 说明](../../assets/panel/README.md)、[CLI 边界](../../crates/tools/bcs-cli/CONTEXT.md)。

## 2. 当前用户体验

1. 消息只展示卡片和“预览协作”按钮。完整载荷解码后由用户点击打开副屏；历史加载、普通重渲染和新消息到达不自动开屏。
2. 卡片和按钮适应宿主可用宽度，不固定为 280px。schema 1 使用载荷标题；schema 2 点击读取前显示“自定义协作”，不批量预取历史原文。
3. 副屏默认“协作预览”；“查看定义”展示只读、高亮的“协作定义 YAML”。复制按钮仅在源码标题右侧显示，复制原始字符串，不复制高亮 DOM。
4. “定义校验通过”显示在标题下的状态行，不占独立提示块。错误、警告、校验摘要和逻辑参与者仍单独展示。
5. 有效且有 graph 时显示图；有效但缺图时明确说明，不伪造图。无效定义显示诊断并保留源码。空白 schema 1 文本保留原文且不发起校验。
6. 源码读取成功后的校验网络失败不丢弃已核对原文；hash 不匹配的 Draft 内容不作为可信源码展示或复制。
7. schema 2 校验通过后右上角提供“发起协作”。它只调用宿主 `onOpenCreateGroup({draft})`；未接入宿主显示提示。schema 1 不显示该入口，也不自动上传为 Draft。

源码是权威文本，图和校验结论是当前服务端的派生结果。预览不代表真实 Bot 已绑定、已经运行或取得执行授权；运行限制 warnings 必须显示。

### 2.1 Loop 逻辑视图

流程图与建群预览采用一致的逻辑呈现：根据服务端 `graph.loops`、执行实例的逻辑节点及 `loop_path`，将各轮实例归并为一份循环体，保留轮数、continue 回边、break / exhausted 出口和嵌套容器。节点详情显示逻辑节点，不把每轮实例全部展开给用户。

布局只投影现有图，不改 YAML、编译结果、校验摘要或执行语义。历史响应缺少完整 Loop 描述时保留原执行投影；元数据存在但损坏时显示错误，不编造结构。图支持缩放、平移、适配宽度和查看详情，不支持编辑节点或绑定。

### 2.2 Tab 和请求生命周期

- 卡片从宿主 DOM 的 `data-message-id` 读取来源，缺失时使用挂载期本地键；不信任业务载荷提供的 Session 或消息身份。
- Tab 按来源消息与固定版本构造无歧义 ID：schema 1 使用 snapshotId，schema 2 使用 draftId/revision。同版重复打开聚焦，不同版互不覆盖；没有 focus 方法的旧宿主只重建目标只读 Tab。
- 相同 Tab 身份对应不同快照内容时报告冲突，不覆盖已有内容。宿主 API base 改变时使旧内容和请求失效。
- 来源卡片卸载时关闭其持有的只读 Tab；请求通过 AbortController、请求序号和内容身份隔离，迟到响应不能污染其他版本或页面。
- 重新打开/刷新按原版本读取和校验，不选最新消息或 Draft head。当前 Tab 的复制、子 Tab 切换不增加校验请求。
- 已展示内容不承诺远程擦除；历史“可复现”指 YAML 固定，当前校验器与布局可能随版本变化。

## 3. schema 1 历史快照契约

```typescript
interface CollaborationDefinitionSnapshot {
  schemaVersion: 1;
  documentId: string;
  snapshotId: string;
  parentSnapshotId?: string;
  title?: string;
  definitionYaml: string;
}
```

- documentId / snapshotId / parentSnapshotId 为规范 UUID，不限制 UUID 版本；父快照不能等于当前快照。
- title 可省略，提供时非空白且至多 120 个 Unicode 码点；definitionYaml 必须是原始字符串，可为空或无效。
- schema 1 拒绝未知业务字段、缺失字段和错误类型。宿主注入的 props 与业务载荷分开解码。
- YAML UTF-8 上限 65,536 字节，完整序列化 AixUI 上限 262,144 字节；超限拒绝，不截断或分拆。原文不得 trim、重新 dump、规范化换行或字符。
- 载荷不携带 token、服务地址、graph、校验结论、作者或执行权限。documentId / parentSnapshotId 只表达谱系，当前没有父消息导航、分支或编辑 UI。
- 历史内联内容继承原消息的存储和可见性；Panel 不凭快照 ID 去读取其他消息。schema 2 的公开固定版本读取按 Draft 契约处理，不沿用消息 ACL。

### 3.1 AixUI 与序列化

```text
<AixUI type="card" component="bcsPanel.CollaborationDefinitionCard" params='SERIALIZED_PAYLOAD' />
```

组件名和预览导出保持稳定，载荷不能选择 CDN、入口模块或宿主 API 地址。

schema 1 producer 先生成紧凑 JSON，再将 ECMAScript `\s`、`&`、`<`、`>`、单引号替换成 JSON `\uXXXX` 转义，最后放入单引号 params。只调用 `JSON.stringify` 不能避免旧 SDK 的空白折叠。接收链路解码一次，不追加 YAML 的 HTML 实体解码；HTML、Markdown、JSX 只作为文本。

此 serializer 保证工具产物的可逆性，**不保证模型再次转抄产物时字节一致**。共享向量覆盖精确编码；生产交付已改用 Draft，不能把历史失败重新列成“继续加强复制提示词”的待办。

### 3.2 保留的离线 CLI

```text
bcs-cli collaboration preview <file>
  [--title <title>]
  [--document-id <uuid>]
  [--parent-snapshot-id <uuid>]
  [--snapshot-id <uuid>]
```

命令在网络发现和认证之前执行。默认 stdout 为完整 schema 1 AixUI，显式 `--json` 返回 snapshot/message；`--no-json` 保持消息模式。父快照参数要求显式 documentId；文件、UTF-8、ID 或容量错误非零退出，不输出部分成功标签。

该命令不校验 YAML 语义、不上传、不发送聊天消息、不创建群或 Run。保留它是因为已有消费者；协作助手新交付不使用它，改为 `collaboration draft create/update` 后显式 `collaboration draft preview`。既有 validate/create/run 语义不变。

## 4. 可信宿主与校验接口

宿主通过 props/data 中的 `apiBaseUrl` 或 `baseUrl` 提供可信 BCS 基址，并由卡片传入预览。没有配置时默认 `/bcnproxy`；支持根基址及已经以 `/collaboration` 结尾的基址，例如 `/api/v1/collaboration`，不重复拼接命名空间。基址来自宿主配置，不来自消息/YAML。

| 载荷 | 数据与校验请求 |
| --- | --- |
| schema 1 | 以原始 `definition_yaml` POST `<base>/collaboration/definitions/validate` |
| schema 2 | GET 指定 Draft revision，完整性核对后 POST 同版 `/validate`，不提交替代 YAML |

响应支持原生扁平结果和 V1 envelope；decoder 核对完整业务结构。schema 2 无 Draft 专用认证头，细节见增量 spec。网络、超时、HTTP 错误、引用不可用、完整性失败、无效 YAML 和畸形响应分别报告。开始新的校验时旧成功图失效，不能把上一次结果冒充当前结果。

现有 `window.aixBridge.openPanelTab`、可选 focus/close 方法和 `window.aixGetLibraryCdn('bcsPanel')` 提供打开只读 UMD 的能力。助手提供方负责绑定资源，不通过本功能探测或补装私聊资源。主屏建群的宿主回调属于已独立实现的后续能力，不能再用“整个迭代绝不修改 frontend”的旧范围描述它。

## 5. 帮助工作流边界

- 通用 `bcs-coordination` 保留 YAML schema、运行/建群能力和按需 Draft 操作说明，不把 Draft 或卡片交付作为所有 Bot 的默认步骤。
- 专用 `avernet-collaboration-help` 面向已绑定 bcsPanel 的协作助手：读取 schema、写文件、create/update 保存及校验、忽略其 message、显式 preview 固定版本、正常回复 preview.message。不得由模型转抄完整 YAML、CLI 发送最终消息或寻找私聊 Session。
- 普通 Bot 即使使用 Draft，也只需消费 CLI 的版本和校验结果。CLI 是否返回展示字段与该 Bot 是否需要交付卡片是两件事。
- 助手 profile 位于 `singlebox/agents/avernet-help/`，由既有按 source 的 Skill 分发机制提供；稳定身份和资源绑定由助手提供方管理。该目录的本地修改不属于本次文档/通用 Skill 提交。

## 6. 验收索引与兼容边界

| 原 AC | 当前判定范围 |
| --- | --- |
| AC-01～11、15 | 卡片点击、状态、生命周期和源码保留；以 Panel 组件测试及现场记录为证据 |
| AC-12～14、16～17 | schema 1 serializer、容量与 CLI 兼容测试；模型搬运长 YAML 的生产链路已被 Draft 替代，不宣称其历史问题已修复 |
| AC-18～19 | 预览无运行副作用；schema 1 消息可见性与 schema 2 公开版本读取分别执行各自契约 |
| AC-20～24、26～27 | 既有 Skill 分发、隔离、稳定身份和地址发现；profile 内容的交付状态另计 |
| AC-25 | 助手交付已转为固定版 preview.message，证据与新流程待验收项见 Draft tasks |

主要实现：[Card](../../assets/panel/src/collaboration-definition/CollaborationDefinitionCard.tsx)、[Preview](../../assets/panel/src/collaboration-definition/CollaborationDefinitionPreview.tsx)、[Loop 布局](../../assets/panel/src/collaboration-definition/loopLayout.ts)、[serializer](../../crates/tools/bcs-cli/src/collaboration_snapshot.rs)、[共享向量](../../tests/fixtures/collaboration-definition-snapshot/vectors.json)。

先部署可消费相应 schema 的 Panel，再启用生产者。已发布消费者保留 schema 1；不能把 schema 2 降级成模型搬运长 YAML。编辑、review 和现有群 apply 的未实现部分统一见 [roadmap](./roadmap.md)。
