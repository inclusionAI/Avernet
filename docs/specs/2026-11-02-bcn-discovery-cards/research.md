# 现有存储核对与验证记录

日期：2026-11-02；修订：2026-11-03。仓库基线 `24c41c89f2bc31ba8f98a28bfe35abe9cb3eb51d`。
这是 spec 的证据与限制，不是上线验证报告。未连接业务数据库。

## 1. 现有事实及来源

下列路径均相对仓库根目录；它们说明存储现状，不要求 Python/Java 实现依赖其中的 Rust 代码。

| 事实 | 来源 |
| --- | --- |
| MySQL/OB 使用同一 MySQL migration lineage；001 不是最新全量 schema | `apps/bcs/migrations/README.md`、`apps/bcs/migrations/mysql/001_init_schema.sql` |
| group_kind 为 normal/dm，group_strategy 为 chat/manager_worker/state_machine | `apps/bcs/crates/services/bcs-group-store/src/store_mapping.rs`、001 DDL |
| 公开目录筛 public，服务层筛 active record；提供 strategy 时还筛 normal | `apps/bcs/crates/application/v1/bcs-app-group/src/service.rs::list_public_groups` |
| 现有 query 匹配 label，大小写归一、转义通配符；目录存在 1000 candidate safety cap | `apps/bcs/crates/services/bcs-group-store/src/store_lists.rs`、上述 application 服务 |
| 成员读取独立 participants 表，不以 groups.participants JSON 为准 | `apps/bcs/crates/services/bcs-group-store/src/store_reads.rs` |
| manager_worker 创建校验恰好一个 manager，不依赖 driver_bot 判定 | `apps/bcs/crates/services/bcs-group/src/application/management/mod.rs::validate_participants_for_strategy` |
| 当前 runtime binding 使用 next_group_version=2147483647，按 group_version DESC 取首条 | `apps/bcs/crates/services/bcs-collaboration-store/src/lib.rs` 的 GroupRuntimeBindingStore::get 实现 |
| v1 group create 将 actor_ids 映射为 source=manual、bot_ids 数组 | `apps/bcs/crates/application/v1/bcs-app-group/src/create.rs` |
| BCN 内部 runtime 检查 source/slot/成员及引用基数；这些不是 Discovery 的导出条件 | `apps/bcs/crates/services/bcs-collaboration-runtime/src/runtime.rs::resolve_participant_bindings`、`runtime_binding_bot_ids` |
| 现有内部 runtime 还可回退 definition.bot_id；Discovery 原样返回 YAML，不解析该字段或补写绑定 | 同上；区分内部执行语义与数据透传 |
| 定义按 env+id+version 精确读取，内部优先 normalized_json 再 yaml_text | `apps/bcs/crates/services/bcs-collaboration-store/src/lib.rs::get_record` |
| content_hash 来源为序列化 normalized JSON，而不是 YAML 文本 | 同文件 `upsert_definition_internal` |
| 当前 authoring 文档约定顶层字段和 DAG/loop 版本；Discovery 不据此设置 YAML 支持白名单 | `apps/bcs/crates/tools/bcs-cli/bcs-coordination/references/custom-collaboration-schema.md` |
| Bot-owned Provider migration 定义了 backfill/writer fence；本文读取完成迁移后的 provider_id，不新增启动扫描机制 | `apps/bcs/migrations/mysql/029_bot_provider_storage.sql` |
| Bot 已保存 agent_card/agent_card_url/agent_card_revision | `apps/bcs/migrations/mysql/039_bot_a2a.sql`、`docs/specs/a2a/minimal-card-registration-v1.md` |
| 当前 A2A 字段/扩展 fixture 使用 supportedInterfaces、protocolBinding、protocolVersion 1.0、params | `apps/bcs/crates/external-clients/bcs-a2a-http/tests/card_contract.rs` |
| 注册路径校验原 Card 并保留未知字段；没有 Card 的旧 Bot 仍可存在 | `apps/bcs/crates/external-clients/bcs-a2a-http/src/card_fetch.rs`、`apps/bcs/crates/adapters/http/bcs-http/tests/a2a_discovery_routes.rs` |
| 现有 `/bots/discover` 保留 bots/count 与内部权限，本 spec 是独立契约 | `apps/bcs/crates/contracts/bcs-protocol/tests/a2a_card_discovery_wire.rs`、上述 route tests |
| 既有公开群写入有 Provider collaboration_scope 约束；Discovery 不重复读取或验证策略 | `apps/bcs/crates/services/bcs-group-store/src/provider_scope_guard.rs` |
| service_spec 已退役，不应当作 manager 轮数/完成策略 | `apps/bcs/crates/services/bcs-group/CONTEXT.md`、`apps/bcs/specs/2026-10-27-retire-service-session-policies/spec.md` |
| MySQL/OB driver 只负责 transport，SQL 业务归属不在驱动内 | `apps/bcs/crates/plugins/bcs-db-mysql/CONTEXT.md` |

本文查询按已完成迁移的数据库结构编写；历史迁移信息用于说明列的来源，不要求 Discovery 新建 schema 扫描或上线审批流程。

## 2. 本 spec 的新设计，不是已实现功能

- 对齐上游搜索 Facade 的统一 `items[]`、混合排序、共用 limit；不新增独立 endpoint 或对外分页协议，不改变既有 BCS `/bots/discover`。
- 群内 `composition.agents: Map<uuid, AgentDependency>` 携带 bindings 的完整依赖；不把依赖追加到顶层推荐，不占推荐 limit，也不受 query/resource_type 筛选裁剪。
- 推荐 item 使用 `bot_uuid` / `group_id` 作为 id，`providerName` 在 item 或依赖包装外层，第三方 `agentId` 与可选 `party` 保留在来源 extension；LOCAL/真实 A2A interface、revision 算法；群 hash 包含成员完整 Card 及其 revision。
- 两种群只通过 groupKind 区分定义类型，不再附加执行 profile；上游自行解释定义和决定执行方式。
- 对已召回群 ID 做 public/active/normal/strategy 筛选和只读投影，保留原推荐顺序；不再做成员公开性、Provider 策略、manager/绑定/版本完整性或执行能力检查，也不新增坏数据省略与补扫链。
- `local://` + `protocolBinding=LOCAL` 是短期受控消费者约定，不是标准 A2A binding/可调用服务；不使用执行模式字段；上游必须在 SDK 初始化、接口选择和探活之前分流。
- 后续迁移为真实 A2A interface，群内 composition 可整体省略；迁移约束见 spec 附录 A。
- YAML 直接读取 yaml_text 并原样输出，不解析语法、不判断 loop/judge/HumanInput 或未知字段的支持范围；不增加 normalized/blob 转译或运行快照回退。
- local 执行不承诺复制 BCN Session、工具上下文、事件、观察者和鉴权语义。
- public 群按约定直接输出 context/YAML/依赖 Card；不增加 owner 批准、内容审批、扩展白名单、URL 审批、签名或二次鉴权。
- 本期假定渲染所需数据已具备，不追加完整性预检；Agent 下线前归档关联群等保障机制留到后续，也不作为本期上线前置条件。
- 删除固定多层安全预算、强制重新发现和 schema readiness 扫描；沿用服务既有批量/超时/大小限制与普通异常处理。

## 3. 协议版本与外部资料

设计固定 A2A 1.0，而不是跟随变化的 latest。参考资料：

```text
https://raw.githubusercontent.com/a2aproject/A2A/v1.0.0/specification/a2a.proto
https://raw.githubusercontent.com/a2aproject/A2A/v1.0.0/docs/specification.md
https://a2a-protocol.org/latest/specification/
https://a2a-protocol.org/latest/topics/extensions/
https://www.rfc-editor.org/rfc/rfc8785
```

此前 2026-11-03 修订记录：已读取官方 specification 网页（当时显示发布版本 1.0.0），并与本地 Card contract fixtures/注册实现交叉检查字段；从官方仓库直接获取 proto 的请求及重试超时，未取得固定版本 schema，**未运行 SDK validator**。网页阅读与静态示例检查不能代替正式实施时的固定版本 conformance。LOCAL 单独按本文的临时约定验证，不声称符合标准绑定或通用 SDK 可直接消费。

此前已核对上游搜索 TR 接口文档的 items、query/limit/filters、评分与错误 envelope；这里只同步必要的 Card 消费契约，不引入上游部署实现或公司内部服务信息。`context.goal/task` 与上游 Facade 一致采用开放 `Map<String,Object>`，不限制为 `id/description`；旧顶层 `goal/task` 兼容接收由 Facade 契约负责，不由本文重定义。

## 4. 本次验证与复审范围

本轮调整 spec、本记录及 JSON 示例，同步字段精简并重算群 Card 摘要；不修改原始 YAML、服务实现或数据库。复审覆盖正文、字段表、SQL、伪代码及验收项，避免在说明中取消校验，却在实施流程中继续要求校验。

本轮静态检查通过：4 个 JSON 文件、10 个 Markdown JSON 示例、13 个相对链接及示例/文档合计 426 项断言；`git diff --check` 通过。检查内容：

- 解析 4 个 JSON 示例及 Markdown 内 JSON 示例，检查统一 items、Agent/Group 包装、来源 agentId/party 与 LOCAL 标记。
- 检查两种群示例与混合响应中的 Card 一致、依赖只内嵌、bindings 仅含 uuid，已删除字段不再输出；样例的绑定映射和内容摘要仍自洽。**这些是样例质量检查，不是要求服务在每次请求时执行引用完整性验证。**
- YAML 原文与 State Machine Card 的 content 逐字节比较，并核对 sha256；不解析 YAML 或检查节点/执行能力。
- 重算成员与群 revision；当前示例仅包含 ASCII 对象键、整数和字符串，可用对应 JCS 子集编码验证，未验证通用 JCS 浮点数和 UTF-16 键排序。
- 检查 SQL 不再读取成员 visibility/actor_kind/is_deleted、Provider disabled/config 策略或定义表 content_hash；伪代码不再包含业务校验、YAML 解析、binding 摘要回填或可导出性断言。
- 检查 Markdown 相对链接、代码围栏、文件行数及 whitespace diff。

未执行：真实 MySQL/OB SQL、事务隔离、EXPLAIN/压测、A2A SDK/schema conformance、Python/Java 服务测试、上游编排、BCN Group-as-a-Bot。原因：本轮是文档修改，没有服务实现或测试数据库。本轮未访问或更新语雀；此前远端文档回读记录不代表已同步本次修改。

复审结论：成员可见性与 Provider 复核、完整性预检、内容审批、执行子集、重新签名、强制重新发现及坏数据补扫均已移除。保留群自身 public 筛选、现有身份/依赖字段、内容摘要和基本读取/序列化行为，不增加新的发布状态或保障机制。

字段精简已落实：删除 `runtimeProfile`、`binding.revision` 与 `sourceContentHash`，不保留旧字段或兼容分支。bindings 仅含 uuid；成员内容摘要只保存在 agents map，群 revision 与原始 YAML sha256 保留。定义查询不再读取 content_hash；单卡和混合响应示例已同步。

## 5. 当前身份与依赖字段

顶层 `items[].id` 直接使用 Agent 的 `bot_uuid` 或 Group 的 `group_id`；`items[].resourceType` 区分 `agent/group`。这些 ID 只在当前 BCS/env 作用域内有效，不保证是标准 UUID，也不是调用 URL。

Agent item 在 `providerName` 中携带第三方平台名称；第三方传入的 `agentId` 与可选 `party` 保留在来源 extension 的 params 中。群的 `composition.bindings` 使用 `{uuid}`；`composition.agents` 使用 `Map<uuid, AgentDependency>`，其中 `AgentDependency` 为 `{providerName, revision, card}`。uuid 取值为 BCS 内已有的 bot_uuid。按 bindings 的 uuid 去重构造 map，成员 revision 仅保存在依赖对象中；不另做闭包或版本一致性预检。依赖成员只内嵌在所属群中，不进入顶层推荐、不占 limit。

短期群仅通过 `local://` 与 `protocolBinding=LOCAL` 识别；LOCAL 地址在 A2A SDK 初始化、接口选择和探活前分流，禁止真实网络调用。长期群改用真实 A2A interface，届时上游按普通 A2A Agent/Group-as-a-Bot 消费。
