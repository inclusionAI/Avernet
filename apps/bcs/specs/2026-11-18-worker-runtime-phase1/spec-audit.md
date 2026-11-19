# PR #4144 原始 SPEC 逐条核对

Historical evidence: output byte quotas and original Gateway assumptions below
are superseded for the current integration by the [2026-11-19 amendment](../2026-11-19-gateway-worker-admission/verification.md). Historical results remain
bound to their recorded heads; they do not certify the new head.


本稿用于复审定位，不是全部验收完成声明。基线固定为 Avernet `8811efbc9751004747451d00c5ea1e6a30a6b224` 中 `apps/bcs/specs/2026-11-18-worker-runtime-phase1/spec.md`，即 #4144 原文；没有用实施分支后来修改的 SPEC 替换要求。原始三个审计分片检查的实现为 `657e324b1184bad5b5d15e72cb1a1c6c1198b5a2`；本稿另行纳入随后 rebase 后的启动容量和连接池压力补丁及其验证。随后生命周期新增五个target的13项公共组合测试（四target12项并集+quota1项）通过，SPEC/质量审查与五target严格Clippy通过；质量修正后的snapshot race另行1/1通过。第三轮真实PROXY指定控制/输出/监督场景通过，不将其外推为13项全部L3。

截至本稿快照：主要生产逻辑、确定性故障测试和指定的真实 PROXY 组合已存在；启动最大候选证明、实际 provider 封装预算契约、大 AppendText 外置回归和持续连接池压力准入已补齐。G1–G5公共模型主链与quota精确场景均已补并审查通过。第三轮真实PROXY正常回复/重复、70000字节AppendText、独立Cancel、实际SendingUnknown监督回合及晚到确定证据消费通过，取得该场景真实命令/字节观测并清理64个owned keys。没有将全部模型故障断点升级为L3。Singlebox opt-in内存Store+SQLite最终12个instrumented实际进程场景通过，独立SPEC/质量审查通过；按仓库原工具与同ignore配置从merge-base测得changed-line9118/11983=76.091129%，超过未修改的65%门槛。发布候选 `decdab6ea7` 的完整远端 CI 已通过；后续 Review 增量以其自身 PR head 检查为准。OB 实例验证继续 defer。

## 口径与本轮边界

- L0：静态生产源码或契约审查。L1：纯状态转换、序列化上界或确定性契约测试。L2：公共 Worker/Store/TaskFeed/DbArchive 生产实现与内存故障 transport、SQLite 的组合测试。L3：已保存的真实 PROXY conformance 或选定多 Worker 功能/故障运行记录。测试名中的 `real_providers` 只说明使用公共生产类型，不能自行升级为 L3。
- 下列“已验证”是检查到的具体测试及保存的执行证据。本次汇总不重新运行测试。每个场景只记实际层级，不从测试总数推导全矩阵覆盖；不同运行集互相重叠，不能把 83、381、44 相加为独立测试总数。
- 用户最新明确排除本轮本地压力/容量测试。DEV 延迟及低容量水位只作诊断，不用于认可或否定生产容量，不再执行深历史、持续吞吐、接近水位压测，也不再索取负载环境。小规模 PROXY 功能/定向故障、确定性公共资源检查仍继续。原 §8.2/§9.2 的容量认证没有被冒称通过；未认证配置不进入生产启用清单。
- 存储 HA、节点切换时版本/持久写/租约不回退，按用户确认的平台前提记录；本轮没有独立执行真实存储节点故障切换，不以 Worker 接管或客户端断连代替。它不是待实现的 Worker 功能。
- 真实归档组合目前是公共 `DbPlugin` + SQLite。OB 实例验收按用户授权 defer。Singlebox 内存 Store 被明确授权用于实际 opt-in 进程的功能验收；它不证明 Store 持久重启或节点切换，也不替代既有真实 PROXY 组合。
- 主逻辑仍归 Avernet。宿主仅拥有存储传输/编码适配、通用资源和命令观测、私有组合与验证。无 SDK 验证替代 PROXY。本稿不含私有 endpoint、租户、凭据或工作树路径。
- #4144 是原 SPEC PR；实施、验证、生产启用分别报告。原评论已逐条回复章节与证据，本任务没有改变 resolved 状态；#4144 合入后读取到六条均已 resolved，不能继续称它们仍然打开。

路径简写均相对公共仓库 `apps/bcs/crates/`：`A`=`plugin-api/bcs-store-api`；`C`=`service-api/bcs-service-api`；`M`=`contracts/bcs-dist-model`；`P`=`services/bcs-dist-projection`；`S`=`services/bcs-dist-store`；`W`=`services/bcs-dist-worker`；`T`=`test-support/bcs-test-support`。`W/processor/*` 表示 `W/src/processor/*`，`S/inbox/*` 表示 `S/src/inbox/*`，其他 `src` 同理。测试函数省略 `()`。

## 明确的接口采用与补充边界

1. **DocumentCas 命名适配。** 原文称 `PersistentJsonCas`；实际采用已有 `StoreCapability::DocumentCas` 与独立 `DocumentStorePlugin`，受限持久接口提供 create/EQ/Replace/Patch，Inbox 和 Coordination 已显式改用它并有契约测试。没有只放松 capability 检查，也没有弱化原 `AtomicJson`。
2. **最大合法候选的显式收紧。** 每个 Decision 最多一个 `SetStatus`，重复状态动作整批拒绝；这是 §3.3 允许的批次上限收紧，用于保证不可外置的 interaction 元数据不撑爆 outbox。当前 Runner 契约只允许当前 tid 的动作；跨 task 动作批次非法，因此没有将一个合法跨 task Decision 拆分处理的分支。Worker 负责队列与下一任务重新 init。不能把这些实施选择伪装成 #4144 原文已指定的数字。
3. **容量声明。** 新 `SessionCheckpointCapacity { max_document_bytes, max_envelope_bytes }` 同时涵盖存储封装；`SessionStorePort::checkpoint_capacity()` 缺省 None，Worker 拒绝未声明的组合。`CoordinationStoreConfig.storage_envelope_bytes` 是宿主声明的最大私有封装字节，默认 64 是配置而不是测量。公共 provider 构造时验证完整 envelope，CAS 前再次按实际编码检查。宿主必须证明其 wrapper/native 上限；新增宿主适配最大版本 wrapper 编码边界测试已通过（scripted transport，未向 DEV 发请求），不能据此推导全体容量认证。
4. **额外有界项。** legacy `recovery_attempts` 最多 64 项、编码列表最多 8 KiB、单 ID 最多 256 编码字节；原身份重试优先判定。Processor `retry_delay` 仅接受 5ms..=5s。新 Inbox 持续普通池压力阈值默认 200ms，允许 5ms..=5s。
5. **压力事实与策略。** `StorePlugin::pool_observation` 提供明确的 `Unpooled` 或 `Bounded { capacity, in_use, waiters, congested_for }`，None 不代表空闲。持续时间是普通池“全满或有排队者”的单调连续时长，不能在槽位交接时重置；blocking 池独立。Avernet 仅在新 Start 的新持久预留前执行反压；已存在身份、Unknown、repair、控制及关键回流沿原协议推进。没有将策略放入通用存储读写，也没有“SDK 自动视作无池”的假设。

## §3.1 持久 JSON CAS

| 编号 | 原要求；生产实现 | 已验证 | 尚缺或限制 |
| --- | --- | --- | --- |
| 3.1-1 | 原子 create-if-absent、首次版本 1；`A/src/document.rs` 的独立 Document 接口与宿主映射已实现 | L1 `T/src/contract/plugin/store/document.rs::document_store_contract_tests`、`document_concurrency_contract_tests`；L3 `document_cas_proxy_dev` | create 与 EQ 分别验收，不能互相推导 |
| 3.1-2 | 精确版本更新、同值也推进、失败不改、耗尽不回绕；同上 | L1 `document_paths_contract_tests`、`document_version_exhaustion_contract_tests`；L3 Document conformance | 公共要求是成功版本严格推进，并非强制所有后端每次恰好 +1 |
| 3.1-3 | Replace 删除旧字段；有序 pointer Patch，null/missing、数组与多处失败原子性；`A/src/document.rs` | L1/L3 `document_paths_contract_tests` 与真实 Document conformance | 默认 Patch 是 read→全量内存校验→original-version EQ，允许全量读写成本；不称物理局部更新 |
| 3.1-4 | 私有元数据隔离，业务 `rev`/`_store` 可用，不强制全后端存字符串 | L1 `document_store_contract_tests`；L3 Document 私有编码边界 | 无已确认实现缺口 |
| 3.1-5 | 子集无 TTL/delete/Any/Newer，保留 AtomicJson；消费者显式采用 | `S/tests/admission.rs::{admission_does_not_require_full_atomic_json,admission_requires_document_cas,worker_reads_the_admitted_spec_through_the_same_document_api}`，L2 | 不承诺删除重建后的历史；DocumentCas 命名采用见前文 |

## §3.2 输出投影

| 编号 | 原要求；生产实现 | 已验证 | 尚缺或限制 |
| --- | --- | --- | --- |
| 3.2-1 | 稳定 `(node,artifact_id,class)`，Upsert 保持首次位置；`P/src/lib.rs::apply` | L1 `P/tests/projection.rs::stable_identity_and_first_appearance_order_survive_replacement` | artifact_id 的纯计算依赖 Runner 契约，运行时不能阻止任意第三方代码私下取随机数 |
| 3.2-2 | AppendText 从空文本创建、非文本拒绝、无隐式 merge；同上 | L1 `append_creates_text_and_a_late_invalid_action_rejects_the_whole_batch` | 无已确认缺口 |
| 3.2-3 | 版本化有序 snapshot、Inline/Ref、事件同操作，checkpoint/restore 共享 projector；`P/lib.rs`、`W/decision.rs`、`S/task_feed/reader.rs` | L1 `validated_output_retains_the_original_operation_and_full_display`；L2 `initial_empty_snapshot_and_append_restore_share_pure_projection` | 无已确认缺口 |
| 3.2-4 | Thinking 临时、不入展示/归档/策略；`P/lib.rs`、`W/thinking.rs` | L1 `thinking_is_ephemeral_and_never_consumes_persistent_quota`；L2 `thinking_is_delivered_once_without_runner_snapshot_or_usage_changes` | 临时事件是有界有损通道，不是持久输出 |
| 3.2-5 | SetStatus 只非终态，整 Decision 校验后再候选，终态走 FinishTask；`W/decision.rs`、`transitions.rs` | L1 `whole_decision_rejects_terminal_status_after_an_otherwise_valid_show`、`finish_requires_archive_confirmation_and_a_separate_terminal_commit`；新增重复 SetStatus 拒绝回归 新增 L2 L-G1/L-G3 正常回复、取消、终态归档故障链。 | ≤1 SetStatus 是实施收紧；相关公共控制/终态故障链已补，P-L3增加正常控制/终态链，但没有逐项远端重测所有模型故障。 |

## §3.3 输出额度、引用与启动容量

| 编号 | 原要求；生产实现 | 已验证 | 尚缺或限制 |
| --- | --- | --- | --- |
| 3.3-1 | 创建冻结额度和格式，接管/配置变化不变；`P/lib.rs::validate_task_limits`、`S/inbox/validation.rs`、checkpoint | L1 `task_frozen_limits_are_checked_independently_of_current_host_configuration`；L2 `snapshot_waits_for_complete_append_and_cannot_change_frozen_limits` | 无已确认缺口 |
| 3.3-2 | unit 1MiB、累计/完整 snapshot 各 8MiB、inline 64KiB；按编码字节和 metadata，替换不返还、重放不重复 | L1 `candidate_charges_exact_json_bytes_without_refund_but_restore_does_not_charge`、`cumulative_snapshot_and_unit_budgets_are_independent`、`encoded_snapshot_limit_includes_escaped_identity_and_order_metadata`、`append_and_replace_size_deltas_match_independent_full_json_encoding` | 这些是安全 profile 与算术验证，不是实际后端容量认证 |
| 3.3-3 | Thinking 不占持久额度仍受单条/速率/事件上限；`W/thinking.rs`、`engine.rs` | L2 `oversized_thinking_does_not_block_the_session` | 无已确认缺口 |
| 3.3-4 | checkpoint≤1MiB 含 outbox/provider，outbox≤256KiB，普通候选分别预留≥64KiB；`W/commit.rs`、`references.rs`、新增 `capacity.rs` | L1 `second_externalization_pass_reserves_the_actual_provider_envelope`、`externalization_includes_the_ids_and_digests_added_when_sealing`；L2 `startup_accepts_maximum_shapes_at_both_envelope_reserves` | 原生产启动缺口已补；宿主必须满足声明，不能只引用默认值 |
| 3.3-5 | action/unit/queue/dispatch/ref metadata 明确上限，不能靠 Ref 绕过；`P/metadata.rs`、`W/decision.rs`、`references.rs` | L1 `count_identity_and_current_unit_bounds_cannot_be_bypassed_by_small_payloads`；L2 `inadmissible_physical_profile_or_binding_never_creates_a_task` | 新增 ≤1 SetStatus；task 最多 64 dispatch 的固定合法集合可在有界收尾中容纳，提高上限需重新证明/分页 |
| 3.3-6 | 最大普通、独立收尾与终态候选启动证明；`W/capacity.rs::validate`、`engine.rs` | L1 `startup_checks_all_effect_shapes_and_both_envelope_endpoints`；L2 `startup_rejects_undeclared_or_incompatible_provider_frames`；64/128KiB 两端均验 | 已闭合：覆盖 full RunnerState、全部 effect 形状、排队终结仍保留 Running 状态/第二 draft、冻结 Inline snapshot/result、时间/发布 metadata 最大宽度；没有运行时 I/O |
| 3.3-7 | provider 完整封装也在启动与写前检查；`S/coordination/{capacity,session}.rs` | L1/L2 `maximum_provider_envelope_includes_wrapper_at_both_capacity_endpoints`、`startup_rejects_a_wrapper_that_consumes_the_control_envelope`、`exact_document_budget_counts_the_storage_wrapper_before_cas`、`envelope_cannot_spend_the_reserved_business_frame`；L1 宿主适配真实编码路径的最大版本 wrapper 边界通过（scripted transport，无 DEV 请求） | 公共 envelope 上界 58,866B，含默认 64B 为 58,930B；exact fit 接受/+1拒绝。仅证明编码上限，不证明吞吐/扫描容量 |
| 3.3-8 | 调度 slots 同时限数/bytes，control/legacy 历史不吃业务 frame；`S/inbox/admission.rs`、`coordination/control` | L2 `inbox_capacity_rejects_before_new_intent_and_reserves_control_and_repair`、`recovery_attempt_bytes_preserve_room_for_control_and_retries` | legacy 新增 8KiB/256B 边界；原身份重试不再消费新配额 |
| 3.3-9 | 稳定 Blob put 确认后才能 Ref，Unknown 原身份确认，draft/publish/save 共用；`W/references.rs` | L1 `large_event_and_snapshot_are_durable_refs_before_checkpoint_submission`、`unknown_blob_without_confirmation_does_not_install_a_reference`；L2 `lost_commit_response_keeps_the_decision_and_large_blob_identity` | 无已确认协议缺口；真实累计 I/O 证据见 3.3-13 |
| 3.3-10 | Upsert/AppendText/result 大载荷均外置；解引用核 digest、按相同编码计费；`references.rs`、TaskFeed storage/reader/follow | 新增 L2 `one_mib_append_externalizes_outbox_and_restores_the_same_complete_text`；既有 `follow_resolves_references_and_does_not_skip_events_at_resolved_byte_limit`、`follow_rejects_missing_and_corrupt_blob_before_delivery`、`follow_delivers_a_unit_at_the_exact_one_mib_json_boundary`、`referenced_json_uses_the_same_resolved_encoding_budget_as_inline` L2 L-G2 增加quiet tail接管和慢publish/trim完整恢复。 L3 P-L3正常回复产生70000字节AppendText，恢复完整原文。 | 1MiB边界、大AppendText与quiet/故障组合仍以各自L2证据为据；P-L3只证明其70000字节指定场景。 |
| 3.3-11 | orphan Blob 留存，IO 不进纯 Runner；`W/references.rs`、`S/archive` | L0 留存实现；L2 `lost_commit_response_keeps_the_decision_and_large_blob_identity` | 物理防淘汰/回滚是宿主平台前提 |
| 3.3-12 | quota 整批拒绝，保留此前确认内容，当前 task 独立 Failed+cancel，无 Session 隔离，终态发布后下一 task init；`prepare.rs`、`lifecycle.rs` | L1 `output_exhaustion_is_distinct_from_unbounded_private_runner_state`；L2 `output_quota_discards_the_entire_decision_and_does_not_poison_next_task`、`real_providers_archive_then_publish_terminal_before_starting_next_task` L2 L-G4补独立cancel完整链；L-G1证明timeout/step后下一task正常。 L2 L-Q新增quota精确场景：完整旧display/usage11保持、合法新artifact和超额Append均拒绝、cancel、init2/calls3、nexthealthy Completed且ControlNormal。 | 原quota漏测已闭合；跨task合法Decision不存在，见契约采用。 |
| 3.3-13 | snapshot/event/Blob 实际累计读写分别计量，不宣称 O(n)；公共逻辑外置已实现，宿主新增计量 wrapper | 通用 transport 命令/RESP 字节/时延/冲突/取消已实现并以 scripted transport 验证；已有方法调用计数不能替代这些量 L2 L-G2实际完整snapshot Blob端口尝试198300B/唯一66100B/故障retry66100B/其他幂等重复66100B；质量修正后目标测试通过。 L3 P-L3取得snapshot/event/payload_blob/snapshot_blob/final_record分类端口字节与通用transport观测。 | 本次指定控制/输出场景计量已取得；不是所有隔离/恢复故障阶段均新增独立成本分段。端口bytes≠SQL物理/wire，RESP为尝试请求编码/已解码回复估算。 |

## §4.1 持久受理与规范输入

| 编号 | 原要求；生产实现 | 已验证 | 尚缺或限制 |
| --- | --- | --- | --- |
| 4.1-1 | scope/sid/tid/格式/大小，重算摘要、先处理既有规范身份；`S/inbox/{validation,producer}.rs` | L2 `caller_digest_cannot_hide_changed_business_content`、`ids_that_disagree_are_rejected_before_any_read`、`documents_of_an_unsupported_version_are_never_written` | 读取失败保持 Unknown，不伪造确定拒绝；新压力 gate 不跳过既有身份 |
| 4.1-2 | 关键 Agent 身份不依赖 holder/收包时间/次数，旧 holder 终态仍有效；validation/identity/consumer | L2 `critical_event_is_stable_across_holders_and_survives_a_closed_gate`；L3 指定 late supervision | Agent 模型不额外引入瞬态收包时间；不是所有回流组合都已 L3 |
| 4.1-3 | 先 durable shard 意图，再 scope-wide canonical，log 不授权建 task；producer/journal/progress | L2 `writes_happen_in_the_fixed_order`、`canonical_identity_is_scope_wide_across_shards`、`concurrent_cross_shard_identity_has_one_winner_and_no_losing_task` | journal 是 root+独立 intent 文档；原文未强制单物理 Stream |
| 4.1-4 | bounded slot EQ 绑定 canonical/gen；仅 Normal 的新 Start 登记，controls 保留配额；admission/journal | L2 `old_canonical_never_rebinds_after_reopening`、`inbox_capacity_rejects_before_new_intent_and_reserves_control_and_repair`；新增 pressure 七用例 | controls 仍走身份/登记/激活协议，不能理解为完全绕过所有 gate |
| 4.1-5 | immutable spec、create-once task、append 后固定 canonical position；progress | L2 `an_existing_task_is_never_replaced`、`repeated_creation_preserves_the_first_spec_and_task`、`every_lost_write_repairs_without_a_caller_retry` | 同 group 保留首份冻结 spec |
| 4.1-6 | 同 schedule EQ 写 Accepted+activation，结论复制后才删 slot；admission/progress | L2 `accepted_activation_survives_close_and_lost_confirmation`、`appended_but_unactivated_is_rejected_with_a_durable_finalization_obligation`；L3 lost hint 发现 | hint 失败不能推翻权威成功 |
| 4.1-7 | unresolved 前停止该 Session，duplicate/rejected 只在修复/收尾持久后推进覆盖；consumer | L2 `unresolved_position_blocks_only_its_session_and_restarts_replay_uncommitted_work`、`duplicate_physical_records_advance_coverage_without_business_inputs` | 无已确认缺口 |
| 4.1-8 | Accepted 历史不反悔；隔离拒绝持久稳定；未知不返回确定 NotApplied；producer/outcome | L2 `pending_canonical_retry_never_reports_definite_failure_then_accepts_later`、`precanonical_read_failures_remain_unknown_and_repair_without_retry`、accepted activation 测试 | 持久责任产生前的容量/压力 NotApplied 可重提；不能与已 durable 的 Unknown 混称 |
| 4.1-9 | 旧 canonical 永不重绑；log-only 收敛首次 canonical；changed body 冲突不覆盖 | L2 `old_canonical_never_rebinds_after_reopening`、`log_only_old_contender_can_converge_to_new_canonical_generation`、跨 shard 单赢家测试 | 无已确认缺口 |
| 4.1-10 | cursor/连续完成水位分离，固定 cut、公平有界、不能越 gap；maintenance/scan_scheduler | L2 `permanent_gap_does_not_hide_later_intents_and_watermark_never_crosses_it`、`durable_fixed_cut_advances_across_provider_restarts_and_new_history`、`slow_pending_retry_cannot_consume_every_forward_scan_turn` | 深历史/持续追加的本地压力验证按用户要求排除；L2 精确语义仍有效 |
| 4.1-11 | journal/canonical/dedup 无自动 GC/expiry，前提下无到期去重窗口；持久 Document API/consumer | L0；L2 duplicate/restart 用例 | 防回滚/eviction 是确认的部署前提；不得写无条件永久去重 |
| 4.1-12 | intermediate `(did,epoch,seq)` checkpoint 去重；append 后激活未知仍可发现；consumer/prepare/maintenance | L2 `an_intermediate_agent_event_is_appended_and_activates_the_session`、reader 去重与 maintenance 用例 | 关键终态不走中间事件代次过滤；不是每个持久 cut 都已重复 L3 |

## §4.2 权威调度与修复

| 编号 | 原要求；生产实现 | 已验证 | 尚缺或限制 |
| --- | --- | --- | --- |
| 4.2-1 | 独立 owner/activation/due/listening/gate/gen/slots；每激活推进版本，ZSet 只 hint；state/scheduling/admission | L2 `forged_owner_and_old_activation_tokens_do_not_authorize_settle`、`listener_reads_durable_schedule_state` | 无 VersionedZSet 依赖 |
| 4.2-2 | observe 安装 fence 后最后检查；settle exact owner/token，拒绝旧决定；scheduling、W acquire/settle | L2 forged owner/token；`unavailable_scheduling_fence_keeps_acquire_outstanding` | checkpoint/shard owner 不等于调度权限；真实 PROXY 主动交接矩阵未单列，非接口未实现 |
| 4.2-3 | 单文档 EQ bounded attempt/time；冲突重读/退避；已 append 未激活保持 Unknown+repair | L2 reliable_inbox fault 矩阵与 `repair_makes_durable_progress_at_five_and_ten_millisecond_document_rtt`、80ms sliced scan 系列  L3 P-L3新增成功运行中的真实命令计数与条件冲突/取消分类。 | 该记录不是专门高负载调度争用实验；bounded EQ/fairness仍以L2精确断点证明，本地压力排除。 |
| 4.2-4 | repair/discovery 游标、预算、完成条件独立；完成水位不授权 GC；maintenance | L2 `discovery_survives_deleted_hints_and_completed_repair_watermarks`、durable fixed cut | 无已确认缺口 |
| 4.2-5 | 记录物理布局、热点、扫描成本；KeyLayout/root/intent docs、宿主记录 | L0 布局；L3 旧小规模 fixture 配置/方法调用诊断  L3 P-L3有真实命令/字节分类与max encoded document观测。 | 仅该小规模功能布局与观测，不是最大历史/物理key容量认证。 |
| 4.2-6 | 扫 next_check/unconsumed/obligations，due=None 仍查，固定 cut 轮转，接管包含未来 deadline | L2 `discovery_survives_deleted_hints_and_completed_repair_watermarks`、`takeover_enumeration_is_not_blocked_by_another_corrupt_canonical`；L3 删除 hint + successor 接管 | 特定未来 deadline/主动交接 L3 未单列，不由基础接管推导 |
| 4.2-7 | watch 只是提示；退出先 unwatch，再 observe/check/settle；reader 监督重建 | L2 `full_reader_capacity_rebuilds_without_losing_pending_input_or_unknown_commit`、`a_locally_released_session_can_be_reacquired_under_the_same_owner`、`releasing_a_quiescent_session_frees_local_state_and_subscription` | 相关资源模型通过；不等于所有真实池故障已测试 |

## §4.3 执行轮次

| 编号 | 原要求；生产实现 | 已验证 | 尚缺或限制 |
| --- | --- | --- | --- |
| 4.3-1 | load/seal→旧effects→read/dedup→Ref→freeze→pure→Blob→CAS→effects→ack/schedule；`W/{drive,prepare,references,commit}.rs` | L2 `real_providers_archive_then_publish_terminal_before_starting_next_task`、`takeover_after_archive_replays_the_frozen_result_without_running_the_reducer`；L3 两 Worker 未知 commit/接管 | L3 是指定断点，不是每个箭头均实际注入 |
| 4.3-2 | 每 Session 串行，全局有界，失败不占满；engine/replay | L2 `a_hot_session_yields_before_draining_all_its_inputs`、`not_applied_outbox_yields_capacity_and_recovers_without_rerunning`、`unknown_outbox_yields_capacity_and_replays_the_same_effect` | 双 Worker 实测不能替代单 slot 公平断点；确定性用例已覆盖后者 |
| 4.3-3 | Ref 在 reduce 前解析和 digest 校验，失败不置空；prepare/references | L1 reference tests；L2 TaskFeed missing/corrupt Ref、`isolation_discards_a_frozen_decision_waiting_on_a_ref_before_authorized_resume` L2 L-G1补正常HumanReply→Continue→archive；TaskFeed缺失/损坏Ref原测试保留。 | 新增HumanReply采用Inline；输入Ref的解析失败/摘要规则依赖已有references和TaskFeed专项证据，不称新用例单独覆盖所有Agent/HumanReply Ref变体。 |
| 4.3-4 | 本轮输入/now 冻结，Runner 无 IO/钟/随机；prepare/risk/FrozenDecision | L2 `succeeded_uncommitted_runner_keeps_its_frozen_time_across_old_outbox_replay`、`successful_effect_history_does_not_freeze_the_clock_for_a_future_deadline` | 纯 Runner 是接口契约，不声称 Rust 类型能封禁任意实现的副作用 |
| 4.3-5 | candidate 原子含状态/consumed/dispatch/deadline/draft/outbox，不修改 confirmed base | L2 lost commit、隔离 fencing、control 原子测试；L3 dropped successful checkpoint reply | 已实现，源码按 clone base + 单提交 |
| 4.3-6 | 仅重复记录仍推进 consumed；InboxPos opaque，不持久化 DeliveryReceipt | L2 `duplicate_physical_records_advance_coverage_without_business_inputs`；L0 checkpoint/slot 类型 L2 L-G1错interaction、错queued tid与late reply都等待consumed实际变化。 | 公共控制重复/迟到已补。 |
| 4.3-7 | ack/trim 只凭 confirmed CommitReceipt，共享分区连续前缀不越未提交 Session | L2 reader/processor execution 回归；`confirmed_checkpoint_and_receipt_advance_together` | 未把这些模型断言冒称所有真实组合通过 |
| 4.3-8 | unknown commit 保留同 ID/digest/candidate，先 resolve、不 effects/新提交，不能以版本大小作证明 | L2 `lost_commit_response_keeps_the_decision_and_large_blob_identity`、`canceling_the_processing_future_keeps_its_unknown_commit_for_resolution`；L3 成功提交丢回复 + successor + Runner一次 | 已实现并有关键 L3 断点 |
| 4.3-9 | 必要 effects 确认后才能替换 outbox；next_checkpoint/drive | L2 archive takeover、unknown/not-applied replay parking；L3 基础接管链 L2 L-G3补archive unavailable/lostreply与terminalpublish失败，下一task保持等待。 | 公共精确断点已补；这些归档/publish故障断点按L2记录，P-L3不包含其全部故障注入。 |

## §5.1 可重放 effects 与发布节奏

| 编号 | 原要求；生产实现 | 已验证 | 尚缺或限制 |
| --- | --- | --- | --- |
| 5.1-1 | 固定 source_commit/effect ID/digest，旧 owner 迟到有证明，同 ID 改内容冲突；effects+Store providers | L2 archive immutable identity、TaskFeed replay、Dispatch lost reply 系列；L3 指定接管 | 非所有迟到组合都 L3，但生产协议存在 |
| 5.1-2 | append→publish→trim，部分 append 固定 ID；transitions、effects validate、TaskFeed | L2 `partial_physical_append_is_not_a_visible_cut`、`append_unknown_at_each_write_resumes_same_ids_without_double_delta`、`snapshot_waits_for_complete_append_and_cannot_change_frozen_limits` L2 L-G2 Notify挂起publish期间trim拒绝，已持久但lostreply后trim允许，再同source重放与完整restore。 | 流式Writer+Reader+trim公共组合已补；不是实际PROXY慢存储容量测试。 |
| 5.1-3 | ID 错误不能假报 AlreadyApplied，回读内容或完成证明；append/effects | L2 `completed_effect_replay_survives_advanced_outbox_and_rejects_changed_body`、append fault 矩阵 | 没有单列 L3 对应注入；不算缺少实现 |
| 5.1-4 | status/interaction/终态必发 snapshot；transitions/archive_confirmed | L2 正常 terminal；L3 指定终态组合 L2 L-G1交互snapshot与L-G3终态发布，持久快照/归档状态明确断言。 | 公共状态/interaction主链已补；P-L3正常回复/终态另有真实证据，不外推全部发布阈值/故障。 |
| 5.1-5 | 128事件/64KiB/1s 任一阈值；任务冻结，Thinking 不计数 | L0 `M/output.rs`、`W/transitions.rs`；L1 snapshot deadline helper 与 Thinking；L2 frozen limits L2 L-G2含127/128事件、32k+34k越64KiB、1999/2000时间边界，阈值测试核持久snapshot。 | 公共三阈值组合已补；字节场景证明跨阈值触发，不声称恰好65535/65536的逐字节边界。Thinking仍由原专项测试证明。 |
| 5.1-6 | counters/coverage/deadline checkpoint 持久，quiet tail 仍发，replay 不重新决策 | L1 `a_persisted_snapshot_deadline_flushes_the_last_small_delta_without_new_output`、`quiet_snapshot_flush_preserves_custom_task_deadlines_and_private_state` L2 L-G2 quiet tail换owner，1999未发/2000发，无Runner新调用；另慢publish/lostreply/trim race。 | 公共恢复与交错已补；1s是健康调度目标，无生产SLO声明。 |
| 5.1-7 | 未达阈值仍 append；旧snapshot+events restore；草稿不授权trim；terminal完整 | L2 TaskFeed restore/retention 系列、archive terminal 测试 L2 L-G2旧snapshot+增量、草稿trim拒绝、race后完整restore。 | 公共节流writer/reader/trim组合已补。 |
| 5.1-8 | Dispatch create 包括持久复查；重试修复，通知失败不否定成功；dispatch journal、effects、supervisor | L2 `creation_retains_provenance_and_repairs_missing_schedule`、`a_persisted_create_intent_survives_failure_and_independent_scan`、`creation_is_notified_and_retains_the_remote_obligation` L2 L-G4由Runner StartDispatch实际创建两个dispatch后取消/接管。 | 公共Runner完整链已补；旧L3手工创建不能回溯升级。 |
| 5.1-9 | continue/cancel receipt 出队后仍保留；Dispatch control/commands | L2 `cancel_unknown_then_lost_write_replay_keeps_receipt_after_terminal`、`lost_or_trimmed_command_hints_do_not_remove_independent_recovery` L2 L-G1 HumanReply→Continue唯一pending input；L-G4实际已提交cancel重放。 L3 P-L3正确HumanReply对应唯一pending input及独立cancel。 | partialcancel/fencing/changedbody全部分支仍L2，不能由正常L3扩展。 |
| 5.1-10 | 累计完整 Blob 写量真实计量，不能根据 Ref 宣称 O(n) | 外置实现已有；新增宿主通用/分类观测局部验证 L2 L-G2修正后完整snapshot put尝试198300、unique66100、fault_retry66100、other_idempotent66100。 L3 P-L3 snapshot_blob实际端口attempted280384B/applied70096B/read350480B。 | Applied只统计新增成功结果，不能把所有其他字节一律归为故障retry；L2 L-G2专门区分故障retry与正常幂等重复。 |

## §5.2 保留与一致读取

| 编号 | 原要求；生产实现 | 已验证 | 尚缺或限制 |
| --- | --- | --- | --- |
| 5.2-1 | trim 前 snapshot+旧 task archive+append completion 覆盖；`S/task_feed/retention.rs` | L2 `partial_physical_append_is_not_a_visible_cut`、`snapshot_waits_for_complete_append_and_cannot_change_frozen_limits` | append 未知不得 trim，协议已实现 |
| 5.2-2 | 先逻辑边界 CAS 后 native trim，失败不扩大范围 | L2 `logical_gap_precedes_physical_trim_and_sparse_boundary_preserves_after_last_removed`、`publish_and_trim_unknown_writes_recover_from_durable_progress`、`trim_definite_later_failures_are_unknown_after_durable_progress` | 允许物理多留数据，仍按逻辑 Gap |
| 5.2-3 | restore/follow 固定 cut，读前后边界，失效 Gap/重restore | L2 `follow_rechecks_logical_boundary_after_native_read`、逻辑 gap 测试 | restore 最多 3次重试后 Unavailable，不返回缺页内容 |
| 5.2-4 | completion/retention 留存，禁止旁路trim/TTL/recreate，不冒称 StreamRecovery | L0 端口与持久 Document；L2 append/trim unknown 系列 | 不声称应用 API 能阻止管理员破坏数据 |
| 5.2-5 | 初始空snapshot可恢复，同cut完整fold，follow交付时查Gap | L2 `initial_empty_snapshot_and_append_restore_share_pure_projection`、`new_empty_task_is_restorable_after_other_task_history_is_trimmed`、follow boundary 用例 | 无已确认缺口 |
| 5.2-6 | scope 隔离、有界 list 分页和整体 deadline | L2 `sparse_task_list_pages_advance_scanned_members_and_bind_scope`、`restore_and_list_enforce_the_whole_read_deadline` | 无已确认缺口 |

## §6 结果归档与任务收尾

| 编号 | 原要求；生产实现 | 已验证 | 尚缺或限制 |
| --- | --- | --- | --- |
| 6-1 | 第一次 FinishTask 固定 result/snapshot/status/身份，AwaitingResult+SaveResult；`W/transitions.rs`、`lifecycle.rs` | L1 `finish_requires_archive_confirmation_and_a_separate_terminal_commit`；L2 `real_providers_archive_then_publish_terminal_before_starting_next_task`；L3 指定双 Worker 归档链 | 正常链已验证 |
| 6-2 | immutable archive confirmed 后第二次 commit 引用+terminal+effects | L2 同上及 `takeover_after_archive_replays_the_frozen_result_without_running_the_reducer`；L3 基础接管+SQLite L2 L-G3新故障链。 | 公共主链断点已补；archive后接管仍以既有专项测试为据，P-L3本次是正常归档/晚到证据不可变，层级不混用。 |
| 6-3 | terminal publication confirmed 后才释放/下一task init；`prepare.rs`、`lifecycle.rs::retire_published` | L2 正常两 task 顺序用例 L2 L-G3在terminalpublish NotApplied期间第二task仍Submitted、Runner仅一次，恢复后才第二次。 | 公共缺口已闭合。 |
| 6-4 | archive unavailable 保留可恢复 AwaitingResult，不提前 Completed；drive/replay/effects | L2 Archive provider 的 Unknown 测试；Worker failed-outbox parking 测试 L2 L-G3实际Worker archive unavailable→恢复，首次结果不可见且第二task无Runner调用。 | 公共缺口已闭合。 |
| 6-5 | SaveResult 丢回复按原 ID核完整内容/ref；同 ID改内容冲突；`S/archive/{mod,sql}.rs` | L2 `ambiguous_insert_resolves_exact_content_without_repeating_write`、`unresolved_write_is_unknown_and_retry_is_same_identity`、`concurrent_same_identity_has_one_immutable_winner`、`absent_row_after_unclassified_failure_is_not_definite_nonapplication` L2 L-G3实际SQLite成功后丢回复、完整source+record重试一致。 | 公共Worker故障链已补；不是OB实例验证或跨进程crash测试。 |
| 6-6 | archive后、第二commit前crash恢复同ref；隔离不改 frozen结果 | L2 `takeover_after_archive_replays_the_frozen_result_without_running_the_reducer`、`quarantine_and_takeover_after_archive_preserve_the_first_frozen_completed_result` | 旧 L3 主要是 checkpoint 成功丢回包；不是这个特定 archive→second commit cut 的证据 |
| 6-7 | 默认 DbPlugin；scope+tid唯一、Blob ID/digest；orphan留存；新增迁移 | L2 `blobs_verify_bytes_and_preserve_identity_after_provider_restart`、`archive_is_scope_and_namespace_isolated_and_rejects_dangling_refs`、`result_requires_persisted_exact_effect_and_replays_after_head_advances`；L3 PROXY+SQLite reopen Singlebox12个最终instrumented实际进程场景已通过（memoryStore+existingSQLite，见SB-L2）。 | OB实例defer；Singlebox本地changed-line门槛已通过；远端 `decdab6ea7` CI 已通过，后续 Review 增量需独立检查。公共archive单测不代表所有DB验收。 |

## §7.1 控制输入与远端监督

| 编号 | 原要求；生产实现 | 已验证 | 尚缺或限制 |
| --- | --- | --- | --- |
| 7.1-1 | HumanReply匹配 tid/当前interaction，稳定ID统一去重；`W/prepare.rs`、Inbox | L0 生产路径存在；Inbox 同身份/changed body 已有L2 L2 L-G1补当前interaction/错interaction/错queued tid、sameID Duplicate/changedbody Conflict、Continue一次、late consumed无reduce。 L3 P-L3正常HumanReply、Duplicate、唯一Continue及完整输出。 | 错tid/id、changedbody、lateHumanReply等按L2 L-G1记录，不声称本L3全覆盖。 |
| 7.1-2 | Cancel稳定输入，取消/完成竞争，迟到不能改归档 | L0 Cancel独立finalize、Awaiting/Done优先和late consume路径 L2 L-G1首Finish前Cancel独立Canceled；L-G3首Finish冻结后lateCancel保持Completed。 L3 P-L3独立Cancel实际Canceled且remote仍SendingUnknown。 | Cancel-vs-Finish两侧精确竞争仍L2；本L3是指定正常取消链。 |
| 7.1-3 | deadline身份登记固定，retry不重复；task timeout/step limit独立终结 | L2 `a_registered_deadline_occurrence_cannot_be_rearmed_by_returning_its_same_identity`；配置overflow拒绝 L2 L-G1真实task deadline/step limit触发Failed、归档、nexttask正常且ControlMode Normal。 | 公共正常终结已补；发生身份去重另由既有task_timeout pure规则和custom deadline专项证明，不混称同一场景。 |
| 7.1-4 | 未领取/未发送保留监督并补notify；`supervisor/visit.rs` | L2 `creation_is_notified_and_retains_the_remote_obligation`；Dispatch create repair | 旧PROXY未单列 Created长驻观察回合，不扩为真实远端链路交付 |
| 7.1-5 | Sending Unknown交Connector恢复，Worker不重发/伪造失败 | L2 `sending_unknown_is_not_reissued_or_failed_when_the_deadline_has_passed`；L0 Worker无AgentClient依赖 L2 L-G5实际due→durable reschedule，Sending与terminal_evidence仍未知。 L3 P-L3本地terminal后实际持久recheck，Sending/outcome/evidence保持Unknown，跨有界分页仍可发现。 | 当前L3不含Accepted+CancelUnknown及holderUnknown分支，这些由L2专项承担。 |
| 7.1-6 | Accepted/InputRequired按deadline/continue/cancel复查；CancelIntent不是Canceled | L2 `accepted_dispatch_with_unknown_cancel_keeps_remote_supervision` L2 L-G1 Continue链；L-G5 Accepted+CancelUnknown在localterminal后仍监督。 | 公共组合已补；不是Connector远端恢复算法实测。 |
| 7.1-7 | definite terminal稳定critical回流；holder Unknown退避不按Dead | L2 `terminal_return_replays_the_same_identity_after_unknown_admission`、`unknown_holder_is_neither_taken_over_nor_notified`、`missing_terminal_evidence_is_not_invented_and_old_revision_is_reloaded`；L3 definite Error回流 L2 L-G5确定late terminal后Worker实际consumed推进、Runner/archive不变。 L3 P-L3最后注入确定终态，Worker实际consumed推进且Runner/archive不变。 | holderUnknown仍依赖已有精确L2专项；不是实际Connector故障恢复。 |
| 7.1-8 | local terminal不删未知远端义务，旧revision不能盖新control，late return不再reduce | L2 `blocked_session_still_accepts_terminal_supervision_without_business_recovery`、`completed_task_keeps_its_archive_when_late_dispatch_evidence_arrives`、`reconstructing_the_supervisor_after_unknown_return_keeps_the_same_business_input`、`repeated_revision_conflicts_yield_the_slot_to_other_durable_dispatches` L2 L-G5确认Unknown时consumed不变、late确定证据消费后变化、Runner/archive不变、due最后删除。 L3 P-L3 late证据确实被Worker消费、Runner Start/reply/Start仅3次、两份归档不变。 | 陈旧revision与AcceptedCancelUnknown精确分支仍以L2为据。 |

## §7.2 隔离、收口、独立终结与恢复

| 编号 | 原要求；生产实现 | 已验证 | 尚缺或限制 |
| --- | --- | --- | --- |
| 7.2-1 | durable Normal/Quarantined/Blocked、reason/head/opID/progress；`S/coordination/control`、`W/control.rs` | L2 processor_control/session_control suites；L3 invalid Decision隔离与受权resume | 已实现；L3不是每个控制分支 |
| 7.2-2 | 风险前记录attempt，success/transient/deterministic，原attempt重试不重复计数；risk/replay/control attempts | L2 `takeover_of_a_pending_replay_after_transient_failure_keeps_the_episode_identity`、`unknown_replay_begin_superseded_by_isolation_uses_a_fresh_authorized_episode`、session_control attempt契约 | Unknown不得按次数毒化；旧 legacy attempt编码新边界已验证 |
| 7.2-3 | 首次确定非法即停重跑，可安全收尾Quarantined否则Blocked | L2 `first_invalid_decision_closes_admission_and_finishes_every_accepted_task_without_rerunning`、`lost_failure_reply_and_takeover_keep_the_same_isolation_and_do_not_rerun_runner`；L3 invalid Runner只一次 L2 L-G4实际Running dispatch取消链。 | 公共主链已补；不是每种隔离触发均新测。 |
| 7.2-4 | CorruptState/UnsupportedFormat/unsafe InvalidWorkflow进Blocked，不造Failed | L2 `unsupported_runner_state_is_blocked_without_fabricating_task_failure`、`undecodable_checkpoint_is_blocked_by_successor_without_replacing_business_body`、`changed_business_body_at_the_same_head_is_blocked_without_overwriting_evidence` | 各enum共享分类分支，但未逐enum单列实测；不得声称所有变体都L3 |
| 7.2-5 | 正常quota只当前task失败；transient/Unknown不累计确定失败 | L2 quota、not-applied/unknown replay、risk episode系列 L2 L-G4 cancel链已补。 L2 L-Q补确认内容后quota失败且nexthealthy正常。 | quota公共覆盖缺口已闭合；错误分类其他分支保持原证据。 |
| 7.2-6 | takeover_streak只是计数；失败绑定head/input/context/attempt；crash pending不是确定失败 | L2 `historical_success_after_a_racing_isolation_never_authorizes_the_ordinary_candidate`、`historical_begin_reply_after_isolation_does_not_call_runner`、`historical_replay_begin_reply_after_blocking_does_not_call_effect_ports` | L3 Runner一次只覆盖特定提交不确定性，不替代全部分类 |
| 7.2-7 | Quarantined Running→Failed/Queued→Rejected，无坏Runner；Blocked保留证据 | L2 first invalid及queued独立终结；L3 accepted queued tasks拒绝归档 L2 L-G4 Running task独立取消、两dispatch分批重放与接管。 | 公共Running/dispatch链已补；既有invalid Decision+queued收口证据组合使用。 |
| 7.2-8 | Awaiting保留第一frozen result/status/source；Done只补effects，冲突Blocked | L2 `quarantine_and_takeover_after_archive_preserve_the_first_frozen_completed_result`、archive takeover | 新capacity proof也覆盖frozen64KiB snapshot+result；旧L3未特定注入归档后隔离 |
| 7.2-9 | isolation exactowner/head只控制写，不盖业务/outbox；badbody仍可记原因；Supervisor独立 | L2 `isolation_fences_an_already_prepared_ordinary_candidate_and_closes_it_independently`、badcheckpoint与blocked supervision L2 L-G4 ContentConflict→Blocked无归档假终态；L-G5 localterminal后Unknown。 | 新两组不是同一Blocked+Unknown完整场景，既有blocked Supervisor专项承担独立进展；证据不混称。 |
| 7.2-10 | 先暂停业务，再gate close固定代次/slots，再捕获覆盖preclose append的cut；Unknown不完成 | L2 `closure_includes_registered_creator_paused_before_task_exists`、`every_recovery_write_can_lose_its_response_without_refreshing_authorization`、admission_closure/control_faults | L3正常close/resume已测，不等价每个竞态断点都实测 |
| 7.2-11 | accepted历史保留、未accepted永久拒绝，未checkpoint任务也收尾，有界分页 | L2 `closure_rechecks_tasks_added_by_independent_finalization_after_scan_started`、firstinvalid closure；L3未消费accepted批次归档 | 已实现；不把已规范旧代次重绑新代次 |
| 7.2-12 | terminal published才删slot/确认项；create-once防迟到Submitted复活 | L2 `closure_cannot_complete_before_actual_archive_and_terminal_publication_then_reopens_once`、`creator_paused_before_initial_task_cannot_resurrect_submitted_after_closure_and_resume` | 旧L3正常close不是paused producer竞态证明 |
| 7.2-13 | 固定cut涵盖released accepted/unregistered canonical；certificate核覆盖与未决，坏项不跳过 | L2 `unauthorized_close_and_fabricated_completion_are_rejected`、`corrupt_progress_cursor_cannot_manufacture_a_completion_certificate`、closure各扫描测试 | 损坏cursor/迟到登记等精确组合为L2 |
| 7.2-14 | 关键Agent/既有control保留budget、激活、消费，不坏Runner/新Task | L2 critical event closedgate、blocked supervisor；L3 small control受理与lateError分别有 | 新pressure gate只新Start且七用例通过；完全存储故障不能承诺控制仍可立即完成 |
| 7.2-15 | 每个task-owned未结束dispatch稳定RequestCancel，先checkpoint/outbox再端口，必要意图确认后local terminal、不等remote | L1 `nested_task_ids_do_not_steal_another_tasks_dispatch_obligations`；L2 Dispatch独立cancel契约；源码`lifecycle.rs`先cancel再SaveResult L2 L-G4 Board在每个初始端口调用前核已持久source/effect/digest/完整intent；首条applied-lostreply，第二未登记/无归档；接管后两原intent保留。 | 公共实际终结器链已补；新replacement使用真实provider proof核对，旧wrapper记录不能冒称全部接管后的端口调用也被同wrapper观察。 |
| 7.2-16 | 大集合有界收尾/进度；metadata不能撑爆；capacity+lifecycle | L1 `independent_field_maxima_fit_ordinary_and_reserved_finalization_frames`及新增生产startup proof | 当前合法task≤64dispatch，最多64cancel+SaveResult有界容纳，不要求泛化>64分页；若提高cap须新增分页/证明 |
| 7.2-17 | cancel部分完成/重放/失权/同ID冲突；local terminal后Unknown继续，Blocked不造授权 | L2 provider幂等/冲突与effect cursor分项；L0 effect proof/fence L2 L-G4 partialreplay/takeover/ContentConflict→Blocked；L-G5 localterminal后Sending或Accepted+CancelUnknown。 | 公共主链已补。ContentConflict由wrapper注入证明Worker分类，真实同IDchangedbody由既有Dispatch契约证明；Blocked不新增授权由既有risk/control测试承担。 |
| 7.2-18 | resume显式授权绑定owner/head/isolation/cert/request；gate新代次+control双确认；新隔离废旧resume | L2 `delayed_ordinary_commit_cannot_cross_an_isolation_recovery_cycle_at_the_same_head`、`recovering_owner_change_requires_fresh_authorization_and_then_can_resume`、`old_successful_recovery_is_not_reported_as_open_after_new_isolation`、`upgrading_isolation_preserves_the_previous_close_cut_and_failure_evidence`；L3 wrongactor拒绝、同IDresume、新代次成功 | 无自动修复/批量控制台，不清除旧证据。L3未遍历每个resume写断点，L2已有fault矩阵 |

## §8 协调依赖

| 编号 | 原要求；生产实现 | 已验证 | 尚缺或限制 |
| --- | --- | --- | --- |
| 8-1 | #4126注册/StrictLease/shard claim/session fence；公共coordination+宿主strictlease适配 | L1/L2协调harness；L3 StrictLease conformance（续约、EQ竞争、retirement、过期不复活、持久tombstone） | JSON版本比较单独不足证明StrictLease；须有服务端时间、原子过期判断、不可复活。HA时钟/持久性是用户确认平台前提，本轮未节点切换 |
| 8-2 | CAS version与业务commit_seq分开，封印/控制共版本但不推进业务序号 | L2 coordination/session_control竞争测试 | 不以epoch乘常量构造版本；实际三方每种交错不全部L3 |
| 8-3 | cached正常checkpoint不固定预读，冲突/unknown读取另计 | L2 `cached_commit_uses_one_write_and_missing_known_checkpoint_never_recreates`；L3成功写丢回复仍Runner一次  L3 P-L3正常/冲突/取消命令分类已有实际记录。 | 仅该场景观测；cached单写的精确成本断言仍由原L2测试，不能从总命令数反推每次checkpoint成本。 |
| 8-4 | 主动交接等待完整轮次（acquire/commit/effects/settle），receiver先新fence | L2 `drain_hands_off_quiescent_session_and_retires_source`、`busy_or_unknown_work_is_not_reported_drained`、`lost_handoff_reply_is_reconciled_without_a_second_epoch`、`unavailable_scheduling_fence_keeps_acquire_outstanding` | L3旧记录是过期takeover，不是主动drain每个断点。不得相互冒称 |
| 8-5 | 只有无历史凭据才create；已知checkpoint缺失异常；证据不TTL/delete/rollback/evict | L2 cached commit/known missing、coordination cancellation tests；L3 persistentlease | 一次absent不能检测所有误删；没有额外初始化标记要求。平台持久前提单独记录 |

## §8.1 资源底线

| 编号 | 原要求；生产实现 | 已验证 | 尚缺或限制 |
| --- | --- | --- | --- |
| 8.1-1 | session串行；processor/effects/IO/watch/cache/batch/scan显式限额和启动交叉验证 | `W/engine.rs`、runtime、Inbox/Dispatch/Supervisor configs；L2配置拒绝、acquisition与resource tests | 实际宿主池/value配置须匹配；默认值不代表实际组合能力 |
| 8.1-2 | count+bytes、编码膨胀、超大input先拒绝 | L1/L2最大shape、`inadmissible_physical_profile_or_binding_never_creates_a_task`、Inbox/Dispatch双额度 | startup缺口现已补；物理Key/segment峰值不是encoded logical或chargedbytes，容量实测本轮排除 |
| 8.1-3 | 连接/阻塞读有界，跨slot不丢流，不支持跨区时单流轮转 | L2 reader/watch容量；宿主transport池/取消脚本测试；L3相应provider conformance | 新public pool事实API与raw映射有局部验证；不宣称真实持续耗尽容量验收 |
| 8.1-4 | slow session/effect/Blob/repair公平退避，不占满槽位 | L2 `a_hot_session_yields_before_draining_all_its_inputs`、not_applied/unknown replay、`canceled_replay_parking_finishes_cleanup_before_retrying_effects`、`isolation_racing_failed_replay_parking_closes_the_gate_and_yields`、slow_pending扫描 | 原“失败outbox永占slot”已由现有实现修复，不能继续列为待开发；真实长期负载测试排除 |
| 8.1-5 | eviction不丢unknown candidate；unsafe保留有界slot，按confirmed consumed重建 | L2 `canceling_the_processing_future_keeps_its_unknown_commit_for_resolution`、`full_reader_capacity_rebuilds_without_losing_pending_input_or_unknown_commit`、`unknown_acquire_at_capacity_recovers_during_normal_ticks`、`cancelled_checkpoint_write_keeps_the_cache_reservation_through_definite_retry_error`；L3 dropped commit reply | 模型已覆盖细节；L3仅指定关键断点 |
| 8.1-6 | 无正文观测：repairlag、unknown、outbox age、archivewait、supervisionbacklog、isolation、restart、resource | L0 `ProcessorObservations`、`MaintenanceObservation`、Supervisor pending/reserved bytes；现有组合取样；新增transport局部测试  L3 P-L3实际transport/payload观测已取得，取样时in_flight4/peak8。 | 这是业务结束、cleanup前快照而非所有后台调用已静止的最终计数；resident gauges与durable backlog语义继续分开，无正文/凭据。 |

## §8.2 冲突、扫描、压力与容量

| 编号 | 原要求；生产实现 | 已验证 | 尚缺或限制 |
| --- | --- | --- | --- |
| 8.2-1 | 工作预算本地单调钟、业务deadline注入 | L0 EqBudget/scan/health/processor/supervisor；L2 stalehealth/fixedcut/clock tests | 持久wallclock年龄是诊断，不授权健康；lease server time另属平台前提 |
| 8.2-2 | EQ最多8写或500ms，IO只用剩余预算，timeout保留原条件身份 | L2 admission/closure/fault及5/10/80ms延迟用例  L3 P-L3取得实际命令/条件冲突/取消/延迟分桶。 | 该小规模功能记录不是人为超过8次/500ms的L3争用实验；准确上界用L2断点，无压力实验。 |
| 8.2-3 | 5ms→200ms指数，跨轮≤5s，回公平队列、不占slot/连接等 | 新L2 `retry_delay_cannot_exceed_the_spec_fairness_bound`；既有scan/supervisorfairness | **实现缺口已闭合**：Processor构造拒绝>5s与极端Duration，默认200ms不再是唯一保证 |
| 8.2-4 | 在途new Start≤64、≤64KiB；full先repair，durable intent后Unknown责任 | L2 full-slot、lost-write、`lost_first_append_credit_still_repairs_at_normal_capacity_boundary`、canonical race | 是逻辑严格预算，不是实际64条历史负载证明 |
| 8.2-5 | page≤128/1MiB，shard≤4pages或100ms，IO timeout/续扫cursor | L2 `scanning_large_intents_bounds_actual_log_read_bytes`、`corrupt_intent_validation_still_spends_the_scan_byte_budget`、`eighty_millisecond_reads_survive_sliced_discovery_and_cancelled_polls`、`eighty_millisecond_repair_resumes_instead_of_restarting_the_prefix` | deterministic边界已检；实际满页峰值不按配置推断 |
| 8.2-6 | 1000logs/s、200sessions/s、burst≤page，scanIO≤8且≤pool/4 | SharedScanBudget/token/permit；L2 budget profiles、`valid_single_io_profile_can_advance_maintenance` | 速率是配置上界，不是可支持吞吐；持续速率实测本轮排除 |
| 8.2-7 | repair/discovery/dispatch各≥1/4，余量共享；shard公平、Unknown不挡页、restart全轮 | L2 permanentgap/fixedcut、slowpending、dispatch restartscan | 不把单hotshard两session旧PROXY当多shard持续公平认证 |
| 8.2-8 | 健康声明容量下发现目标60s；50%告警、100%新Start反压 | L2 `stopped_scanners_backpressure_without_a_wall_clock_or_paused_flag_update`、`completed_fixed_round_publishes_health_while_new_intents_keep_arriving`、cold health tests | warning在Start健康检查触发，不承诺无请求后台主动告警；DEV耗时不认证60s/生产profile |
| 8.2-9 | backlog/historyitems/bytes/S/hotspot/maxphysicalkey有部署上限 | root严格预留、Inbox/Dispatch双配额、encodedmaxdoc；L2 `concurrent_reservation_cannot_spend_control_capacity` | 物理存储bytes/最大Key实测未认证；**本轮容量负载排除**，不是允许无测量生产启用 |
| 8.2-10 | 新intent前并发预留，Unknown/full/stale/sustainedpool反压；保留既有repair/results/critical/control | 新L2 `sustained_pressure_refuses_before_intent_but_keeps_control_and_existing_identity`、`an_unknown_durable_reservation_is_repaired_while_new_starts_are_blocked`、`pressure_does_not_refuse_a_same_identity_pending_reservation_retry`、`pressure_refusal_rechecks_a_racing_canonical_winner` | **压力实现缺口已闭合**；pressure在pending身份恢复后、新charge前，仅新Start。没有全store gate阻断critical |
| 8.2-11 | 资源事实可验证；观测缺失不能默认为健康；continuity跨slot交接 | 新L2 `startup_requires_consistent_pool_facts_and_bounded_policy`、`unavailable_or_invalid_observation_fails_closed_only_for_new_starts`、`short_pressure_is_allowed_but_queued_slot_handoff_is_still_pressure`；宿主rawpool脚本验证 | observation不新增I/O，但整条admit仍需原有validation/canonical/root读取；不称“拒绝全程zeroIO”。实际共享ordinarypool接线是host义务 |
| 8.2-12 | 完全存储故障保留Unknown/backoff，不承诺60s | L2多写点fault/replayparking；L3成功写失回包 | 不向共享服务制造整体故障；小规模namespace/客户端故障注入不冒称节点failover |
| 8.2-13 | 每启用组合记录L/S/bytes/input/activationrate/hotspot/pool/fullround/maxdelay，近边界验证 | 旧PROXY保存功能profile与耗时；生产配置未宣称认证 | **按用户排除本轮本地容量/持续负载验收**。不补压测、不依据DEV调参、不索取环境；不把排除写成通过 |
| 8.2-14 | 实际provider往返/unknown确认/retry/EQconflict/latency分开；effects/ref/blob/archive/isolation另记读写/bytes | 新通用transport diagnostics与分类wrapper已实现、scripted transport验证；wrapper宿主适配编码边界独立通过（scripted transport，非L3） L2 L-G2完整Blob端口字节已按unique/faultretry/otheridempotent分开； L3 P-L3观测命令started5135/replies5004/conflicts48/cancelled127、请求尝试编码3541009B/已解码回复3442585B，43条回复无法计量，快照仍4在途。 | 仅指定回复/输出/取消/监督场景，不补造各隔离阶段的独立费用。RESP编码估算不是抓包；逻辑payload/SQLite端口字节不是物理SQL或wire。 |

## §8.3 兼容性与保留

| 编号 | 原要求；生产实现 | 已验证 | 尚缺或限制 |
| --- | --- | --- | --- |
| 8.3-1 | runtime opt-in，显式format，旧Show不默转AppendText | L0新模型/Processorports；L1projection/modeltests；L3显式测试fixture | Singlebox实际opt-in host的12个instrumented进程场景与本地changed-line门槛通过；远端 `decdab6ea7` CI 已通过，后续 Review 增量需独立检查；不得称生产默认已切换 |
| 8.3-2 | 独立namespace、不与旧GT混写 | KeyLayout新protocolprefix；L3每次owned namespace+exact-key清理 | 不做tenant-wide扫描/删除；本稿不公开私有namespace |
| 8.3-3 | 存量迁移另授权，停写核证，不承诺透明在线迁移 | L0未引入旧协议透明迁移 | 正确排除，非本期缺口；新增DB schema迁移与存量协议迁移不可混称 |
| 8.3-4 | owner/spec/checkpoint/admission/effect首批不自动TTL/evict/GC | L0protocol；L3persistent strictlease、lost disposablehint恢复 | lease逻辑expiry不等于删除tombstone；平台保留前提独立记录 |
| 8.3-5 | 披露历史增长/扫描成本/上限，不冒称治理完成 | L0 Inbox/Dispatch README与容量配置说明 | 保留治理/GC/孤儿清理属后续批次；未认证容量不能生产启用 |

## §9.1 评审切片与交付状态

| 编号 | 原要求 | 当前状态 | 尚缺 |
| --- | --- | --- | --- |
| 9.1-1A | 受理/修复/调度/reader/processor/committer/Document CAS，故障不丢逻辑输入、不重复推进 | 生产实现与大量L1/L2存在；L3Document conformance、两Worker指定unknown/takeover已通过 公共补丁和指定L3证据保持。 L3 P-L3新增指定功能运行与实际成本观测。 | 不能由此升级为全部受理故障cut均L3；完整CI仍未绿。 |
| 9.1-1B | effects/Dispatch/TaskFeed/projector/Result/Blob，重放收敛/完整恢复/归档再终态 | 生产实现与L1/L2、指定L3组合存在；大AppendText漏测已补 新增L-G2/L-G3/L-G4公共组合与完整Blob写量通过。 L3 P-L3补正常回复输出、70000B大Append、immutable archive与late不变。 | archive不可用/terminalpublish故障与snapshot race仍L2；OB defer。 |
| 9.1-1C | 控制/timeout/supervision/isolation/coordination/resources，多Worker故障与交接 | 生产实现、L2与指定L3隔离恢复存在；startupcapacity/pressure补丁通过独立审查 新增L-G1..L-G5公共模型组合12测试通过、独立SPEC与质量审查通过。 公共生命周期+quota13项通过；L3 P-L3正常控制与SendingUnknown监督已通过。 | Singlebox最终12项与本地changed-line门槛通过；远端 `decdab6ea7` CI 已通过，后续 Review 增量需独立检查，不宣称第一批已生产启用。 |
| 9.1-边界 | 三切片同一批次；测试Runner显式装配，不生产fallback；真实群Runner/Gateway/Connector第二批 | 边界保持；Singlebox按用户允许memoryStore+SQLite，只作为实际opt-in测试host | 实际群业务/完整远端链路不是本期缺口；切片不能独立称可上线 |

## §9.2 必须测试矩阵的证据与差距

下表是验收索引，不替代上文的逐项断言。G1–G5新增公共组合已通过，统一以文末L-G1至L-G5定位新测试；它们都是L2，不能升级为每一对应PROXY场景都通过。

| 编号/原类别 | 已实现与已有验证 | 本轮剩余及边界 |
| --- | --- | --- |
| 9.2-01 受理各写点故障/同ID竞争/无callerrepair | L2 `every_lost_write_repairs_without_a_caller_retry`、`concurrent_cross_shard_identity_has_one_winner_and_no_losing_task`、`writes_happen_in_the_fixed_order` | 既有L2精确断点是有效证据，不能转写每点真实PROXY已过 |
| 9.2-02 受理×隔离交错 | L2 inbox_gates/admission_closure：accepted lostconfirmation、appendedunactivated、pausedproducer等；L3选定隔离批次 | 不把正常L3隔离推导全部交错。未发现独立新增实现缺口 |
| 9.2-03 收口/恢复跨owner、oldcanonical/logonly/latecreate | L2 oldcanonical/logonly/cut/cert、`creator_paused_before_initial_task_cannot_resurrect_submitted_after_closure_and_resume`；L3受权resume | L3不全量复刻L2矩阵；原discussion仍打开 |
| 9.2-04 修复公平/长期gap/restart | L2 permanentgap/fixedcut/slowpending；L3小规模losthintrestart | 持续历史/长期负载的本地压力部分排除；不影响确定性语义测试 |
| 9.2-05 调度token、新activation、hintlost、未来deadline | L2 forged/staletoken、discovery、lifecycle；L3losthint+接管 | 未来deadline/主动handoff真实断点未单列，不能冒称 |
| 9.2-06 调度争用与真实往返/conflict/tail | L2 EqBudget/慢IO/竞争断点；L3 P-L3实际命令/冲突/取消/延迟分类。 | 不是高负载调度争用认证；超过8次/500ms公平重排精确证明仍为L2，本地压力排除。 |
| 9.2-07 去重/旧holder/中间seq/unknowncommit | L2 criticalclosedgate、duplicateconsumed、unknowncandidate；L3丢成功checkpoint回复Runner一次 | 核心指定L3成立，不扩为所有组合 |
| 9.2-08 commit/seal/control/handoff协调竞争 | L2coordination/sessioncontrol/lifecycle drain、schedulingfence；L3leaseexpiry takeover | Active drain L3不是expiry takeover同义。平台HA确认与真实节点测试分开 |
| 9.2-09 effects半批/旧owner/冲突/append丢receipt | L2TaskFeed/Dispatch/Archive fault、Worker replay，新增L-G4 committedcancel半批/接管；L3 P-L3正常reply/cancel。 | 全部半批、旧owner、changedbody分支未逐一重复L3，不冒称。 |
| 9.2-10 输出恢复/Thinking/空snapshot/trimGap | L1projection；L2TaskFeed固定cut/Gap/ref、大Append与L-G2慢publish/concurrenttrim完整restore；L3 P-L3完整70000B输出restore。 | P-L3不是全部Gap/trim变体；层级保持。 |
| 9.2-11 输出预算/Ref/Blobunknown/编码极值/下一task | L1serializedproof/reference；L2startup、1MiB Upsert/AppendText；L-Q确认内容后quota整批拒绝、usage11、cancel、nexthealthy/init2/calls3/Normal；L3 P-L3 70000B Append。 | 原精确quota漏测已闭合；1MiB/全部metadata极值仍为L1/L2。 |
| 9.2-12 snapshot cadence/fullBlob累计量 | L2 L-G2三阈值、quiettakeover、slowpublish/trim、完整Blob故障retry与其他重复分型；L3 P-L3另有snapshot/blob累计端口字节。 | P-L3没有重复全部三阈值/慢publish故障，不把13项模型测试整体升级。 |
| 9.2-13 两阶段archive各断点/terminalpublishfail | L2既有archive后takeover/frozenresult，新增L-G3 Worker archive不可用/成功丢reply/terminalpublish失败阻挡nexttask。 | 公共主链已补；L3 P-L3是正常immutablearchive+late不变，不是同一故障矩阵；OB defer。 |
| 9.2-14 HumanReply/CancelFinish/timeout/Unknown监督 | L2 L-G1/L-G5 HumanReply/CancelFinish/tasktimeout/step/Unknown监督；L3 P-L3正常reply/Cancel/SendingUnknown实际recheck与late消费。 | holderUnknown、AcceptedCancelUnknown、timeout/step精确分支仍L2。 |
| 9.2-15 首次非法停、Blocked、transient、冻结archive、坏outbox、受权resume | L2processor_control/risk/controlfaults；L3firstinvalid+resume | 冻结result等以L2为据；不称每个errorenum均L3 |
| 9.2-16 隔离cancel先commit、partialreplay/fencing/conflict、remoteUnknown | L2 L-G4/L-G5实际effectproof、部分重放/接管、ContentConflict→Blocked、localterminalUnknown；L3 P-L3独立Cancel与SendingUnknown。 | 真实同IDchangedbody/Blocked禁授权另查已有契约；wrapper冲突注入不是实际provider争用。 |
| 9.2-17 JSON provider | L1公共documentcontracts；L3 `document_cas_proxy_dev`独立create/EQ/null/patch/metadata/耗尽 | 已有指定真实conformance证据；不能把Lease大版本证据借给DocumentCAS |
| 9.2-18 boundedresources/hotfair/cache/pool/large/reader/fault | L1/L2resource/retainedscan/parking/新pressure7用例；宿主rawpoolscript；L3指定unknown/takeover | 真正持续池耗尽/长期故障峰值没有容量认证；按用户不本地压力，保留小规模定向故障 |
| 9.2-19 历史容量/近上限持续新history/restart/告警反压/最大delay | L2固定cut、双配额、健康/pressure语义；旧DEV记录只诊断 | **用户明确排除本轮本地压力/容量验收。未完成容量认证，也不继续请求负载环境/根据DEV调参** |
| 9.2-20 ≥2Workers+确定性Runner+真实持久provider组合 | 原L3双Worker/losthint/commitlostreply/Runner一次/SQLite reopen；新增P-L3第三轮控制输出监督通过；Singlebox12个instrumented进程故事补充（SB-L2）。 | P-L3不是13项模型故障全量远端重跑；SingleboxmemoryStore不替代持久PROXY；OB defer。 |

## §9.3 完成判定

| 编号 | 原要求 | 本稿判定 |
| --- | --- | --- |
| 9.3-1 | 公共确定性、真实providerconformance、多Worker组合三层分开 | 已按L1/L2/L3逐条记录；Review PASS与测试通过也分开，不用静态审查制造执行证据 |
| 9.3-2 | StrictLease/create/CAS/failover分别证据 | Lease与Document create/CAS各有真实conformance；HA是用户确认平台前提，本轮未做节点failover，不互相推导 |
| 9.3-3 | provider版本/命令/配置/限制在宿主记录 | 已有源码/客户端配置/命令语义记录；新transport统计局部验证。服务端build/version若平台不公开，应记unknown/平台提供，不推测版本，不在公共稿复制私有endpoint |
| 9.3-4 | 未通过的provider只报告公共完成；failclosed不是组合完成 | Capacity/pool声明缺失拒启是安全行为，不等于实际组合验收；公共G1–G5/quota与指定新PROXY控制/输出计量已补，发布候选 `decdab6ea7` 的完整 CI 已通过且六条原评论已逐条回复；后续 Review 修复仍按实际提交验收，不宣称已部署 |
| 9.3-5 | 不承诺长期容量/固定恢复时延/特定吞吐 | DEV耗时只诊断；容量压力本轮排除，生产profile仍未认证；不从计量功能推出生产SLO |
| 9.3-6 | 原PR仅proposed文档，文档/实现/验证/部署分别报告 | 本稿明确原881提案基线与后续实现事实；未部署、未生产启用。原评论六条均已回复，本任务不改变讨论 resolved 状态 |

## G1–G5 公共补证与剩余宿主交付

| 组 | 可核验的完成条件 | 当前快照 |
| --- | --- | --- |
| G1 正常业务控制与任务边界 | 可保持Running/InputRequired的确定性Runner；User→interaction→HumanReply；错tid/id只消费、同ID同内容一次、changedbody冲突、Continue不重复、late不改archive；Cancel在Finish首提交前后；真实tasktimeout/stepquota→独立Failed归档→下一task正常；补已确认输出后quota保留 | L2 L-G1/L-Q主链与quota已通过；L3 P-L3正常reply/dup/大Append/Cancel通过，错误tid/id和竞态分支不升级为L3 |
| G2 发布节奏/恢复/完整Blob计量 | 默认128event/64KiB/1s边界及冻结策略；status/interaction立即发，Thinking不计；N−1仅append/N publish；quiettail重建/owner变化/慢publish/丢回复/concurrenttrim同cut完整；分类累计实际读写及唯一/重试Blob量 | L2 L-G2已通过，含真正Unknown确认失败→后续put与分类字节；L3 P-L3另有snapshot/blob累计量，未重跑三阈值/慢publish全部故障 |
| G3 两阶段归档故障 | SQLite既有DB即可；SaveResult不可用/成功丢回复时Awaiting且第二task无Runner；terminalpublish失败/Unknown也等；恢复/接管同record/ref、terminal稳定、再init下一task | L2 L-G3已通过；L3 P-L3验证正常归档和late不变，不宣称archive unavailable/terminalpublish故障已远端重测。OB继续defer |
| G4 独立终结取消链 | Runner创建≥2dispatch，含SendingUnknown/已有terminal；终结器request_cancel首次调用可核已提交checkpoint/effectproof；部分生效丢回复后接管，同effect/requestID无重复；必要意图确认再localterminal；changedbody冲突Blocked且不跳过、Blocked无新授权 | L2 L-G4实际Worker主链已通过；真实changedbody契约、Blocked禁授权与已终态dispatch核证依赖既有专项，不冒称新用例全部独立覆盖 |
| G5 远端Unknown实际监督 | localterminal后至少一轮due→reschedule仍Unknown，没伪造terminal/criticalfailure，持久due保留；holderUnknown不接管；随后definiteTerminal criticalreturn让Worker实际消费，Runner次数/archive不变；旧revision不盖新cancel | L2 L-G5已通过；L3 P-L3实际SendingUnknown持久回合/late消费通过；AcceptedCancelUnknown、holderUnknown与stale revision仍用已有L2专项 |
| 观测实跑 | 新PROXY transport总命令/RESP请求响应bytes/latency/conflict/cancel；场景分段；snapshot/event/Blob/SQLite逻辑端口读写分开，说明 attempted/confirmed、重复重试及唯一对象口径；这些 payload bytes 不能冒称 SQL wire bytes | 已实现+scripted局部验证；第三轮P-L3完整通过并取得本指定场景分类记录。不是压力测试，不推导生产容量/性能 |
| Singlebox与CI | 实际opt-in host接公共Worker，显式memory Store mock + existingSQLiteArchive；相关功能/CI完成，保持默认不启用 | 12个instrumented实际进程场景、独立SPEC/质量及本地changed-line76.091129%通过，远端 `decdab6ea7` CI 已通过，后续 Review 增量需独立检查；无新SQLiteStoreprovider，无持久Store重启证明 |
| 知识与Reviewer回复 | durable说明同步DocumentCas采用、≤1SetStatus、providercapacity与hostwrapper义务、legacyattempt字节上限、pressure事实/策略；原评论逐条回复变化+SPEC章节+证据层级 | 六条原评论已逐条同步；本任务未改变讨论 resolved 状态；后续 Review 修订另行记录 |

## 已完成补丁的可复核运行记录

- 启动容量/封装/retry/AppendText选定回归：**83 passed，0 failed**。运行 `cargo +1.94.1 test --manifest-path apps/bcs/Cargo.toml -p bcs-dist-store -p bcs-dist-worker --lib --test coordination_limits --test coordination --test session_control --test processor_capacity --test processor_large_append --test processor_execution --locked --offline -- --nocapture`。两实现crate严格all-target Clippy通过，独立SPEC与质量审查PASS。代表shape打印 ordinary checkpoint740279B/outbox195790B，Running独立finalization checkpoint537379B/outbox56210B；这些是代表样本，**不是所有phase上界的穷尽统计**，生产checker另覆盖queued/frozen形状。
- 压力准入：七个新场景RED为6失败/1已有通过，修改后7通过。随后相关 `bcs-dist-store`、`bcs-dist-worker`、`bcs-store-api` 全target运行 **49 suites / 381 passed / 0 failed**；共享harness `document_subset`、`lease_contract_faults`、`conformance_store` **3 suites / 44 passed / 0 failed**。上述选定实现crate严格all-target Clippy与独立SPEC/质量审查通过。
- **扩大检查的失败没有省略**：整个 `bcs-test-support` 加入严格all-target Clippy时，未改fixture既有 `unwrap/expect` lint报错（lib684/libtest693，存在重叠）。未改gate/allow清除它；因此不能说全workspace Clippy或所有CI绿。它与上述44项harness实际运行通过不同。
- 新宿主通用transport diagnostics与普通池连续拥塞/取消/阻塞池独立映射完成脚本测试；新增宿主适配最大版本Document wrapper编码边界通过（scripted transport，无DEV请求）。**第三轮P-L3完整通过**，64个owned keys已清理；只把明确覆盖的正常控制/输出/监督与观测记作L3，不升级全部模型场景。
- 旧真实PROXY指定组合通过：两个Worker、丢hint、成功checkpoint回复丢失、接管、Runner一次、SQLite重开归档不变、首次非法隔离/授权恢复、latecritical。旧记录耗时与store方法调用数仅作历史诊断；不能转为wirequeries、生产恢复目标或容量认证。
- 本汇总未执行新增测试、SDK调用、迁移、部署、Git/CI变更，也未触发本地压力测试。新增公共生命周期12项、quota1项、质量修正race重跑1项及Singlebox12场景由各自执行日志/父任务报告提供（13个不同模型测试，race重跑不重复计数）。后续远端CI/最终交付完成时，应只更新对应行和最终运行集，不把新证据追溯归给原审计HEAD。

## 新增生命周期公共证据索引（L2）

四个target并集12 passed/0 failed，追加quota第五target1/1通过，共13个不同测试；质量修正后的snapshot race另行1/1通过。五个新target严格Clippy通过，依赖既有warning与完整workspace/CI状态分开。以下均使用公共Worker/StoreModel/SQLite，不是PROXY运行，也没有容量负载。

| 索引 | 精确测试函数与范围 |
| --- | --- |
| L-G1 | `W/tests/processor_lifecycle.rs::{matching_interaction_on_another_queued_task_is_consumed_without_reducing,human_reply_requires_current_interaction_and_replays_once_through_dispatch_and_archive,cancel_before_finish_is_independent_and_retires_only_after_dispatch_cancel_and_archive,task_timeout_and_step_limit_finalize_without_isolation_and_allow_next_task}`。错误/迟到回复先确认consumed推进，再断言无reduce/不改archive。tasktimeout/step是实际独立终结，不能代替quota场景。 |
| L-Q | `W/tests/processor_quota_lifecycle.rs::quota_failure_preserves_confirmed_output_rejects_entire_decision_and_initializes_next_task`。先确认完整display与11B usage，再合法新增artifact+超限Append，整批均未出现；Failed+cancel，nexthealthy完整8字符restore、init2/calls3/ControlNormal。1/1通过，独立SPEC/质量PASS。 |
| L-G2 | `W/tests/processor_snapshots.rs::{count_policy_publishes_at_128_committed_deltas_without_clock_progress,encoded_byte_policy_externalizes_large_append_and_publishes_before_count_or_time,quiet_tail_deadline_survives_takeover_and_trim_requires_published_coverage}`；`W/tests/processor_snapshot_race.rs::slow_publication_and_lost_replies_keep_trim_safe_and_count_full_snapshot_retry_bytes`。Notify证明publish确实挂起；实际Blob确认失败1次后同ID后续put独立计fault_retry，另计正常幂等重复。最终实际端口字节attempted198300/unique66100/fault_retry66100/other_idempotent66100，业务OutputUsage66010只计一次；不是SQL物理或wirebytes。 |
| L-G3 | `W/tests/processor_lifecycle.rs::frozen_finish_wins_late_cancel_and_archive_or_terminal_publish_failure_blocks_next_task`。真实SQLite写成功丢回包及终态publish NotApplied均保持完整source/record，第二task不提前调用Runner。原archive后接管由既有专项测试补充。 |
| L-G4 | `W/tests/processor_finalization.rs::{partial_committed_cancel_batch_survives_takeover_without_rerunning_or_changing_intents,conflicting_cancel_effect_blocks_instead_of_publishing_a_false_terminal}`。端口前核持久effectproof；两dispatch首条已登记丢回复，第二条尚未登记且无归档；接管后同intent完成。Conflict是wrapper注入，实际provider同IDchangedbody仍查旧契约。 |
| L-G5 | `W/tests/processor_finalization.rs::local_terminal_keeps_sending_or_cancel_unknown_supervised_until_definitive_late_evidence`。分别Sending/Accepted+CancelUnknown，实际持久reschedule且未伪造consumed；definite late terminal后重建Supervisor，Worker实际消费、Runner与archive不变、due消失。holderUnknown/stale revision另依赖已有专项。 |

质量审查曾发现“attempted−unique等于故障retry”证据错误：原put丢回复后的get成功可在同次确认，重复put可能只是draft/Publish共享内容。最终已加入首次同IDget也Unknown并显式标记下次put，以上新数字取修正后的执行记录；旧132200/66100/66100不能继续称为故障retry记录。

## 新增指定真实 PROXY 证据（P-L3）

`proxy_normal_reply_cancel_and_unknown_supervision` 第三轮完整1/1通过，清理64个owned keys。实际127.33s仅是本次执行时长，不用作生产延迟、恢复目标或容量参考。场景明确覆盖：正常HumanReply及重复去重、唯一ContinueDispatch、70000字符AppendText完整restore、第二任务独立Cancel、localterminal后真实SendingUnknown持久recheck、跨有界扫描页仍发现义务，最后确定late terminal由Worker实际消费，Runner只Start/reply/Start共3次、两份归档不变。没有将13项模型测试全部在PROXY重跑。

transport观测在业务结束、cleanup前取样：started5135/replies5004/conflicts48/cancelled127，另有4个在途，peak8；请求尝试RESP2编码3541009B、已解码回复估算3442585B，43条回复无法计量。started不是服务端确认收到的次数；取消后的写入结论仍由原协议解决；这不是抓包结果或全部后台工作结束后的最终计数。按命令的时延分桶、累计/最大值保存在宿主记录，仅作诊断。

| 逻辑端口类别 | read bytes | attempted write bytes | Applied write bytes |
| --- | ---: | ---: | ---: |
| snapshot document | 87822 | 8583 | 7630 |
| output event | 0 | 1649 | 1649 |
| payload Blob | 72055 | 70002 | 70002 |
| full snapshot Blob | 350480 | 280384 | 70096 |
| final record | 3641 | 1123 | 794 |

以上是本场景实际端口参数的累计计量，Applied与AlreadyApplied语义分开；不可把attempted−Applied全部称为故障retry或物理数据库重复写。event read=0是本场景记录事实（restore可直接用完整snapshot），不是声称事件读取通道不存在。SQL物理写入/网络字节未被这些payload计数证明。L2的专门故障场景另将fault_retry和正常幂等重复分开；两类证据不要混算。没有新增每个隔离/恢复阶段的独立费用分段，不从这次累计量推导全部场景成本。

## Singlebox 最终真实进程功能证据（SB-L2）

显式 `worker-runtime-e2e` feature启动独立 `bcs-worker-e2e` binary，外部Python JSON-lines驱动生产公共Worker/Store/TaskFeed/Dispatch/facade；Store为用户授权的memory mock，Result/Blob使用既有SQLite。最终instrumented报告12项全部passed，独立SPEC与质量审查通过；这是真实进程功能证据，仍非持久Store重启/平台failover/L3 PROXY。

| 已执行check | 确切证明 |
| --- | --- |
| `document_projection_atomic_patch_and_unknown` | create/read/paths/null-vs-missing/有序patch，非法多edit后全值不变，成功丢回复后原版本内容核对 |
| `finish_archive_restore_and_late_cancel`、`append_archive_restore_and_late_cancel` | normal与70000B大Append完整restore；lateCancel实际consumed推进，Runner/完整archive/restore不变 |
| `cancel_independent_of_runner` | hold task独立Canceled且Runner无新增调用 |
| `quarantine_and_control_authorization`、`blocked_and_control_authorization` | 首次非法/unsupported分别隔离、错误actor拒绝；精确授权恢复后新Task完成 |
| `memory_store_worker_takeover` | 停止旧Worker驱动并推进Model存储时间，新incarnation接管原task；不是进程重启持久性 |
| `graceful_handoff_preserves_pending_task` | application facade真实drain、持久handoff、receiver原task继续Cancel、owner等于target、Runner次数不增 |
| `human_reply_dispatch_input_once` | created→public claim/send/Accepted，HumanReply Duplicate只一次pending input且payload正确，interaction清空 |
| `holder_input_acceptance_and_cancel_unknown` | public resume_owned推进generation、input1被接受移出pending，localCanceled后Accepted+CancelUnknown真实持久recheck；late definite证据消费且archive/Runner不变 |
| `live_follow_snapshot_trim_gap_restore` | 两个33000B Agent delta被follow实际交付，完整snapshot Ref还原seed+a+b；published trim后旧cursor Gap/新cursor有效，restore全内容不变，wrongscope拒绝 |
| `sending_unknown_late_evidence_without_rerun` | localterminal后SendingUnknown持久revision/due保留，最终确定证据由Worker实际消费、不rerun；follow真实交付terminal事件 |

原host质量审查两P2已修：ready构造失败就地kill/wait/close child；profile清理前先失效旧passed报告。新增三个真实child异常和一个清理失败回归4/4通过。artifact required集合同步12项，默认装配、覆盖率阈值和排除项未改。

本地实际changed-line测量沿用仓库原 `changed_line_cov.measure`、原ignore配置和merge-base差异：9118/11983=76.0911290995577%，门槛65%未改。它不是完整远端Singlebox CI结果。host strictClippy曾被未改build_support/git_metadata.rs既有lint阻断；diagnostic新binary无warning，但仍不可称整个host strict gate通过。

最新独立回归集：公共六包70 suites/424 passed/0 failed/0 ignored；宿主adapter76 passed/0 failed/1 ignored（需要显式DEV配置的strict_lease_proxy_lost_responses未在该离线集执行，不冒称通过）；CI结构46 passed，新增child/cleanup文件最终4/4。各运行集有重叠，不能简单相加。新增PROXY指定场景以P-L3记录为据；旧真实Lease conformance以其原记录为据，不能用离线76项代替。

## 复审摘要

**已补的明确实现缺口**：生产启动最大合法候选/完整provider封装证明、拒绝不可能容量与缺失声明、≤1SetStatus、recovery历史编码限额、retry≤5s、大AppendText外置与恢复回归、持续共享普通池压力只反压新Start。

**交付状态**：发布候选 `decdab6ea7` 的完整远端 CI 已通过，原评论已逐条回复。后续 Review 修复的精确证据见文末，必须区分候选 SHA。Singlebox最终12项instrumented场景和本地changed-line门槛已通过。G1–G5公共模型主链、quota精确覆盖与指定真实PROXY正常控制/输出/监督计量已通过；其余精确故障仍按实际L1/L2层级报告，不声称全部L3。**OB实例defer，历史容量/持续负载本地验收按用户排除，HA按确认的平台前提记录**；后两者不能被写为本轮实测通过，也不应作为继续压测或请求环境的理由。


## 最终本地组合验证（发布前）

- 六个公共包合并回归：424 passed，70 suites，0 ignored；包括新增13项生命周期组合。此前83、381等分组计数与此重叠，不相加。
- PROXY适配器：76 passed、1 ignored；ignored真实资格测试不计入单元通过，指定真实PROXY生命周期另有P-L3记录。
- Singlebox最终12个instrumented进程场景通过：原7项，加真实HumanReply/held input、CancelUnknown、SendingUnknown晚到结果、livefollow/snapshot/trim/Gap和主动handoff，最后验证原Task不重跑Runner。内存Store仍不证明跨进程持久性。
- 采用仓库现有changed_line_cov实现及原排除规则，以Git merge-base为差异基线，局部companion的变更行覆盖9118/11983=76.09%，超过原65%门槛；没有降低门槛或新增排除。公共Worker/Store本地line覆盖分别74.13%/73.30%。这些不替代整套Singlebox总覆盖和GitHub CI。
- CI编排/产物/取消回归46项通过；此后增加的清理失败回归所在4项全部通过。真实子进程启动无响应/错误ready/非法JSON均由宿主回收；产物先标失败再清profile，清理失败不能保留旧passed。
- Host严格Clippy被同package既有build_support/git_metadata.rs collapsible_if阻断；不降gate。未改lint配置的诊断检查中新companion bin无warning，既有BCS lib警告另列。其他本次Worker/Store/adapter目标严格检查按前述记录。

发布状态：生产补齐、companion/CI/审计已在 PR #4245 发布为 `decdab6ea7`，该 SHA 的全部检查成功或 neutral。后续 Review 修复在本稿中另列；其最终结果以 PR 当前 head 检查为准。OCB gitlink发布须满足公共提交的GitHub/镜像及release可达要求，不能以本机编译代替集成。

## 已发布候选的远端 CI 与后续 Review 修订

`decdab6ea7c780d1957b5a306388b9cdc21cbcbc` 的 [BCS 单测与 MySQL 验证](https://github.com/inclusionAI/Avernet/actions/runs/44479260852)通过：9,196 passed、69 skipped；真实 MySQL 完整迁移链及后续所有 MySQL 契约步骤成功，line/changed-line 为89.49%/91.32%，原门槛不变。它补齐了早期只执行048 DDL、后续被旧版本断言阻断的证据，不能继续把完整链列为未执行。

同 SHA 的 [Singlebox](https://github.com/inclusionAI/Avernet/actions/runs/44479260997)通过：line61.33%、method54.47%、changed-line9139/11983=76.27%，183/183端点、67/67 CLI 命令及必需产物验证通过。原65%变更行门槛未降低。该远端结果与前述本地局部76.09%是不同测量，不相互替换。

PR #4245 后续 Review 指出的生命周期预算与旧 checkpoint 布局另作如下修订：

- **§8.2／§8.3 生命周期预算。** history entries/bytes、Session额度及Inbox额度都不因消费或完成而释放。可靠受理至少预留两单位，物理重试另计；4096并不等于4096个任务。正常额度耗尽先背压新Start，修复/控制预留也耗尽后，固定profile内修复可暂停；义务和Unknown状态不丢弃，不宣称无限故障仍能装入有限存储。
- **可恢复的协调扩容。** 去掉任意的4096历史条数上限，保留原有效4096 Session结构上限及文档硬预算；先停旧writer、验证更大profile，再以同namespace恢复所有实例和maintenance。不得清计数、删除证据或把未决输入换namespace重投。`tests/inbox_retention.rs` 用一个真实公共意图及直接设置的边界计数证明：满额时canonical不变，扩容后仅maintenance即可完成原意图，没有生成大量历史。
- **扫描结构上限。** 放大配置不能令持久 `Scan.pending` 超出单文档。启动按最大宽度空Scan元数据222B和每个u64成员最多21B推导上限：`222 + 21 × max_history_entries <= max_document_bytes`。默认960KiB最多46,800单位；8192可用，超过推导上限拒启。纯编码及构造边界检查不代表存储容量认证。
- **观测与算术。** `MaintenanceObservation.capacity` 给出实际计费单位和剩余history/normal/Session/Inbox预算，复用原有两次读取。新Start的已有健康检查在shard额度消耗约80%时发有频率限制的结构化告警，不增加存储往返。极端字节计数使用checked_add，溢出拒绝且不改权威状态。
- **§7.2／§8.3 格式识别。** 普通checkpoint读取先检查版本头，再解码body；未来版本和#4145原型无task归属的旧RunnerState布局明确返回UnsupportedFormat，不推断tid、不改写或seal。旧数字版本1的原型namespace不支持直接复用，已在SPEC与协调README中说明。23项coordination回归通过，新增用例核验读取/acquire失败后内容与版本不变；独立SPEC/质量复审通过。
- **指定PROXY功能证据。** 新 `proxy_retained_repair_and_checkpoint_format` 用生产PROXY、一个真实持久意图、pre-append故障和直接设置的预算计数，验证满额时保留Unknown、同namespace扩容后后台修复完成、旧/未来格式拒绝且无写入。1/1通过，清理11个owned keys。计数是边界fixture，不是实际写满存储；未做压力测试、SDK调用或生产容量认证。OB仍defer。

以上Review增量与已发布候选的CI区分记录；其发布后的最终检查以[实施PR当前head](https://github.com/inclusionAI/Avernet/pull/4245/checks)为准。本任务已在#4144六条原评论下逐条回复，未执行resolve操作；该SPEC PR合并后的读取显示六条均resolved，这是观察到的外部状态，不是本任务关闭讨论。

Review修复后的六包合并回归：71 suites／433 passed／0 failed／0 ignored；Inbox定向14项是其中子集，不相加。Store全target严格Clippy、两组补丁的独立SPEC及质量复审通过。加入扫描上限后，单条输入PROXY场景再次1/1通过，另一个新namespace的11个owned keys均清理；两次小场景不能合并解释为负载或容量认证。
