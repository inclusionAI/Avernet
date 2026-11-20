# PR #4060：2026-11-17 审阅修订记录

状态：文档修订，未实施。输入为用户提供的 `2026-11-17-pr4060-group-as-agent-review.md`；审阅基线及本轮开始时HEAD均为 `136fd9aaa6041b088404a0925324ea5d78ab4fe3`。不将本地审阅文件绝对路径、私有运行记录或凭据提交到公共仓库。

阅读入口：[spec](spec.md)、[plan](plan.md)、[接口](plan/interfaces.md)、[schema](plan/schema.md)、[conformance](plan/conformance.md)。本记录区分“已改文档”和“实现已验证”，不把审阅问题的消除等同于功能通过测试。

## 1. 逐项回应

| 审阅项 | 核实与修订 | 实施任务／未来验证 |
| --- | --- | --- |
| P0-1 表名与迁移 | migration039的Bot出站表保持不动；Group生命周期改为bcs_group_a2a_*，Session改为bcs_group_sessions；明确SQLite/MySQL实际SQL及include_str，补版本断言文件和全量迁移测试 | T02；源版本未变时MySQL048、SQLite046，执行时重新分配；新装/旧库升级+出站schema/数据不变 |
| P1-1 CardFetcher | 最小Card公开读application/json，调用六操作仍Bearer；不是无鉴权执行，也不新增extended Card | T16 projection+真实Fetcher验证，T17真实Group router/HttpA2aCardFetcher集成 |
| P1-2 LOCAL discovery | 明确选择替换Fuse Group Card：旧LOCAL模板保留本地用途，Fuse/HTTP输出独立HTTP+JSON投影；不混合LOCAL或required discovery扩展；未开启群撤下为有意兼容变化 | spec §3.5、T16；原生群列表保留，LOCAL消费者迁移为上线门槛 |
| P1-3 两个writer | 旧Group直接PUT/DELETE分支退出，内存Group通知仅唤醒durable worker；Bot分支保留；切换前停旧领取并drain在途 | spec §3.6、T16/T19；无远端revision fence不保证任意旧请求即时失效，uncertain期间不标撤销完成，结算后最终DELETE核对 |
| P1-4 middleware | v1/mod.rs平级merge独立Group router，公开Card与认证数据面子路由分开，不经过management transport::boundary | T17精确A2A/SSE Accept与裸400/401/404/406/413/415测试；原Agent管理envelope回归 |
| P1-5 a2aOutput | 权威配置是GroupRuntimeBinding.a2a_output；新增binding存储列/revision，create/detail/PATCH/configure与OpenAPI在T06同步，校验node_id/format/成功分支覆盖并与资格组合提交 | T06真实管理写→读回→开启→snapshot.outputs；禁止fixture成为唯一输入 |
| P1-6 执行快照 | 加入Group.context、opening_message、routing_policy规范化值；context是执行目标，不把其Card描述用途误当纯展示 | T06/T09/T10；上述变更旧context拒绝，label-only允许 |
| P1-7 GroupStatus | 仅Active/Completed开放；Closed/Error/Inactive拒绝新调用且撤销Card，status进入snapshot；关闭/错误状态变更不可被开启不变量阻止 | T06/T16；生命周期变更增加revision+publication意图 |
| P1-8 输出模式 | 去重未命中后在受理事务按快照求交集；ContentTypeNotSupported独立错误、保存selected_output_modes；终态逐Part验证不匹配OutputInvalid | T07/T08/T11/T17；无交集不占ID，重试不按新Card重算 |
| P1-9 依赖与R25 | Submission/认证/查询/执行DTO下沉domain；application/core只声明trait；新增17份合同的集中harness/真实runner矩阵，按方法增量交付不留默认Unsupported | T01起、T20门禁；port purity/R25必须通过，不能借旧红灯或新waiver豁免 |
| P1-10 deployment | a2a.deployment显式可信配置、env复用原解析值；首期单DbPlugin数据源只绑定一个(deployment,env)，singleton启动核验，Store仅db+StoreScope，无logical_db | T02/T03/T07/T19；同映射多实例成功，另一映射失败，后台查询强制固定deployment |
| P1-11 dispatch barrier | claim≠许可；begin_dispatch CAS在start之前保存owner/token/attempted_at；consume permit和根work入队原子，queue send-start与取消竞争，观察/记录/续约全fence | T08/T09/T10/T12/T13；30s lease/10s续，旧owner/claim后取消/barrier后崩溃/ack未知测试；远端副作用仍不承诺exactly-once |
| P2-1 DAG | 总图与任务依赖补T13→T15和T08→T16，T16真实router测试归T17避免反向循环 | plan §2、T15/T16/T17 |
| P2-2 跨实例wait | TaskWaitService独立1秒durable poll，按Task聚合、100key/批、2批并发，Notifier只是加速 | T14 poll_heads、T18双实例独立notifier共享DB测试；等待者看到另一实例终态 |
| P2-3 查询索引 | SQL同时scope_hash和完整scope；索引含deployment/scope_hash/result_deleted_at_ms前缀 | T14/T20，至少100万旧墓碑的真实MySQL EXPLAIN和rows examined，未运行前不宣称性能通过 |

## 2. 产品／安全决议：仍需书面确认

新增spec §3.7及T20 Gate E：supportA2A开放会间接暴露群内Bot已有工具／写操作能力，Caller只用于资源归属与审计，不传播为工具授权。明确需要管理员委托责任、高风险写策略、审计关联和管理/Card文案的书面决议。

本轮**没有**替产品／安全批准这项委托，也没有擅自引入Group白名单或高风险工具新开关。此项为上线启用阻断，技术实现可按已明确合同推进，但不能仅凭测试全绿开放群。

## 3. 核对的当前源文件

- [MySQL出站A2A迁移039](../../migrations/mysql/039_bot_a2a.sql)、[SQLite同版本](../../migrations/sqlite/039_bot_a2a.sql)、[Session基线](../../crates/services/bcs-db-migrations/src/migrations/baseline.rs)、[SQLite迁移加载](../../crates/services/bcs-db-migrations/src/lib.rs)。
- [HttpA2aCardFetcher](../../crates/external-clients/bcs-a2a-http/src/card_fetch.rs)、[旧Group模板](../../crates/services/bcs-group-store/src/card_template.rs)、[旧直接publisher](../../crates/services/bcs-fusion/src/core/card_sync.rs)。
- [V1 router](../../crates/adapters/http/bcs-api-http/src/v1/mod.rs)、[管理router](../../crates/adapters/http/bcs-api-http/src/v1/a2a/mod.rs)、[management boundary](../../crates/adapters/http/bcs-api-http/src/v1/a2a/transport.rs)。
- [GroupStatus](../../crates/contracts/bcs-domain/src/group.rs)、[RuntimeBinding](../../crates/contracts/bcs-domain/src/collaboration.rs)、[Binding SQL](../../crates/services/bcs-collaboration-store/src/sql_binding.rs)、[Store数据源约束](../../crates/services/bcs-group-store/src/lib.rs)。
- [port purity gate](../../scripts/ci/check-port-purity.sh)、[R25 gate](../../scripts/ci/check-r25-conformance.sh)；审阅中的简写scripts/ci路径需从apps/bcs运行，不是仓库根同名目录。

## 4. 本轮验证范围

本次实际执行结果：

| 检查 | 结果与限制 |
| --- | --- |
| 全目录链接／结构 | 13份Markdown、101个本地相对链接目标存在，围栏闭合、无行尾空白 |
| 示例解析 | 14个JSON块、4个SSE data帧、1个TOML块通过；其中52个嵌套A2A对象继续通过固定官方proto的轻量字段/required/oneof核对 |
| 计划结构 | 20个任务、136个未勾选实施步骤，34项AC有对应任务；97次Modify路径存在，7次指向前序任务明确创建的文件 |
| 审阅覆盖 | 15项P0/P1/P2均有上表对应修订；工具权限委托另列未决上线门槛 |
| 表名检查 | 15个拟新增表名与当前SQLite/MySQL及Rust基线CREATE TABLE提取结果无重名；保留出站表不变，确认实际Session表名 |
| SQL样例 | schema中的SQLite CREATE TABLE示例在临时内存库可执行；不是完整迁移链测试，更不是MySQL验证 |
| 新合同矩阵 | 17项合同均列声明位置、集中harness精确函数名、真实实现runner与交付任务；不等于未来R25门禁已经通过 |
| Git | `git diff --check`通过；改动仅该spec目录下Markdown |

未执行尚未实现的Rust、完整SQLite/MySQL迁移链、Fetcher真实HTTP互通、跨实例运行、网关或负载测试；没有运行完整port-purity/R25功能门禁来冒充未来合同已验证。临时文档校验工具留在仓库外。功能验收仍由对应任务交付。

本轮不推送、不发布GitHub审阅回复、不改变PR状态；用户后续决定是否推送。原research.md与plan.md的2026-11-16检查保留为历史证据，不用本轮计数覆盖历史事实。
