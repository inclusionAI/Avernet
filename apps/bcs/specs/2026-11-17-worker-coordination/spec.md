---
status: proposed
date: 2026-11-17
owner: BCS distributed Worker
---

# BCN Worker 协调与接管 SPEC v1

## 1. 状态、目标与边界

本稿供设计评审，定义 Worker 注册与心跳、Shard owner 认领、宕机检测、主动交接、Session 封印和异常恢复。此次变更只提交 SPEC 和导航，不修改接口声明、运行时、配置或插件实现。合入文档不代表功能已经实现、验证或部署。

基线为 Avernet `f1a31052b42d7305ca848a0cb8179b36cc94f23b`。本稿提出下列既有契约的后续变更，不通过文档覆盖当前代码的行为：

- [分布式接口设计](../2026-11-15-distributed-contracts/spec.md)及[开发任务](../2026-11-15-distributed-contracts/tasks.md)。
- [成员与分片处理权](../../crates/service-api/bcs-service-api/src/port/repo/distributed/ownership.rs)、[会话存储](../../crates/service-api/bcs-service-api/src/port/repo/distributed/session.rs)、[输入与调度](../../crates/service-api/bcs-service-api/src/port/repo/distributed/inbox.rs)。
- [Worker runtime 入口](../../crates/service-api/bcs-service-api/src/application/dist_runtime.rs)与[runtime 控制端口](../../crates/service-api/bcs-service-api/src/port/dist_runtime.rs)。
- [存储能力](../../crates/plugin-api/bcs-store-api/src/common.rs)与[JSON 条件更新契约](../../crates/plugin-api/bcs-store-api/src/json.rs)。
- [架构约束](../../../../docs/arch/arch.rules.md)和[BCS ADR 导航](../../docs/adr/README.md)。本稿不修改已有 ADR。

Runner、输入去重、checkpoint 和 outbox 的业务流程沿用既有设计，只补充协调所需的约束。Gateway 与 Connector 的受理、执行和恢复协议不在本次展开。

### 1.1 成功条件

- Shard owner 决定谁运行分片调度；`ckpt.owner` 决定谁可以提交该会话。转交 shard 不会同时撤销所有旧会话的写权限。
- 正常交接允许旧 Worker 完成当前轮次，不等待整个 Task、远程调用或工作流结束。
- 故障接管通过逐会话封印保证安全。不承诺没有计算重叠，但封印之后旧 Worker 的 checkpoint 提交必须失败。
- 通知用于加速，持久状态用于恢复。丢失通知、重启或中途退出都不能使已登记的待处理工作永久失去调度。
- 存储和队列可以替换。公共协调逻辑依赖业务端口；具体命令、连接、内部 SDK 和私有验证材料留在相应 provider 的宿主仓库。

## 2. 对象、接口与配置

### 2.1 身份和持久状态

| 对象 | 定义与作用 |
| --- | --- |
| `Instance` | 节点名 `node` 和注册实例 `incarnation`。新注册生命周期生成新 UUID，正常续租不改变 UUID。 |
| `ShardOwner` | `Instance + epoch`。epoch 在同一个 shard 内单调递增。 |
| `ShardClaim` | `shard + ShardOwner`。继续作为认领身份，不携带公开存储版本或交接状态。 |
| `ShardRecord`（拟新增） | 当前 `ShardClaim` 与可选的当前 `handoff`，作为完整协调状态的读取视图。 |
| `handoff`（拟新增） | `request_id、from、deadline、source_done`。接收方就是当前 owner，不重复保存 target；存在该记录表示当前有正常交接上下文。 |
| 成员记录（拟扩展） | 实例、角色、运行/排空/退休状态、内部 HTTP 控制地址和租约信息。 |
| `CheckpointEnvelope` | 复用 owner、业务 checkpoint 和恢复信息。封印保留最新业务内容。 |

三个版本概念必须分开：

- `epoch` 是分片处理权代次。
- `commit_seq` 是会话业务提交序号；封印和 handoff 清理不制造新的业务提交。
- 存储 CAS 版本由适配器管理。同一文档的封印、业务提交和恢复信息更新共用同一个版本。

不把公开 `revision` 字段加回 `ShardOwner` 或 `Checkpoint`。存储版本不要求按 epoch 和 commit_seq 编码，也不能被解释成真实时间。

Owner 记录永久保留，不删除或重置 epoch。第一版不对 checkpoint 控制记录自动过期、删除重建；后续 GC 必须独立证明旧版本不会因重建再次有效。首次创建使用原子 create-if-absent，不能普通覆盖已有记录。

### 2.2 拟议接口变更

以下是后续实现需要的契约变化，本次不修改 Rust 定义。

| 边界 | 变化与结果约定 |
| --- | --- |
| `MembershipPort` | 注册时登记控制地址；`members` 返回可寻址、活跃且未 draining 的候选；增加精确实例的幂等退休操作。保留注册、续租、draining 和三态存活查询。 |
| `ShardLeasePort` | `current` 返回 `ShardRecord`；增加转交 owner、记录来源收尾完成、同 epoch 清理当前 handoff 的条件操作。故障 `claim` 在同一次 CAS 中推进 owner 并移除旧 handoff，见第 3.2 节；不新增独立的强制清理操作。所有操作核对精确 claim 和请求身份。 |
| `SessionStorePort` | `AcquireOutcome` 增加 `Deferred`，附下一次检查时间；增加来源调用的 `handoff_session`，按第 2.2.1 节条件转移处理权并保留最新 envelope。 |
| `DistWorkerRuntimeService` / `DistRuntimeControlPort` | 保留 `snapshot/drain(grace)`，补接收交接请求和唤醒提示的入口。HTTP adapter 调 application service，由其使用控制端口推进；不新增高层协调服务或 Actor。 |
| 存储插件边界 | 提供符合实际声明的持久单文档条件更新和严格租约能力。业务层不调用厂商命令。 |

交接请求至少绑定固定请求 ID、精确来源 claim、目标实例和交接预算。接收结果区分已接受、明确拒绝、失权和结果未知。通知接收成功不代表 session 已激活。相同 ID 携带不同内容必须拒绝。

成员注册的控制地址在同一注册生命周期内固定。出站请求从成员记录解析地址；入站请求验证目标 incarnation，避免落到同名重启实例。控制接口复用服务间认证并将调用身份绑定到来源实例，不能只检查“具有 Worker 角色”；部署缺少这种身份绑定时不得启用入口。

现有 claim 契约承诺同请求返回原结果，需与测试一起改成第 4.5 节的有界重试约定。该兼容性变化必须随实际接口改动交付，不能认为新增本稿已经改变旧契约。

#### 2.2.1 `handoff_session` 的契约

由来源 Worker 在该会话当前轮次及相关在途操作收尾后调用，入参为来源 `SessionOwnership`（含 sid、shard 和精确 owner）、目标 `ShardClaim` 和当前 handoff 的 `request_id`。这组身份固定同一交接操作，不另建逐会话交接历史。调用方不传待覆盖的 checkpoint；适配器必须读取并保留最新 envelope。

新转移必须满足以下条件：

1. sid 属于目标 shard；来源和目标 shard 相同，目标 epoch 严格大于来源 epoch。
2. 当前 shard claim 精确等于目标 claim；当前 handoff 的 ID、from 与请求一致，且保护期尚未结束。目标实例租约有效。资格未知时不发起新写入；本次交接已被取代或结束时转入失权处理。
3. checkpoint 的精确 owner 仍为来源 owner。以刚读取的整文档版本执行 CAS，只改为目标 owner，完整保留业务 checkpoint 和恢复信息；推进私有存储版本，保持 `commit_seq` 不变。
4. 写后再次核对当前目标 claim 和会话 owner，结果未知按第 4.4 节确认。来源一旦发出转移就不能恢复该会话的业务写入；成功也不替接收方激活会话，接收方仍须核对自身资格、安装 scheduling fence 并恢复 outbox。

结果语义如下；本稿不新增 Rust 返回类型：

| 结果 | 含义 |
| --- | --- |
| 已转移 | 条件写与写后核对已确认，会话当前属于目标 owner。 |
| 已在目标 | 重试读取到目标仍为当前 shard owner，且会话已属于该精确 owner；不再写入或推进版本。不承诺证明原请求曾成功，handoff 已清理也可确认这一当前状态。 |
| `Fenced` | 当前 claim 已被取代，或会话已属于其他 owner；若仍为来源但 handoff 已结束/不匹配，也不再转移，由当前 owner 接管。不得把较新 owner 改回目标。 |
| `StoreError` | 无效入参用 `Invalid`；可证明未写入的存储失败用 `NotApplied`；写入或确认超时、无法判定结果时用 `OutcomeUnknown`。未知不等于未转移。 |

CAS 版本冲突在适配器内重读并重新核对上述条件，不暴露公开 revision；仅条件仍成立时才可用最新 envelope 重试，且受预算约束。已超时的同一次存储尝试重发时保留原条件，不能把迟到请求换成新版本强写。目标已取得会话后的业务提交必须保留，不能用来源旧快照覆盖。

`acquire` 由接收方用于发现、封印和加载会话，正常保护期内可返回 `Deferred`；`handoff_session` 由来源主动释放自己已收尾的会话，不等待保护期自然结束。两者共用文档版本和禁止 owner 回退的规则。shard 记录与 checkpoint 不要求跨 key 事务：资格采用写前/写后核对。若来源 A 把会话交给 B 的迟到写先成功，后继 C 重读并保留最新内容再封印；若 C 已封印，迟到的 A→B 写入必须被文档版本和 owner 条件拒绝。

### 2.3 配置与默认值

HRW 指 Highest Random Weight，也称 rendezvous hashing：对每个 shard 给候选分别算分，分数最高者优先认领。v1 固定使用 SHA-256，规则如下：

- 输入依次为固定 ASCII 前缀 `bcn-worker-hrw-v1`、shard 的 u32 大端字节、一个模式字节（`node` 为 0，`node_incarnation` 为 1）、node；模式 1 再追加 incarnation。固定前缀直接写入；node 和 incarnation 采用成员记录的原始 UTF-8 字节，各自前置 u32 大端字节长度，不使用分隔符拼接或进程随机种子。
- 将 32 字节摘要按大端无符号数降序排列。分数相同时按 `(node, incarnation)` 的原始 UTF-8 字节元组升序打破平局；同一实例去重。
- 仅对同一成员域内符合资格的候选排序，排名从 0 开始，退避为 `排名 × 退避步长`。候选视图不同可能造成并发尝试，最终仍由 owner CAS 决定唯一赢家。
- 默认只把 node 放入得分输入，节点重启不会改变它的得分；`node_incarnation` 才将注册实例加入得分输入。算法、编码和模式在同一集群内必须一致，变更需协调升级。

| 配置 | 默认值或规则 |
| --- | --- |
| 心跳间隔 | 1 秒，可配置 |
| 租约 TTL | 3 秒，可配置；初始参数，不代表已验证的生产容错预算 |
| 成员、owner 检查周期 | 500ms，可配置 |
| 认领候选退避 | HRW 第一候选立即尝试，后续候选按排名每级增加 500ms |
| 协调请求超时 | 500ms，可配置，必须小于租约 TTL |
| 注册稳定观察期 | 一个租约 TTL |
| HRW 身份 | 默认 `node`，可选 `node_incarnation`；同一集群使用一致配置 |
| 待处理会话枚举 | 每页 200 个，最多同时接管 32 个；均可配置 |
| 主动交接预算 | shutdown hook 传入的 `drain(grace)`；退出余量默认 1 秒，可配置 |

Shard 数量、pool 和 session 路由沿用部署配置。新节点加入、实例 UUID 变化只影响需要认领的 shard 的候选排序，不主动搬走健康 owner 的 shard。

成员检查按实例合并，同一轮不因它持有多个 shard 重复查询。扫描、接管和重试都受分页、并发和连接池预算约束；持续失败使用有界退避。预算不足退出余量时不发起新的正常交接。

## 3. 正常运行与故障接管

### 3.1 注册、续租和死亡判断

1. Worker 生成新的 incarnation，注册角色和控制地址。
2. 持续续租，稳定满一个 TTL 后，才参与故障认领和接收主动交接。Worker 的 `ready` 必须包含这一条件：本注册生命周期内租约连续保持有效满一个 TTL，所需依赖就绪且未 draining；新 incarnation 重新计时。自身资格 Unknown 时即使缓存 ready=true 也不得接单。
3. draining 时继续续租，但关闭新增认领、接管和入站交接入口。
4. 租约确认失效后，旧 incarnation 永不复活。正常运行的进程可以用新 UUID 重新注册，无须重启进程。
5. 已进入 shutdown 的进程保持 draining，不通过重新注册恢复接单。

`Alive` 表示确认有效，`Dead` 表示该注册生命周期已终止且不可复活，`Unknown` 表示超时、读取失败或结果不确定。draining 的有效租约仍是 Alive，但不属于新增交接候选。

Unknown 不允许故障认领。自身租约不确定时暂停新增工作；超过保守的已确认有效期限后，不能继续按自己仍有效处理。注册请求超时不能通过反复无条件创建同一 UUID 来确认结果。

### 3.2 认领 shard

仅允许认领从未持有的 shard、当前 owner 已确认死亡/退休的 shard，或当前 owner 明确发起的主动交接。

故障认领依次执行：

1. 读取当前 owner 和存储版本，取得旧实例已经终止的可靠证据。
2. 核对 `expected_epoch`。从未持有时从 epoch=1 开始，否则加一；溢出失败，不能回绕。
3. 以精确版本条件在同一个 owner 记录内写入新 owner 并设置 `handoff = None`；两者一次原子提交，竞争只允许一个请求成功。旧实例不可复活，因此它的终止证据不会在后续 CAS 前变回有效。
4. 再次核对自身成员资格及当前 owner，确认后启动分片处理。写入成功但本实例已失效时不能激活；记录留给后继者正常接管，不能回滚覆盖。

故障认领推进 epoch 时，上一轮 graceful handoff 随同一次 CAS 失效；不是新 owner 认领成功后再调用清理接口。第 4.5 节的 source_done、来源 Dead 或到期条件只约束不推进 epoch 的普通清理。此处认领前必须确认死亡/退休的是当前 shard owner，不能把其状态为 Unknown 当作故障证据。

例如 B/14 → C/15 尚未交完，B 仍 Alive、截止时间未到，但 C 已确认死亡：D 可条件认领为 D/16 并同时移除 B→C 的 handoff，无须再等 B 的旧保护期。D 仍须逐会话封印，才能挡住旧提交；推进 shard epoch 本身不撤销 session 写权限。接管者恢复所有更旧的 session owner，不仅是直接上一任。

### 3.3 Session 接管和提交

取得 shard 后，启动独立的恢复扫描，枚举全部待处理会话，包括未来才到期的会话。session 激活依次执行：

1. 确认当前 shard claim 和自身成员资格。
2. 读取 checkpoint，按第 4.2 节判断当前 handoff 是否要求等待。
3. 使用读取到的文档版本条件更新 owner，保留最新 `commit_seq、consumed、runner_state、outbox` 和恢复信息。不存在时用 create-if-absent 初始化。
4. 确认封印及当前 claim、成员资格后，加载最新 checkpoint。校验失败或未知不激活，也不回滚已经发生的封印。
5. 通过 `InboxSchedulingPort::observe` 安装并确认本代会话的 scheduling fence，再检查或结算调度安排；按既有恢复协议幂等补做 outbox，再处理下一批输入。

封印、业务提交、恢复控制更新必须推进同一个文档版本。一个示意顺序是：旧 Worker 读取 V1，新 Worker 封印产生 V2，旧 Worker 携带 V1 提交整份 checkpoint 时整笔失败。

若旧提交先成功，新封印的旧版本条件失败；接管者重读后保留这次提交再封印。不能把缓存的旧 checkpoint 用更高所有权代次直接覆盖回去。

正常提交校验精确 `ckpt.owner` 和 `expected_commit_seq`。它不要求提交者仍是当前 shard owner，否则旧 Worker 无法完成正常交接中的当前轮次。CAS 冲突后重新判断所有权和业务序号，不能只刷新版本就重试旧内容。

现有原子提交约束继续有效：状态、consumed 和 outbox 一次提交；结果未知时先确认；上批 outbox 未完成前不覆盖；输入确认只依据已确认提交覆盖的位置。控制更新不算业务进展，正常交接不累加异常恢复失败次数。

### 3.4 待处理索引与通知

- 待处理索引属于 shard，不随 Worker 搬迁，也不是处理权凭据。
- 全量恢复遍历独立于 handoff 存在；任务丢失或重启后重新启动，清掉 handoff 不能停止它。
- 通知或已有会话读循环加快发现，持久 pending 枚举补齐未监听会话。
- 遍历必须满足输入仓储的公平、有界和并发登记不丢失约定，不能把一次可变列表的偏移扫描当作完整快照证明。
- 来源进入交接后停止删除 pending 项或推迟安排，并将已发出的调度操作纳入收尾确认。
- 修改 shard owner 或 ckpt.owner 不会自动安装 scheduling fence。接收方通过 `observe` 安装新 fence 后，旧 owner 的 `settle` 才必须被拒绝；此之前仍按原 owner 与调度 token 校验，不能声称所有旧在途操作已经失效。

## 4. 主动交接、超时与清理

### 4.1 正常交接：A → B

**A 先关闭新增入口。** shutdown hook 到达后，立即关闭本地新增认领、接管、处理轮次和入站交接入口，再发布 draining；不能等待成员状态传播之后才停止接单。

**A 确认在途接管结果。** 交出 shard 前，已经发出的 session acquire 必须有明确结果。成功的纳入 A 收尾范围，明确未成功的留给接收方，结果未知的继续确认。这里等待的是获取处理权的操作，不是所有业务轮次。

**B 接收并写入 owner。** A 从候选中选择 B，发送固定请求 ID 的交接请求。B 在本地检查租约、ready（包含第 3.1 节稳定满一个 TTL）、容量和 draining，登记在途请求后释放生命周期锁，再执行网络操作。

最终条件写由 B 执行：一次把 owner 从 A/13 改成 B/14，并写入当前 handoff 的来源、请求 ID 和截止时间。持久化确认后才返回接受成功。

如果 B 登记请求后收到自己的 shutdown hook，仍须确认这笔在途请求的结果。成功入站的 shard 纳入自身排空范围，不能因为刚进入 draining 就把已发生的转交当作拒绝。相同请求的结果查询和确认不能被“拒绝新请求”的入口一起挡住。

**A 逐个交出 session。** 空闲会话在相关在途操作确认后交出；忙碌会话完成当前轮次、确认 checkpoint、必要的 outbox 和已发出的调度操作后，调用第 2.2.1 节的 `handoff_session` 条件修改 owner 为 B。未确认的 settle 不能被忽略后报告会话已释放或 source_done。转移只改变处理权，保留最新 envelope。B 取得会话处理权并安装 scheduling fence 后，才能继续调度、恢复和处理。

只依赖各节点的 shutdown hook，不假设存在发布批次协调器。因此不能保证 A 永远不会选中随后也开始 shutdown 的 B；靠 B 的本地入口和已接受请求收尾保证安全。

### 4.2 当前 handoff 只保护精确来源

以 B/14 → C/15 为例：

| C 读到的 session owner | 行为 |
| --- | --- |
| B/14，当前保护期有效、未 source_done、未确认 B 死亡 | Deferred，等待 B 交出 |
| 更早遗留的 A/13 | 直接条件封印成 C/15 |
| 已是 C/15 | 加载后恢复或继续处理 |
| 比 C/15 更新，或不属于目标 shard | 停止并重新核对处理权 |

比较包括节点、incarnation 和 epoch。读取 B 的存活状态为 Unknown 时，不能据此跳过仍有效的保护期。

同一 shard 开始下一次正常交接前，上一次保护期必须已合法结束；不要求所有历史 checkpoint 都已改写。这是每个 shard 的限制，不能成为“整台机器必须等入站全部完成才能释放出站”的屏障，否则同时发布可能相互等待。

正常交接在发布 shard 转移前已排空 acquire。异常情况下如果 C 读到 A/13 后，旧 B 的在途封印先改成 B/14，C 的旧版本写入应失败；重读后重新判断保护条件，不能沿用过时结论。

### 4.3 时间、超时与故障收尾

本机 shutdown 预算使用单调时钟。持久化 handoff 截止时间由协调存储的权威时间生成和判定，不能直接拿来源机器的绝对时间让其他机器比较。

来源只传剩余退出预算；接收方为同一请求固定一次截止时间，重试不延长保护期。预算扣除退出余量，来源不会因为对端超时而延长自己的退出期限。后端无法可靠判定持久期限时，不以到期为由强制接管；该时间能力属于启用前置条件。

以下任一条件允许当前 owner 结束等待：来源明确 source_done、来源租约已确认终止、交接期限已可靠到达。随后封印剩余会话并按故障恢复推进。

封印之前已成功的旧提交保留；之后的旧提交失败。在途外部动作仍依靠既有 effect ID、内容校验及幂等写入收敛，不能把 handoff 清理当作所有外部动作已经停止。

如果本机退出预算耗尽，仍有 shard、session 或相关在途操作未完成收尾，停止业务并返回现有 `DrainOutcome::DeadlineReached { remaining: u64 }`。保持返回类型不变，未确认项的诊断详情通过第 5.2 节的结构化日志输出，数量通过指标观测，不放进返回值。后继者必须等待合法故障接管条件，不能仅凭来源 hook 超时抢占 owner。无接收者时允许退回故障恢复，不承诺发布期间零延迟。

### 4.4 结果未知与迟到请求

“没有收到回复”指没有收到存储写入或交接 RPC 的明确结果，不代表写入没发生。

| 读回结果 | 判断 |
| --- | --- |
| 精确的预期 session owner | 可确认当前封印状态；不证明全部历史请求回执 |
| 当前 handoff 的 ID、来源、目标及内容匹配 | 返回已经持久化的交接结果 |
| 仍为旧 owner，且无法排除在途请求 | 仍是未知，不能宣称失败或取消 |
| 已是更高 epoch 或请求已被后继状态取代 | 停止旧请求，返回失权；不能据此断言它历史上未成功 |
| 读取失败 | 仍是未知 |

同一次重试保留原 ID、内容和写入条件，不能悄悄更换目标。版本冲突不证明前一次未成功；重读后先核对业务身份，再决定后续行为，不能只刷新版本强写旧 owner。

如果 B 的迟到封印先成功，C 重读并保留最新内容，再封印为 C。若 C 先成功，B 携带旧版本的封印和提交均失败。即使业务 commit_seq 没有变化，封印也必须推进文档版本。

### 4.5 完成、清理与有界重试

A 只有在新增入口关闭、已有轮次与相关在途操作收尾、所持会话转交结果明确后，才能报告 source_done。结果未知不能报告完成。接收方将该声明条件记录在当前 handoff 中；丢失通知可重试，来源宕机则走租约失效/期限结束路径。

同 epoch 的普通 handoff 清理仅由当前 owner 执行，必须匹配当前 claim 和 handoff ID，且满足 source_done、来源 Dead 或合法到期之一。清理不增加 epoch，不删除 owner。旧请求不能清掉下一轮 handoff。第 3.2 节的故障 claim 是另一种状态转换：确认当前 owner 终止后，推进 epoch 并原子移除旧 handoff，不受旧来源保护期的这三个清理条件限制，也不提供绕过认领条件的强制清理入口。

清理表示等待保护结束、后续恢复责任归当前 owner，不表示全部历史 session 已迁移。pending 扫描、会话封印、outbox 恢复继续运行；不能删除它们的恢复或去重依据。

只保留当前 handoff 和至多最近一次必要的请求结果，不建立无限增长的历史。记录存在时同 ID、同内容返回已知结果；清理或覆盖后，旧 expected_epoch 返回失权，不再承诺永久恢复原回执。结果未知但 epoch 尚未变化的在途请求，不能简单清掉并宣称已经取消。

`Drained` 表示本节点已完成收尾和释放，不表示接收方已建立所有订阅。收尾未完成、所有权结果未知或退出期限到达时不能报告 Drained。接收方通过持久恢复保证剩余推进。

## 5. 存储前置条件、验收与交付

### 5.1 Provider 要求

协调记录要求一致读取，以及精确旧版本比较、内容更新和版本推进的一次原子提交。路径更新和整份更新必须共享文档版本，失败不能部分修改数据、版本或 TTL。不能混入绕过版本检查的普通覆盖，也不能用允许同版本多次更新的条件代替互斥。

JsonStorePlugin 已定义整文档版本条件语义，但具体 provider 仍需按声明范围验收。支持一项 CAS 操作不代表整个 AtomicJson 能力已经满足：还需区分精确 Replace、顺序路径编辑、null/删除、TTL 更新和删除重建后的版本历史。递归合并不能直接冒充精确整体替换。

严格租约要求注册生命周期不可复活、有效性检查与续期原子完成、退休不可撤销，以及可靠时间判定。普通 TTL 写入，或者 CAS 后另行无条件续期，均不能替代这些保证。后端缺少所需能力时不得启用本协调模式。

本协议不假定跨 key 或存储与队列之间存在事务。旧实例已终止的事实必须稳定；当前 claim、成员资格与 session 封印仍按第 3 节核对。存储成功回包的持久性和故障切换行为也需要 provider 验收，不能由小规模 CAS 并发测试推断。

### 5.2 必须通过的场景

下表是未来实现的验收要求，不是本次文档提交已经运行的测试。

| 场景 | 预期结果 |
| --- | --- |
| 多个 Worker 同时认领 | 一方成功，epoch 只增加一次 |
| 注册/续租回包丢失、旧 UUID 延迟重试 | Unknown 不当 Dead，失效实例不复活，不误操作新实例 |
| 节点重启或短暂网络故障 | 默认 node 排名稳定，不搬走健康 shard |
| 同一候选集在不同进程计算 HRW | 两种身份模式、字符串边界和同分决胜产生一致排名；成员视图不同仍由 CAS 决定赢家 |
| 新注册实例尚未稳定满 TTL | ready 不成立，拒绝新交接；新 incarnation 重新计时 |
| 旧提交与新封印并发 | 旧提交先成功则保留；新封印先成功则旧提交整笔失败 |
| 封印与恢复元数据并发 | 不丢业务状态、consumed、outbox 或控制信息 |
| shutdown 时 acquire 仍在途 | 未确认前不宣布正常交接完成 |
| A、B 同时收到 hook | 新请求被拒；已接受请求继续收敛，无节点间等待死锁 |
| RPC 超时、重复 ID、同 ID 不同内容 | 不重复推进 epoch；内容冲突拒绝；未知不冒充失败 |
| 来源交一半宕机、outbox 执行一半 | 接收者封印后恢复，并幂等补做 |
| 上一轮 A→B 的 handoff 清理后，B→C 开始，仍有 A 的遗留 session | C 等待受当前保护期保护的 B，直接接管 A 的遗留会话 |
| B→C 保护期尚未结束，C 宕机，D 故障认领 | 确认 C Dead 后，一次 CAS 写 D/新 epoch 并移除旧 handoff；D 不等待 B 的旧保护期，逐会话封印后恢复 |
| 来源转移会话时 CAS 冲突、回包丢失或目标已更新 checkpoint | 只在精确交接条件仍成立时重试；已在目标则不再改写，保留目标新提交；更高 owner 返回 Fenced，未知不报完成 |
| 通知丢失、未来 deadline、扫描重启 | 持久 pending 恢复仍然发现并推进工作 |
| C 封印后 B 的请求迟到 | B 无法改回 owner 或覆盖业务状态 |
| 旧 settle 与接管并发 | 来源确认在途操作；接收方 observe 安装新 fence 后拒绝旧 settle，不把 owner 修改误当成调度 fence 已生效 |
| 截止时间读取失败或机器时钟偏差 | 不用不可靠时间提前强抢；按合法的终止证据恢复 |
| 退出预算耗尽仍有未确认操作 | 保持 DeadlineReached { remaining } 返回形态，诊断详情写结构化日志，不返回 Drained |
| 版本上限、删除重建、普通写绕过 | 上限失败；拒绝不满足历史/写入纪律的启用方式 |

验证分为可控时钟和故障注入的契约测试、多 Worker 集成测试、每种 provider/transport 的开发环境测试。跨 transport 写入应使用同一版本协议。生产持久性和故障切换另行验收。

观测至少包括心跳延迟/Unknown、认领冲突、handoff 剩余预算、未确认操作数、Deferred 时长、恢复扫描滞后及旧提交被拒次数。诊断携带 shard、实例、epoch、handoff ID 和 sid，不记录凭据或消息正文。

### 5.3 访问成本与实施顺序

成员检查按每轮不同实例数计费，不按 shard 重复读取。分片检查分页且有界；一次 session 接管包含协调资格读取、checkpoint 读取、条件写及封印后的确认。提交不会逐次额外查询 shard owner；冲突重读和恢复 replay 的成本必须计入失败路径预算。pipeline 可以减少往返，不能扩大原子范围。

实施时应先更新契约和故障用例，再完成所需存储能力，随后实现协调运行时和 HTTP 入口，最后进行多进程与 provider 验证。不得用本稿的测试矩阵替代实际执行证据。

本次交付只新增设计文档与索引。文档检查包括相对链接、现有接口引用、提案/现状区分和公开内容边界；不因文档提交运行 Cargo 构建、业务测试或访问真实存储。代码实现、运行验证和部署均待后续授权。
