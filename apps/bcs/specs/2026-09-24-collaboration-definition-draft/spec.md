---
status: implemented
date: 2026-09-24
updated: 2026-10-27
owners: [bcs]
---

# 自定义协作定义 Draft：固定版本与只读分享

当前交付链路：**CLI 直接上传 YAML 文件，BCS 保存不可变 revision；协作助手显式调用固定版本 `draft preview`，正常回复其短 AixUI；Panel 按 ID 和 revision 读取原文。** 模型不转抄完整 YAML，CLI 不承担聊天消息发送。

本文以当前代码为准：读取凭据、专用 header、签名密钥及旧四字段卡片机制已移除；现有 Bot 管理认证保留。早期实现与验收保存在 [verification](./verification.md)，其中的旧鉴权及发布状态不是当前契约。实施状态见 [tasks](./tasks.md)，后续编辑/review 见 [roadmap](./roadmap.md)。

## 1. 背景与边界

现场文件经过 CLI 校验，但模型把完整 AixUI 复制为用户消息时改坏缩进。回归样例原文 2174 字节，消息 2188 字节、14 行变化。Base64、长 JSON 或更强“原样复制”提示仍依赖模型搬运原文；固定引用将原文字节交付转移到文件传输和存储。

```text
已绑定 bcsPanel 的协作助手收到编写/修改请求
  → 写独立 YAML 文件
  → create/update 直接上传、保存固定版、校验；忽略其 message
  → draft preview <id> --revision <已确认版本>
  → 核对本次 preview 的校验结果，正常回复 preview.message
  → 用户点击卡片
  → Panel 读取同一固定版并核对 hash/长度，再请求同版校验
```

保存、定义有效、取得卡片、消息持久化是独立事实。短引用仍可能被模型抄错，错误时明确失败，不猜 ID/版本，不重建原文或回退 head。

### 1.1 职责

| 模块 | 职责 |
| --- | --- |
| BCS service-api / application / core / store | Draft、Bot 归属、CAS、固定版本读取与校验、撤销和删除 |
| BCS HTTP adapters | 原生管理/读取协议；V1 固定版只读投影；身份解析和响应映射 |
| bcs-cli | 文件上传/下载、响应完整性核对、分阶段结果和短卡片生成；不发送聊天消息 |
| BCS Panel | 消费 schema 1/2，显示原文、逻辑 Loop 图和诊断；可选通知宿主打开建群浮层 |
| 通用 Skill | 按需介绍 Draft 存储/版本能力，普通 Bot 消费结构化结果 |
| 助手专用 Skill | 唯一的默认卡片交付工作流；资源绑定由助手提供方配置 |

Draft 的数据和规则只归 BCS，不依赖 Backend、Gateway、独立 Engine Runtime 的新存储或委托。BCS adapter → application → core/repo → store/plugin 的边界不变，具体实现由 bootstrap 装配。

[只读预览 spec](../2026-09-23-collaboration-definition-preview/spec.md) 定义共用 UI。后续已实现的 [主屏建群交互](../2026-10-22-collaboration-draft-group-launch/spec.md) 涉及 frontend，属于独立能力，不应继续描述为整个迭代“未改 frontend”。预览和 Draft 保存本身不创建群或启动 Run。

依据：[BCS 规则](../../AGENTS.md)、[分层](../../CLAUDE.md)、[架构约束](../../../../docs/arch/arch.rules.md)、[CLI 边界](../../crates/tools/bcs-cli/CONTEXT.md)、[Draft context](../../crates/services/bcs-collaboration-draft/CONTEXT.md)、[原生 HTTP 契约](../../api-contracts/collaboration-drafts/v1.yaml)。

## 2. 原文、版本和存储

| 对象 | 当前语义 |
| --- | --- |
| Draft | draft_id、owner_bot_id、head_revision、创建时间、删除标记 |
| Revision | draft_id、正整数 revision、definition_yaml、可选 title、content_hash、size_bytes、created_by、created_at |
| 可用状态 | revision 的 revoked；撤销不修改原文或版本号 |
| 消息引用 | schemaVersion=2、draftId、revision；不包含 YAML、标题、地址或凭据 |

- ID 是小写规范连字符 UUID；revision 范围为 1～9007199254740991。ID 的唯一范围是当前数据库，不额外持久化部署签名身份。
- 首版为 1。CAS 仅在 head=n 时原子写入 n+1 并推进 head；同基线最多成功一次。
- 原文最多 65,536 UTF-8 字节。可保存空白、语法或领域无效的 YAML；非法 UTF-8、超限拒绝。不得 trim、重排、重新 dump、转换换行或 Unicode 规范化。
- content_hash 为原始 UTF-8 字节的 SHA-256 小写十六进制；size_bytes 按相同字节计算。存储、CLI 和 Panel 都按原文核对。
- title 省略/null 表示无标题，字符串须非空白且至多 120 Unicode 码点；更新不从 head 隐式继承。
- 没有聊天 sessionId、origin、群基线、appId、用户委托或运行状态字段。

SQLite 迁移 [036](../../migrations/sqlite/036_collaboration_drafts.sql) 和 MySQL [035](../../migrations/mysql/035_collaboration_drafts.sql) 已存在。原文和元数据存于 revision 的 payload_json，撤销保存在独立 revoked 列。早期 read_token 曾位于 JSON 内，并非独立数据库列；新记录不再写入，旧 JSON 的未知字段可忽略。移除读取凭据无需新迁移，不改写已提交迁移的编号、内容或 checksum。

创建/CAS 使用有界事务和主键查询；校验及网络操作不在事务内。公开读取按 Draft/版本点查，不扫描所有资源，不增加后台清理或通用文档索引。存储失败必须上报，不能先返回保存/撤销成功。

## 3. 访问、撤销和配置

### 3.1 Bot 管理与公开固定版本读取

创建、元数据、追加、取得引用、撤销、删除沿用既有 Bot 认证，已存在资源要求 owner。owner 来自服务端解析的身份，请求体中的 Bot 名称、用户或 Session 不授予权限。共享助手的同一 Bot 管理身份不等于不同私聊用户之间的隔离。

**读取原文和固定版校验不要求凭据，只需 draftId + revision。** 不发送或校验 `X-BCS-Draft-Read-Token`，也不引入替代 header/query token。引用不是授权凭据；已知 ID/版本的调用者可读该版，但不能因此管理资源或操作群。读取不继承原会话 ACL，勿保存不应公开分享的内容。

不存在、已撤销或已删除的版本统一不可用；owner 也不能通过公开读取接口绕过撤销。重新打开/刷新检查当前可用状态，响应 `Cache-Control: no-store`。已下载或正在显示的原文不能远程追回。

撤销以单个 revision 为单位，重复操作幂等，不影响其他版本；没有恢复分享接口。迟到 create/update/preview 不重置撤销，客户端不得自动另存新版绕过。删除移除该 Draft 的原文并保留最小 ID/owner 删除标记，防止迟到 PUT 复活。消息删除、退群和删除聊天不联动删除 Draft。

### 3.2 默认开关与 503

当前默认关闭，通过现有 BCS 配置显式启用：

```toml
[collaboration_drafts]
enabled = true
```

省略小节、未设置 enabled 或设为 false 都不装配 Draft 服务；合法请求返回 HTTP 503 `DRAFT_SERVICE_UNAVAILABLE`。关闭不删除数据，也不改变已存撤销状态。仓库本地配置 `configs/bcs-config-local.toml` 显式启用，示例配置保持关闭；SQLite/MySQL 使用同一开关。

没有 deployment_id、active_key_id、key_files 或 Draft 密钥文件依赖。bootstrap 不初始化、读取或删除旧 `*.draft-keys.json`；保留的旧文件不影响当前启动。旧签名配置不是现行配置契约，升级需使用当前配置结构。

## 4. API 与历史 CLI 兼容

### 4.1 原生 HTTP

基址相对 BCS 为 `/collaboration/drafts`。完整 DTO 和错误见 [OpenAPI](../../api-contracts/collaboration-drafts/v1.yaml)。

| 方法 / 后缀 | 权限 | 行为 |
| --- | --- | --- |
| PUT /{id} | Bot；已有 ID 要求 owner | 创建第 1 版；同内容/标题重试返回原第 1 版，不同内容 409 |
| GET /{id} | owner | 元数据/head/删除标记；历史预览不用 head |
| POST /{id}/revisions | owner | definition_yaml、可选 title、必填 expected_revision；CAS 追加 |
| GET /{id}/revisions/{n} | 公开 | 指定原文、标题、hash、字节数和创建元数据 |
| POST /{id}/revisions/{n}/validate | 公开 | 不接收替代 YAML，校验已存原文 |
| GET /{id}/revisions/{n}/reference | owner | 未撤销固定引用，附旧 CLI 兼容字段 |
| DELETE /{id}/revisions/{n}/share | owner | 永久撤销该版读取 |
| DELETE /{id} | owner | 删除原文并保留最小 tombstone |

创建首次 201、同内容重试 200；即使 head 前进仍比较第 1 版。先核对 owner 再比较内容。更新冲突/结果未知时 CLI 只核对 n+1 的原文、hash、标题；匹配仅证明内容已保存，不归因为本次请求。不同为冲突，不能读取为未知；不改查 head、不自动提高基线或合并。

固定版校验结果包含 draft_id、revision、content_hash、validated_at、validator 和 validation。定义结论在 validation.valid；校验使用当前部署的规则/能力，源码固定不等于历史校验结论永久不变。

### 4.2 V1 只读投影与宿主 base

BCS 另有 `/api/v1/collaboration/drafts/{id}/revisions/{n}` 和同版 `/validate`，返回 V1 envelope，复用相同公开读取和校验服务，不要求 Human browser context。它们不是 Draft 管理或建群接口。

Panel 使用可信宿主提供的 apiBaseUrl/baseUrl，默认 `/bcnproxy`。支持 BCS 根或已经以 `/collaboration` 结尾的基址，不从卡片接收服务 URL，也不自动尝试其他部署。响应兼容原生结果和 V1 envelope。请求不带 Draft 专用头，设置 no-store、禁止重定向并处理取消/超时。

### 4.3 保留已部署 CLI 的兼容

新的领域引用、CLI 卡片和 Panel 归一化结果只有三个字段。原生 `/reference` HTTP 响应仍带废弃的 readToken：值固定为 32 个零字节的 base64url 表示，用于满足已部署 CLI 的必填/编码检查。

该占位值只存在于 HTTP adapter，不落库、不签名、不鉴权，不需要作为请求头发送。旧 CLI 可以继续产出含该字段的卡片，新 CLI 丢弃它；Panel 1.7.1 和当前 frontend 读取核心三字段、丢弃额外字段。因此不能把“HTTP 兼容响应仍有 readToken”误写为“仍要求读取凭据”。服务端和 Panel/宿主消费方需配套升级，旧 CLI 可独立延后更新。

## 5. CLI 当前行为

```text
bcs-cli collaboration draft create <file> --draft-id <uuid> [--title <title>]
bcs-cli collaboration draft update <id> <file> --expected-revision <n> [--title <title>]
bcs-cli collaboration draft get <id> --revision <n> --output <file>
bcs-cli collaboration draft validate <id> --revision <n>
bcs-cli collaboration draft preview <id> --revision <n>
bcs-cli collaboration draft revoke <id> --revision <n>
bcs-cli collaboration draft delete <id>
```

所有命令沿用现有地址发现、Bot 认证和 ws/wss→http/https 转换，无聊天 Session 前提。当前 CLI 建立客户端仍使用既有 Bot 认证；服务端公开读取不意味着此次改动把 CLI 全局认证流程改成匿名。

| 命令 | stdout / 副作用 |
| --- | --- |
| create/update | 一次读取文件，保存核对，再校验和取引用；短 JSON 包含 saved、matched_existing_revision、draft_id/revision、hash/size、phase、validation，以及成功时的 reference/message |
| get | 核对原文后原子写入新路径，拒绝覆盖；stdout 仅版本元数据和 output 路径 |
| validate | 固定版校验对象，定义结论为 validation.valid；不含卡片 |
| preview | 重读、校验并取固定引用，不追加 revision；返回结构与 create/update 的准备结果一致，结论路径是 validation.validation.valid |
| revoke/delete | 执行明确管理操作，返回 ID/版本及 revoked/deleted 结果 |

当前 **create/update 仍然返回 message**，未新增 --panel 参数，也未更改 CLI 输出契约。助手不依赖该字段，并不等于 CLI 已移除它。成功 stdout 不含完整 YAML，也不代表已发送消息。

保存后校验无效/不可用或取引用失败时可非零退出并保留 saved=true、固定版和失败 phase；不回滚保存，也不能重新 create 伪装重试。preview 允许返回无效定义的诊断卡片，故 phase=ready 或存在 message 不能单独证明有效。

get 核对 ID/版本、UTF-8、长度和 SHA-256，再以临时文件原子落盘，不覆盖已有目标。修改从下载文件进行。旧离线 `collaboration preview <file>` 保留 schema 1 语义，与联网 `collaboration draft preview` 不同。

## 6. 引用与 Panel

```typescript
interface CollaborationDefinitionReference {
  schemaVersion: 2;
  draftId: string;
  revision: number;
}
```

组件名保持 `bcsPanel.CollaborationDefinitionCard` / `CollaborationDefinitionPreview`。核心字段必须符合规范 UUID 和正安全整数。Panel 对历史载荷归一化为以上三字段，忽略额外字段，不把 readToken、URL 或其他扩展传给请求。CLI 使用统一 serializer；不要求模型手拼标签。当前最大规范引用 fixture 为 176 UTF-8 字节；历史四字段 234 字节的实测记录只作历史证据。

- 点击后读取固定版，核对 ID、revision、UTF-8 字节数、SHA-256 和标题边界；随后校验同一版且核对校验响应 hash。
- 标题由存储返回，消息不重复保存。旧消息绝不自动读 head、替换成新版或重建 YAML。
- 公开读取失败、撤销/删除、服务未启用、网络、完整性、校验无效和畸形响应分开报告。失败不回退另一接口或其他身份。
- Tab、请求取消、逻辑 Loop、高亮源码、复制与状态展示见 [预览 spec](../2026-09-23-collaboration-definition-preview/spec.md)。卡片额外字段变化归一化后不再作为凭据状态触发重鉴权。
- schema 1 历史载荷保留只读能力，不导入、修改或自动修复。
- schema 2 的“发起协作”只触发宿主建群回调。frontend 主屏允许在 Draft 初始文本上编辑并提交当前文本，不写回 Draft；建群成功与预览成功是不同结果。

## 7. Skill 分工与助手交付

通用 `bcs-coordination` 只在需要保存/下载/管理固定版本时引导读取 Draft 参考；普通 Bot 无 bcsPanel 也可消费 CLI 标准结构化结果。不能默认要求所有 Bot 发卡片或预先保存 Draft。

专用 `avernet-collaboration-help` 面向已配置 bcsPanel 的助手，当前工作区流程为：

1. 读取已安装的公共 schema 和专用编写参考，写独立 YAML 文件，仅使用逻辑 binding。
2. create/update 保存文件，读取 saved、phase、draft_id、revision 与 validation；**忽略其 message**。修改先 get 固定版，update 明确 expected-revision。
3. 保存及校验通过后，**显式执行 `draft preview <id> --revision <n>`**，参数来自已确认保存的结果，不猜版本或查 head。
4. 核对本次 preview 的 phase=ready、validation.validation.valid=true、非空 message；只正常回复这次 preview.message，可附说明。不能回退使用 create/update.message，不调用 CLI 发最终消息，不找私聊 Session。
5. 无效定义仅在明确诊断预览诉求下交付并说明错误。保存后失败保留版本恢复；消息重试复用 preview.message，必要时重新 preview 同版，不重复保存。
6. 最多自动修复两轮，不为通过校验删掉用户要求的 judge、提前退出或其他语义。已撤销版本不自动另存绕过。

本次只提交通用 Skill 和 BCS 文档；`singlebox/agents/avernet-help/` 的专用内容仍为独立本地变更，提交/刷新/真实模型验证不能由本次文档勾选代替。助手提供方负责资源绑定，不增加加载探测或跨服务接线。

## 8. 验收映射

| 编号 | 当前条件 |
| --- | --- |
| D-AC-01～02 | 文件→存储→get/Panel 字节/hash 一致；模型只传短引用，坏引用明确失败 |
| D-AC-03～05 | 同 ID 重试、CAS 原子性、固定 n+1 核对和错误传播；不重置作者/撤销状态 |
| D-AC-06 | Bot 管理隔离保留；固定版读取/校验公开且无 Draft 专用请求头；无管理或执行越权 |
| D-AC-07～09 | 撤销/删除幂等、不复活；无效 YAML 可保存；校验精确对应原文 |
| D-AC-10 | 助手私聊真实投递/历史重开；按用户要求暂缓，不能以群聊代替 |
| D-AC-11 | 群聊正常回复、固定版读取与原文复制；历史实测已记录 |
| D-AC-12～13 | 三字段引用、176 字节边界、历史 readToken 归一化、旧 CLI 与 schema 1 兼容 |
| D-AC-14～16 | Tab/版本/宿主隔离、错误状态、Loop 逻辑视图、源码高亮与原样复制；预览无运行副作用 |
| D-AC-17 | 当前 CLI 七命令行为与分阶段失败、旧命令不变、无聊天发送副作用 |
| D-AC-18～20 | Skill 分工、显式 preview 交付、地址发现和专用内容隔离；本地分发与实际刷新分开验收 |
| D-AC-21 | 现场原文和最大引用自动化；新增显式 preview 流程尚需真实模型复验 |
| D-AC-22 | 配置开关、数据重启/撤销持久化、旧 JSON/CLI 兼容、no-store 与发布范围准确记录 |

历史群聊、自动化、未通过门禁和迁移编号例外保存在 [verification](./verification.md)，不改写为本次通过。当前发布顺序为 BCS API/配置与 Panel/宿主配套，再启用相应 Skill；保留数据库与已存版本。回滚到要求读取凭据的旧构件与新卡片不兼容，不能声称无条件回滚；跨版本演练仍未实测。已有 release tag 不证明每个部署环境已更新。
