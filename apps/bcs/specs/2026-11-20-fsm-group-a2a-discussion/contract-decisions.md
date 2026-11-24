# FSM 群 A2A：D0 合同决策（2026-11-24）

状态：**五项设计方向已选定，物理协议待验证，D0 未冻结**。本文细化[讨论稿](spec.md)，验证工作归入[实施计划](plan.md)的 T1～T9。以下记录和字段是设计，不是 `dev` 已交付的 API。[#4245 固定版本的受理说明](https://github.com/inclusionAI/Avernet/blob/c7187edcc99354f017f2e1a9bfcb3d6e88c15b96/apps/bcs/crates/services/bcs-dist-store/src/inbox/README.md)区分 input 文档、schedule、checkpoint 和 Inbox；现行 input 级 CAS 本身不能裁决不同 input 争用同一 Sid。

## 1. 受理：在 `ckpt:<sid>` 上安装一轮，而非另加 Session 锁

严格入口通过 Worker 受理服务写入；Gateway 不写 checkpoint。选用 checkpoint 的精确版本 CAS，校验当前写者的 owner fence、前轮状态和代次。同一次 CAS 安装新 `generation`、`Tid`、原请求引用、`Accepted` 许可和待调度义务。只有这次成功才算受理，也是 Get/List 可见资格的起点。`schedule:<sid>`、Inbox 和索引各自可补写，均不能代替该许可。通用 JSON-RPC 路径不改。

| 同一 Sid 上的持久切面 | 宕机或竞争后的行为 |
| --- | --- |
| 原请求已准备，受理 CAS 尚未确认 | 无许可则不运行、不公开。用原请求身份读回裁决；不能因超时判定未写。准备须绑定预期空闲代次/版本，不是等候下一代的排队票。 |
| A 赢得 CAS，尚未唤醒 Worker | 同请求重试返回原 Tid；不同请求 B 见 A 占用得 BUSY。接任者补发 A 的 Start/唤醒，执行前校验许可、Tid 和代次；物理重复不产生第二轮。 |
| B 的条件写失败 | 读回区分自己的成功、其他请求胜出和单纯的版本冲突。争用输家不获 Task；废弃该次准备，旧准备即使晚于 A 完成也不能被后台升级。调用方可在空闲后用 B 的 messageId 重新提交。 |

**冻结前必证（T1/T2）：** 首次无 checkpoint 的受保护创建、无活跃 owner 时的受理与唤醒、旧 owner 在交接后迟到写。须列出如何发现已提交却从未发出 hint 的 Sid，以及旧请求如何在多轮后按 Tid 找回许可；不假设多键事务。若 #4245 的 owner 路径不能支撑此协议，阻塞 T2/T7 并重评写者，不降级为 Gateway 先查后写。

## 2. 重试：按消息定位，按首次依据判等

先检查当前权限，再按 `(tenant, principal, group, messageId)` 查准备、已受理记录或过期墓碑。服务端分配的 Sid/Tid 不进查找键。首次保留客户端显式字段（特别是 context 有无）、有序 Parts，以及首次解释这些字段的 schema、默认值、有效配置、扩展和语义摘要。命中时用首次依据解释重试；只有确认未命中才验证当前发布能力。并发准备的输家重读赢家，存储故障返回依赖错误。

T1 固定向量：首轮未给 context 且丢回包，部署改默认值后原样重发仍取 A；显式改 context、输出配置或有序 Parts 造成语义改变则冲突；撤权后不可读；结果过期则报过期不重跑；BUSY 不消耗消息键。不得把“准备中尚未裁决”当作成功、过期或新请求。

## 3. 成功：归档先写，`Completed` 一次提交，不等通知

1. Worker 只取 `final_output`，按 Tid 固定完整公开表示、校验预算并幂等归档，读回核对摘要。此时无终态证明则 Get 不得公开成功，B 仍忙。
2. 持有 fence 的 Worker 在 A 的精确代次上一次提交 `Completed`、终态证明（含归档引用与摘要）及首次 Feed 写许可。该状态本身允许 B 立即竞争；不存在另一次解锁。提交回包丢失只读回/补原决定，不重跑 A。
3. B 推进不得覆盖 A 的证明或未兑现的发布许可。证明按 Tid 转存并读回相符后才可压缩；`AppendEvents` 与 `PublishSnapshot` 各有持久许可，分别绑定事件身份/内容/Sid 前驱和快照摘要/source_commit/snapshot_event_id。Publisher 先按前驱 append 再 publish，两步均不阻塞 B 的业务派发；公共 Get 不依赖 feed 快照。T7 故障验收让 A 首次 append 失败、B 已派发后恢复，再在 A append 成功但 publish 前宕机；两步分别读回完成后才清许可和 pin，不靠当前 checkpoint 授权旧快照，不重复、不越序。
4. Dispatch 目录回收同时修改扫描合同：使用稳定单调键、每轮上界和版本化游标，不按会收缩的数组下标续扫；保护当前扫描 work，兼容转换须允许受保护的幂等重扫。测试删除游标之前的已完成项并重启，存活调用仍能发现和接管；不得仅提高容量或全量重扫历史。

## 4. 下游：分开判断可重发、可查询、已停止

首次发送前按节点 attempt 固定操作、目标去重域、稳定请求 ID、有效截止时间、正文及发送配置摘要；首次受理后绑定可核验的远端 Task/context。每次操作从可信 scope、群、轮次、节点、attempt、目标和操作重新检查授权。冻结绑定不等于沿用旧权限。

- 有未送达证明，或仍在原去重域保证窗口内且身份、操作、内容不变，才可在获准后重发。只有查询能力则只查原调用；暂时 NotFound 不证明未送达或停止。
- 只有与原目标、远端引用及 attempt/操作相符的终止事实才证明该 attempt 已停；不能替其他分支作证。首批要求能按原 Task 取回有序完整产物。正常流的 Completed 和断流接管都先核验停止，再用完整 GetTask 校验结果清单/摘要与入站预算、持久固定后首次报告产物 Final；不先固定残缺 Final 再尝试覆盖。不完整或超限的结果走有界失败，已核验的停止事实单独保留。
- T6/T7 采用包含 #4245 配套 Connector/Inbox 的联合版本，以 `ConnectorAdmission` / `admit_connector` 交付稳定终态身份；不得把 dev 通用 admit 直接接到该 Inbox。真实 GetTask 无远端事件 ID 的完成结果必须经过真实 Inbox 到达 Runner；重复、丢报告响应仍只应用一次。
- Worker shard Supervisor 接管已 ACK 后死 holder 的调用，provider 必须支持固定版本的 `RetainedLease`，重启后保留 Terminated 供判定 Dead，不把 Absent 当 Dead。短期查询耗尽时在权威 Dispatch 上以 CAS 登记核实模式、独立 claim epoch 和可修复的调度义务，使普通 claim 失效后才释放槽；原进程存活、旧 hint 重投也不得重新拉起普通 actor。核实者持独立 fence 仅执行获准 query/cancel 和报告，旧 holder 写被拒。
- 当前真实 A2A 的 ByRequest 不支持反查；按请求恢复须有明确扩展/adapter 交付及真实 HTTP 验收。扩展 profile 测索引迟到后主动发现，标准 profile 测安全保持未知且不重发；不能以假 port 宣称支持。停止仍未知则按 spec 的核实期与隔离规则对外，不因 deadline、重启或普通管理员解锁提交终态。

## 5. 读取：按证明 Get，按同一版本 List，联冻入站、内部与公开预算

每次读、订阅、翻页或取消都校验当前资源来源和相应权限。撤销发布阻止新调用，既有资源仍按当前权限访问；撤销读取权限则旧游标、订阅也不能继续披露。Get 校验本 Tid 的证明与归档，不等待列表索引。

List 首页取得按授权群过滤、按 Tid 唯一的版本化快照，行、total 与状态都按该 cut，排序 `(status_updated_at DESC, tid ASC)`；游标绑定主体、群、筛选、排序、cut、末键和有效期。**T1 存储原型**须证明：查询开始前已公开的提交均被 cut 覆盖，即使部署有多个写者、索引异步且热数据正转冷。有界等待后仍不能证明则返回可重试依赖错误，不返回过时 total。不能仅凭索引“最近更新时间”宣告追平。

公开响应/单 SSE 帧暂以 **8 MiB 为候选**；#4245 内部 Feed 为 128 KiB/事件、256 KiB/批，Worker 入站解引用另受 8 MiB 的 `PHASE_ONE_MAX_INPUT_PAYLOAD_BYTES` 限制。D0 同时冻结下游 IO/报告/Worker 解引用与公开编码预算；中间节点也受入站限额约束，小最终结果或引用外壳不能豁免。超限须保留独立核验的停止事实，再交付有界错误经过停止屏障，不走损坏隔离或静默截断。测试大中间产物、小候选最终产物，以及入站边界值。大公开产物在 Feed 内使用带摘要的不可变引用，保护到 append/publish 均完成；提交前分别验证入站合同、实际存储编码和完整 wire 表示，失败不提交 `Completed`。D0 冻结前须签定权限矩阵与撤权生效点、游标期限、默认及非默认投影的实际预算；不把候选值写成已上线配置。

**评审顺序：** 先用同一对 A/B 走通第 1、3 项每个写入窗口，再验证第 5 项的快照覆盖证明；第 2、4 项可并行固定重试和远端证据向量。T1 已增加下列受限正向原型，实际命令与剩余缺口见 [验证记录](validation.md)。真实 provider、跨进程接管和发布验收仍按 plan 单独交付，不以模型原型替代。

## T1 正向原型的具体选择（尚非生产冻结）

- **发现先于许可：** 在授权 scope/group 的有界目录中预登记 Sid，再由合法 shard owner 创建/封存 checkpoint。准备目录不是 Accepted；接管扫描目录并读取权威 checkpoint，只有已许可、未派发的轮次生成待执行项。原型最多 16 个 Sid，每 Sid 保留 16 轮，满则受理前拒绝，不靠静默 GC 维持容量。生产规模与回收不能照搬测试上限。
- **证据保留：** 原型把已受理请求、规范化引用、终态证明及两项发布许可保留在同一 checkpoint 的轮次记录中，业务 head 推进不移除旧记录；不依赖旧 `resolve_commit` 查询历史。跨 Sid 请求预留绑定完整首次请求、预期 checkpoint head 和 owner；预留本身不授权执行。未决异请求只报依赖未决，不伪报 Accepted/Conflict；CAS 失败后读回区分 Replay/Busy。废弃时先 seal 新 owner 或推进原 head 栅栏，再以精确版本把意图标成 abandoned；禁止直接删键，旧提交即使已读到预留也会被 checkpoint 栅栏拒绝。同请求接管只在原 generation 仍有效时恢复首次依据，不能自动升级至新轮。冷证明转存/压缩尚须在联合版本验证。
- **外部效果与发布：** 模拟接收端写入前先提交 dispatch_intent；效果已发生但 ack 未提交仍必须等匹配 RoundKey 的停止事实。Get 重新计算归档和 closure 正文摘要。终态 CAS 同时固定事件、稳定 event ID、前驱和快照内容；独立 publisher 只读取权威许可，在自己的 CAS Feed 文档 append，随后发布对应快照，不申请执行 owner、不推进执行 checkpoint。重复发布读回同内容，篡改/越序/未 append 快照均失败。保留引用和许可，不用原型的有限生命周期证明生产 GC。
- **列表覆盖：** 有界双重读取目录版本和每个 Sid 的 checkpoint head，中间读取对应版本的投影；只有前后目录/head 相同且全部投影追平才生成不可变 cut，否则报可重试依赖错误。每次最多两次目录读、每 Sid 两次 checkpoint 读和一次投影读；原型不做无限等待。S1.2 原型已把该 cut 物化为首页一次的不可变快照，续页只读快照 + 签名游标（绑定主体/群/筛选/排序/cut/末键/有效期），不逐页重跑实时 Get；生产物化索引与到期游标协议仍归 T4/T8（物理 key 见 S1.2 节）。
- **候选 profile v1：** 入站和公开单帧各 8 MiB，Feed 单事件 128 KiB、单批 256 KiB，分页最多 100 行，快照游标有效期 300 秒。默认历史投影为 0 条，非默认历史最多 100 条且与全部 Part 共同计入公开预算；无法完整编码则拒绝该投影，不静默截断产物。值在合同中明确；S1.3 已在原型级完成真实序列化的实际编码验证（B−1/B/B+1，见 S1.3 节），生产编码与索引保留验证仍归 T4/T8，不宣称上线默认。
- **权限矩阵：** New 要求当前 published+invoke；Replay/Get/List/Subscribe 要求当前 read，撤销发布不剥夺已有读取权；Cancel 要求 read+cancel。订阅在每次输出前重验授权并在撤权通知时关闭；翻页重验，不能用旧 cut 绕过撤权。下游 start/resend/continue 与 query/cancel 继续使用独立节点委托，不从入口 read 推导。
- **边界：** 原型 sink 是幂等测试接收端，不是 Connector；archive 是测试文档，不是 SQL；append/publish 使用独立 CAS 文档和真实内容许可，但不是 #4245 TaskFeed。正向原型证据不能证明这三条实际链路已经集成。

### S1.1 生命周期闭环原型的物理 key 与写者（尚非生产冻结）

下表固化测试级原型（`bcs-dist-store/tests/group_round_support`）的物理 key、唯一写者和 CAS/转存边界，checkpoint 仍为唯一执行权来源；同名事实在夹具模块 rustdoc 中以表格维护。该表描述测试 Model 文档，不是生产 SQL/表。

| 切面 | 物理 key | 唯一写者 | CAS/转存边界 |
| --- | --- | --- | --- |
| 发现目录 | `t1-group-registry`（Sid 行） | 受理 Worker（发现先于许可，无需 owner seal） | 整文档 CAS；行数 ≤ 原型上界，满则拒绝，仅栅栏证成后显式回收 |
| 请求意图（准备/废弃/过期墓碑） | `t1-request-registry` 内行 `{rid,state,request,head,owner}` | 受理 Worker（行绑定预期 head/owner） | 单文档整 CAS，无多键事务；废弃先栅栏后标 abandoned，墓碑长期可读且原型内不回收 |
| 执行许可+轮次热证据 | checkpoint `runner_state.records[]`（`layout.ckpt(sid)`） | 已 seal 的 shard owner | 精确 `commit_seq` CAS；受理、终态、压缩、过期摘除同源 |
| 冷证明 | `cold:<tid>` 文档 + checkpoint `state["cold"]` 索引 `{tid,digest}` | seal 后的 owner 先转存 | create-if-absent；读回与热证据相符才允许 checkpoint CAS 压缩；每次读回重算摘要，不符即错 |
| 归档/closure | `archive:<tid>` / `closure:<tid>` | 模拟接收端 / finish 时的 owner | create-if-absent，摘要读回核对 |
| Feed 许可与效果 | checkpoint 行 `feed_permission` → `feed:<sid>` / `feed-snapshot:<tid>` | 独立 publisher 凭 checkpoint 许可 | 许可随终态 CAS 固定；效果独立 CAS，压缩后仍按冷证明核对 |

**过期顺序（墓碑先可读）：** 过期分两步。第一步把请求登记行 CAS 成 `expired` 并读回，从此同请求 `lookup`/`admit` 一律 Expired，终态结果在删除窗口内仍按证明可读；第二步才以 checkpoint CAS 摘除热记录/冷索引，随后物理删除 `cold:/archive:/closure:` 文档。整个窗口不存在可重执行间隙；墓碑行保留，不以直接删键换取容量。

**有界方案（原型参数，非生产容量证明）：** 目录 Sid（默认 16）、请求登记活跃行（默认 16，废弃行不计）、每 Sid 保留轮次（默认 16，= 热记录 + 冷证明）、单记录字节量（默认 128 KiB，受理前与冷转存各验一次，超限拒绝不截断）均为构造参数，测试用显式小上界覆盖满时拒绝、废弃/过期后恢复与回收；废弃行只在显式 `recycle` 先证明旧 owner/head 已被栅栏阻断后删除，过期墓碑在原型中明确拒绝回收（容量限制以依赖错误暴露）。默认 16/16/16 不是生产持续运行证明。

这些仍是模型级证据：真实 provider、SQL 表结构、Blob GC 与联合版本验收依次归 T2/T4/T7。

### S1.2 公共列表快照与游标原型的物理 key 与写者（尚非生产冻结）

下表延续 S1.1 的夹具记录方式，固化 T1 列表/游标原型（`bcs-dist-store/tests/group_round_support/pagination.rs` + `group_round_cursor.rs`）新增的物理 key。checkpoint 仍是唯一执行权来源；这些 key 只服务读取合同，不授权执行。

| 切面 | 物理 key | 唯一写者 | CAS/转存边界 |
| --- | --- | --- | --- |
| 首页列表快照 | `snapshot:<cut-id>`（cut-id = 主体/群/筛选/排序/页大小/行投影的摘要） | 列表采集器（一次首页物化） | create-if-absent，已存文档身份必须逐字段相等；不可变，续页只读它 |
| 快照保留释放 | 同上（版本删） | 保留机制的释放操作 | 版本删；释放后续页明确报 `snapshot_released` 依赖错误，绝不静默回退实时视图 |
| List 读权限 | `t1-read-acl` 行 `{principal,groups[]}` | 授权夹具操作 | 首页与每次续页都重验当前行；撤销即时生效，旧游标不能绕过；重新授权后同一保留游标可继续 |
| 行排序通道 | checkpoint 轮次记录 `status_seq`（受理提交序号，终态行由 `terminal.commit_seq` 覆盖） | seal 后的 owner（与执行授权同源） | 生产用真实 `status_updated_at` 时间戳列（T4），原型把权威 commit 序号当作排序/末键通道 |
| 签名游标 | 客户端票据 = canonical JSON claims + HMAC-SHA256（原型常量测试密钥） | 列表采集器签发 | 生产密钥管理与轮转不在本层（T8）；防篡改只依赖 MAC，不依赖隐藏字段 |

**续页读取有界：** 续页最多 1 次 ACL 读 + 1 次 `snapshot:<cut>` 读，0 次实时 checkpoint/目录/投影重跑；首页继承既有双采集上界（2 目录读、每 Sid 2 checkpoint 读 + 1 投影读）再加 1 ACL 读 + 1 快照 CAS。原型页大小与游标有效期为构造参数（候选 v1 值 100 行 / 300 秒，尚待真实编码和索引保留验证）；快照行数受原型容量上界约束（16 Sid × 每 Sid 16 轮）。到期、版本清理、撤权、篡改、跨主体续页均明确拒绝；投影落后或并发提交时按既有可重试依赖错误失败闭，不做无限等待，不返回过时 total。

排序证据边界：`(status_updated_at DESC, tid ASC)` 的排序/末键语义在模型内以 commit 序号通道证明（含同值 tiebreak、跨页不重不漏、total 不变、热转冷/新增/状态更新交错冻结）；真实时间戳列物化索引与 HTTP 协议属 T4/T8。

### S1.3 完整编码预算与失败收尾原型（尚非生产冻结）

本节记录 S1.3 在测试级原型（`bcs-dist-store/tests/group_round_support/encoding.rs` + `group_round_encoding.rs`，service-api `group_round_vectors.rs` / `group_round_contract.rs` 正向向量）上实际验证的编码预算语义；候选 profile 数值本身仍由 S1.5 统一登记冻结，本节不改变其状态。生产边界归 T4/T6/T7/T8。

**实际编码（非裸字节）语义：** 所有预算都以 serde_json 真实序列化字节数计——外壳、JSON 转义、历史、摘要引用与解引用内容一并计入，B−1/B/B+1 通过精确求解填充字节命中精确编码位置（入站 4096/B、事件批量 512/2048/1024、公开帧 2048 与候选 8 MiB 各测过三点；8 MiB 与 128 KiB 事件量级证明与显式小预算同一定律成立）。转义样本证明同字符数 '"' 内容比 'x' 多一倍字节且在边界处被正确拒绝。入站 9 MiB 中间产物以流式 `ChunkSource` 检测：读到 `PHASE_ONE_MAX_INPUT_PAYLOAD_BYTES`(8 MiB)+1 即停，超大对象从不完整载入；测试通过 `RoundBudgetProfile::v1()` 与真实常量做等值钉（inbound = 8 MiB 常量，event/batch = 128/256 KiB）。

**三层有序 artifacts 贯穿与还原：** 同一组有序 Part（含转义样本与数据 part）入站完整解引用编码可解、内部 Feed 事件内联承载、公开 Get 帧（外壳+parts+history 投影+deref 结果）与 SSE 快照按同一 digest 还原一致。默认历史投影 0 条、非默认 ≤ 100 条且计入公开帧预算，编不下则整体拒绝该投影（`projection_frame_budget`），不截断产物。

**超内联预算 → digest 引用与 pin：** 事件（#4245 形态 `{"key","id","kind","result"}` 字节级预算）装不下时改用带 digest 的归档引用事件；引用/批/快照外壳都必须各自通过校验，否则不提交成功 → 有界失败收尾（B+1 的帧与批外壳场景均验证绝不公开 Completed）。带 digest 引用的发布物证在 append+publish 完成前 pin：过期清理在该窗口内拒绝物理删除（`archive_pinned`），义务完成后释放。内容损坏 → Get/SSE 有界读取错误（digest 不符），内部 feed 仍可写可读（内部事件能写 ≠ 公开帧能完整还原）；重传修复、blob 丢失（引用缺失 → `archive_missing` 依赖错误）、重归档恢复三条路径均已覆盖。

**超大中间结果完整屏障：** attempt 级派发/停止事实文档（`sink/stopped:<tid>#<attempt>`，身份 = encode(key)+attempt，独立持久化、身份核验、拒绝静默覆盖）；统一停止屏障要求每个已派发 attempt 都有核验过的停止事实（未记 attempt 的旧式整轮派发仍核对整轮 `stopped:<tid>`）。失败收尾经同一屏障提交有界、稳定 ID 的失败事件（无 artifact 事件），失败记录只含有界 reason（≤1 KiB），超限字节既不是停止证明也不进入产物；结果读取失败不伪造停止；兄弟未停阻止整轮终态（下一轮受理保持 Busy）；失败记录经正常证明路径读回，不落入损坏隔离；超限结果的所有下游派发被拒、交付效果为空。几 KB 候选最终产物不能豁免大中间结果（正向向量钉）。

这些仍是模型级证据：#4245 task_feed 的真实 append/publish 实现与引用转存归 T4/T7，Worker IO 限长与入站解引用归 T6/T7，公开 HTTP/SSE 协议与游标密钥归 T8。
