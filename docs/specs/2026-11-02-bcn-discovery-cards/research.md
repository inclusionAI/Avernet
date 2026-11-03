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
| source 必须 manual；检查 slot/群成员；被节点引用的 slot 恰好一个 Bot | `apps/bcs/crates/services/bcs-collaboration-runtime/src/runtime.rs::resolve_participant_bindings`、`runtime_binding_bot_ids` |
| 现有内部 runtime 还可回退 definition.bot_id；本文不支持这类历史导出 | 同上；这是明确的本期兼容性限制 |
| 定义按 env+id+version 精确读取，内部优先 normalized_json 再 yaml_text | `apps/bcs/crates/services/bcs-collaboration-store/src/lib.rs::get_record` |
| content_hash 来源为序列化 normalized JSON，而不是 YAML 文本 | 同文件 `upsert_definition_internal` |
| authoring YAML 顶层受限、不含真实 Bot UUID；DAG 与 loop 版本分开 | `apps/bcs/crates/tools/bcs-cli/bcs-coordination/references/custom-collaboration-schema.md` |
| Bot-owned Provider projection 需先 backfill/writer fence，不能只看列存在 | `apps/bcs/migrations/mysql/029_bot_provider_storage.sql` |
| Bot 已保存 agent_card/agent_card_url/agent_card_revision | `apps/bcs/migrations/mysql/039_bot_a2a.sql`、`docs/specs/a2a/minimal-card-registration-v1.md` |
| 当前 A2A 字段/扩展 fixture 使用 supportedInterfaces、protocolBinding、protocolVersion 1.0、params | `apps/bcs/crates/external-clients/bcs-a2a-http/tests/card_contract.rs` |
| 注册路径校验原 Card 并保留未知字段；没有 Card 的旧 Bot 仍可存在 | `apps/bcs/crates/external-clients/bcs-a2a-http/src/card_fetch.rs`、`apps/bcs/crates/adapters/http/bcs-http/tests/a2a_discovery_routes.rs` |
| 现有 `/bots/discover` 保留 bots/count 与内部权限，本 spec 是独立契约 | `apps/bcs/crates/contracts/bcs-protocol/tests/a2a_card_discovery_wire.rs`、上述 route tests |
| Provider public group 边界要求 collaboration_scope standard，非法配置失败关闭 | `apps/bcs/crates/services/bcs-group-store/src/provider_scope_guard.rs` |
| service_spec 已退役，不应当作 manager 轮数/完成策略 | `apps/bcs/crates/services/bcs-group/CONTEXT.md`、`apps/bcs/specs/2026-10-27-retire-service-session-policies/spec.md` |
| MySQL/OB driver 只负责 transport，SQL 业务归属不在驱动内 | `apps/bcs/crates/plugins/bcs-db-mysql/CONTEXT.md` |

注意：migration README 的部分条目落后于当前目录。实现应核验**完整实际 migration 链与部署 schema**，不能只读 README 中某段“当前最高版本”。

## 2. 本 spec 的新设计，不是已实现功能

- 对齐上游搜索 Facade 的统一 `items[]`、混合排序、共用 limit；不新增独立 endpoint 或对外分页协议，不改变既有 BCS `/bots/discover`。
- 群内 `composition.agents: Map<resourceId, AgentCard>` 携带 bindings 的完整依赖；不把依赖追加到顶层推荐，不占推荐 limit，也不受 query/resource_type 筛选裁剪。
- BCN extension URI、resource identity、local/remote、revision 算法；群 hash 包含成员完整 Card 及其 revision。
- manager-worker/state-machine 的 local runtimeProfile 与上游适配要求。
- 公开群只读 SQL 投影、profile 校验、坏数据整项省略与元数据降级；keyset 仅用于内部扫描，已召回候选批量复核后保留原推荐顺序。
- `local://` + `protocolBinding=LOCAL` + `execution.mode=local` 是短期受控消费者约定，不是标准 A2A binding/可调用服务；上游必须在 SDK 初始化、接口选择和探活之前分流。
- 后续迁移为 remote、真实 A2A interface，群内 composition 可整体省略；迁移约束见 spec 附录 A。
- v1 只导出 authoring YAML 明文；不实现内部 normalized/blob 的无损转译。
- local 执行不承诺复制 BCN Session、工具上下文、事件、观察者和鉴权语义。
- 现有 public 标记是否包括对 context/YAML 的外发授权，需要部署 owner 确认；本文不推定历史用户已经作出这种授权。

## 3. 协议版本与外部资料

设计固定 A2A 1.0，而不是跟随变化的 latest。参考资料：

```text
https://raw.githubusercontent.com/a2aproject/A2A/v1.0.0/specification/a2a.proto
https://raw.githubusercontent.com/a2aproject/A2A/v1.0.0/docs/specification.md
https://a2a-protocol.org/latest/specification/
https://a2a-protocol.org/latest/topics/extensions/
https://www.rfc-editor.org/rfc/rfc8785
```

2026-11-03 修订时已读取官方 specification 网页（当时显示发布版本 1.0.0），并与本地 Card contract fixtures/注册实现交叉检查字段；从官方仓库直接获取 proto 的请求及重试超时，未取得固定版本 schema，**未运行 SDK validator**。网页阅读与静态示例检查不能代替正式实施时的固定版本 conformance。LOCAL 单独按本文的临时约定验证，不声称符合标准绑定或通用 SDK 可直接消费。

本轮同时核对上游搜索 TR 接口文档的 items、query/limit/filters、评分与错误 envelope；这里只同步必要的 Card 消费契约，不引入上游部署实现或公司内部服务信息。goal/task 的必填性由 Facade 契约负责，不由本文重定义。

## 4. 本次验证

- JSON：解析 4 个示例文件，检查必要字段、items/resourceType 包装、扩展类型、LOCAL 三标记及 remote interface 分类。
- 引用闭包：每个群独立以 composition.agents 解析全部 binding，map key 与成员身份一致且 key 集合精确匹配引用并集、revision 相同；两种群的单卡示例与完整响应一致。顶层只有三个推荐项，analyst 独立推荐与状态机依赖的副本同时保留。
- 静态反例：缺成员、额外成员、map key/身份不一致、引用 revision 不匹配、未知 mode、错误/混合 interface、嵌套群和重复 JSON key 均被检查脚本拒绝。这只是本次示例检查，不是已实现服务或上游执行器测试。
- 校验和：YAML 原始 UTF-8 字节与 Card.content 完全相等，sha256 相同；6 张唯一 Card 的 revision 重算一致；map 键顺序不影响 hash，成员内容或内嵌 revision 变化会改变群 hash。
- YAML：使用仓库已有 Node `yaml` 包解析原始文件与 Markdown YAML 示例，检查 participant/assignee/edge/final-output 对应关系。没有安装新依赖。
- 示例哈希只包含 ASCII 对象键、整数和字符串，使用与 JCS 相同的该子集编码；**未验证通用 JCS 实现的浮点数、UTF-16 键排序等全量兼容性**。
- 文档：相对链接存在性、Markdown fenced JSON/YAML 语法、文件行数及 whitespace diff 检查。

未执行：真实 MySQL/OB SQL、事务隔离、EXPLAIN/压测、A2A SDK/schema conformance、Python/Java 完整服务测试、manager 控制协议联调、BCN Group-as-a-Bot。原因：本次只有设计/样例，无服务实现和测试数据库；官方 proto 获取亦未成功。
