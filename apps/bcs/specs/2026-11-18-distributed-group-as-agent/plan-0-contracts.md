# Group A2A Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把本轮审阅修订稿（复审通过后）的 Group 输入、公开结果、独占受理、许可重调度和消费证明落实成可编译、可测试的增量 Rust 合同。

**Architecture:** 无传输类型的数据放 bcs-dist-model，application/core/repo 放 bcs-service-api。沿用现有 DistGatewayService，不新建第二套任务服务；共享测试框架必须调用 provider/消费者，而不是播放成功脚本。

**Tech Stack:** Rust、serde、async-trait、现有 bcs-dist-model / bcs-service-api / bcs-test-support。

---

执行基线、依赖和提交流程见 [总计划](plan.md)。本文件修改全局共享合同，按 D01→D02→D03→D04→D06→D05 执行；D06 新增合同先于 D05 最终 conformance 门槛。D0 通过后，C01 交付共享 validator 实现，G01/R01 才可接入；独立 adapter/存储任务可先推进。D06 负责 Blob 生命周期、checkpoint 压缩和本期删除禁用边界。以下是接口设计需要落实的最小形状，具体字段导出及 serde 命名在任务内同步迁移所有构造者。

## D01：typed 请求、规范化依据和完整公开结果

**Files**

- Create：`apps/bcs/crates/contracts/bcs-dist-model/src/group_call/{mod,request,public_task,admission}.rs`。
- Modify：`apps/bcs/crates/contracts/bcs-dist-model/src/{lib,session,task}.rs`、`apps/bcs/crates/contracts/bcs-dist-model/CONTEXT.md`。
- Modify：`apps/bcs/crates/contracts/bcs-dist-model/Cargo.toml`、`apps/bcs/Cargo.lock`、`apps/bcs/scripts/ci/check-public-api.sh`；Create：`apps/bcs/scripts/ci/tests/public_api_version_regression.sh`、本目录 `api-propagation.md`（实施时记录模型/API 的消费者及迁移）。
- Test：`apps/bcs/crates/contracts/bcs-dist-model/tests/group_call_shapes.rs`。

- [ ] **1. 建立模型测试入口，写序列化/规范化依据测试。** 测试 raw 请求保留“省略”和“显式默认”区别，basis 保存默认值后再比较；Artifact 支持 text、object/array/scalar JSON，不把最终结果限制成输入对象子集。两个 Part 分别带 filename/metadata，Artifact 顶层另带不同 metadata，序列化重载后各自保留；改变任一 Part 属性须改变完整语义指纹。

```rust
#[test]
fn basis_round_trips_without_current_card() {
    use bcs_dist_model::group_call::NormalizationBasis;
    let basis = NormalizationBasis {
        format_version: 1,
        return_immediately: false,
        history_length: 20,
        response_budget_version: 1,
        max_history_length: 100,
        max_response_bytes: 8 * 1024 * 1024,
        accepted_output_modes: vec!["text/plain".into()],
        include_progress: false,
        workflow_digest: bcs_dist_model::Digest("sha256:frozen".into()),
    };
    let saved = serde_json::to_vec(&basis).unwrap();
    let restored: NormalizationBasis = serde_json::from_slice(&saved).unwrap();
    assert_eq!(basis, restored);
}
```

- [ ] **2. RED：** `cargo test -p bcs-dist-model --test group_call_shapes`；缺类型的编译失败仅作合同开发反馈，不算行为 RED；类型落地后增加不支持 format 和 Part 多分支的行为负例，确认断言失败再实现校验。
- [ ] **3. 定义请求/结果结构，保持业务类型与 wire 分离。** `GroupCallRequest` 保存 RequestId、客户端 sid/tid、保序 Parts、全部 configuration、metadata、激活扩展；额外未知 configuration 字段必须明确保留或按协议拒绝，不能静默丢掉后仍声称“完整指纹”。`GroupCallInput` 保存选定模式、原始语义输入、basis 与指纹。`PublicTaskResult` 保存有序 Artifact 和选定媒体模式；完整 PublicArchiveRecord 另包含公开历史与 Worker 固定的提交边界。

```rust
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct NormalizationBasis {
    pub format_version: u32,
    pub return_immediately: bool,
    pub history_length: u32,
    pub response_budget_version: u32,
    pub max_history_length: u32,
    pub max_response_bytes: u64,
    pub accepted_output_modes: Vec<String>,
    pub include_progress: bool,
    pub workflow_digest: crate::Digest,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct PublicPart {
    pub content: PublicPartContent,
    pub media_type: Option<String>,
    pub filename: Option<String>,
    pub metadata: Option<std::collections::BTreeMap<String, serde_json::Value>>,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum PublicPartContent {
    Text { text: String },
    Data { data: serde_json::Value },
    ObjectRef { reference: crate::BlobRef },
}
```

输入 Part 约束由业务校验实现，PublicPartContent::ObjectRef 只是存储表达且必须有 media_type，本期 adapter 拒绝公开未支持文件。Part 的 filename/metadata 与 Message/Artifact 属性独立；raw 请求保留原字段存在性，规范化不能因 typed 转换丢掉嵌套属性。公开属性在源头按发布策略校验，不自动公开输入 metadata，也不能静默删掉已允许的属性。`PublicArtifact` 明确 artifact_id、name/description、parts、position、metadata/extensions。`PublicTaskView` 包含 scope/group/sid/tid、状态时间、公开 history/artifacts、覆盖位置；不得依赖临时远端查询补最终结果。新增版本化 `GroupRoundInput`（scope/group/sid/tid/request_id/round_generation/execution_id/workflow_digest/initial_goal/ordered_parts/previous_success/selected_output_mode/include_progress/execution_deadline）和 `PublicTaskResult`（result_version、固定 artifact IDs/positions/parts、selected output mode、公开结果内容）。`RunnerInput::Start` 和 `ActionIntent::FinishTask` 必须使用这些 typed 类型，不能把上述字段重新塞进无 schema 的 Payload。

精确结构在 D01 编译落地；RoundRef 放模型层，D02 只消费，不先引用尚未定义的类型：

```rust
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct RoundRef {
    pub scope: crate::TaskScope,
    pub group: crate::GroupId,
    pub sid: crate::Sid,
    pub tid: crate::Tid,
    pub generation: u64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct RoundMessage {
    pub parts: Vec<PublicPart>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct PreviousSuccess {
    pub tid: crate::Tid,
    pub result_ref: crate::BlobRef,
    pub result_digest: crate::Digest,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct GroupRoundInput {
    pub format_version: u32,
    pub round: RoundRef,
    pub request_id: crate::RequestId,
    pub execution_id: String,
    pub workflow: crate::WorkflowRef,
    pub initial_goal: RoundMessage,
    pub current: RoundMessage,
    pub previous_success: Option<PreviousSuccess>,
    pub selected_output_mode: String,
    pub include_progress: bool,
    pub execution_deadline: crate::TimestampMillis,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct PublicTaskResult {
    pub result_version: u32,
    pub round: RoundRef,
    pub selected_output_mode: String,
    pub artifacts: Vec<PublicArtifact>,
}
```

`ResolvedGroupRoundInput` 另含原 `GroupRoundInput` 与校验后的 `previous_success: Option<PublicTaskResult>`；`EvaluationContext.resolved_round` 引用该 typed 值，Worker 只按 `PreviousSuccess` 中固定 ref/digest 解析并校验、不得改写语义。Start.input 与 context 中 round 身份必须完全匹配。RoundMessage.parts 的 data 对输入仅允许至多一个对象，其保序位置由 parts 保持；前轮结果通过固定引用在进入 reduce 前读出并校验，不修改原 GroupRoundInput。PublicArtifact 在本任务定义完整 IDs/position/name/description/parts/metadata/extensions，不能由 Worker 补生成。公共事件覆盖边界是 D04 ArchivePlan 字段，不是 Runner 计算的物理位置。新增测试断言 typed Start 序列化/重放原绝对期限不变、Finish 多 Artifact JSON 类型/ID/顺序不变，未知输入版本明确失败。

PreparedRequest 另保存 `PreparationContextBasis { observed_generation: u64, initial_goal: BlobRef, previous_success: Option<PreviousSuccess> }` 和 `PreparationRevision(pub String)`（derive Debug/Clone/PartialEq/Eq/Serialize/Deserialize、serde transparent）；revision 由 repo 分配且永久不复用，BlobRef 自带 digest。该依据不进入客户端请求指纹。仅 Preparing 且未 claim 时可条件刷新背景及候选 round generation；claim 将匹配该依据的 GroupRoundInput 固定。刷新不改原请求/basis、稳定 ID 或绝对 deadline，已有 pin 只能在刷新提交已确认、旧引用不再需要后依证明解除。

PreparedRequest 再保存 `ContextOrigin::{GeneratedForRequest, ClientSuppliedExisting}`；来源从原 raw contextId 的存在性与服务端身份分配流程派生，不接受客户端直接填写。GeneratedForRequest 必须能证明保存的 Sid 属于该 RequestKey；ClientSuppliedExisting 在预留前证明同 scope/group/source 的 canonical Session 已存在。恢复时沿用来源，不把显式 Sid 的缺行变为新建指令。

```rust
pub const MAX_HISTORY_LENGTH: u32 = 100;
pub const MAX_PUBLIC_RESPONSE_BYTES: u64 = 8 * 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ResponseReadBudget {
    pub format_version: u32,
    pub history_length: u32,
    pub max_response_bytes: u64,
}
```

预算 v1 只接受 history_length≤100、max_response_bytes=8,388,608，默认 history_length=20；预算是服务端解析结果，不能信任任意 Rust 构造值。NormalizationBasis 保存上限及默认值，PreparedRequest/GroupCallInput 保存合并后的有效值；不是把 101 静默裁剪为 100。模型测试覆盖默认/0/100/101、负数/溢出 wire 转换、未知预算版本和相同请求重试的保存值。字节预算涵盖整个公开响应，不是逐 Task 配额。

本次模型变更明确按 breaking 发布：D01 将 bcs-dist-model 0.1.0 升为 0.2.0；新增必需 group/source 等字段的既有 SessionSpec/TaskSnapshot/FinalTaskRecord 持久格式升 v2，旧 v1 明确 UnsupportedFormat，不默填归属。D01～D06 同一发布切片沿用 0.2.0，不以“尚未部署”免除外部构造者、枚举匹配、path/version 约束与锁文件迁移。`api-propagation.md` 分开列模型 0.2.0 和 Service API 7.0.0 的改动、消费者、实现者及持久化拒读/迁移策略。

API gate 的 crate/path 映射加入 `bcs-dist-model -> crates/contracts/bcs-dist-model`，保留现有三个 Service API crate。0.x breaking 必须提高 minor（或进入更高 major），0.1.0→0.1.1 不接受；稳定版本 breaking 仍要求 major。`public_api_version_regression.sh` 用隔离 fixture 验证模型保持 0.1.0/仅升 patch 失败、升 0.2.0 通过、漏扫模型失败、Service API 6.x breaking 只有 7.0.0 通过；缺 crate/工具失败不得伪装为空 diff 成功。门槛增强不修改已有豁免或跳过其他 crate。

- [ ] **4. GREEN：** 同目标测试通过且测试数量非零；再运行 `cargo test -p bcs-dist-model`、`bash scripts/ci/tests/public_api_version_regression.sh` 和 `cargo check --workspace --all-targets`。核对模型 0.2.0/Cargo.lock/全部构造者，CTX 标明持久 v2/旧 v1 拒读；不凭 serde 成功代表合法业务。真实 public API diff 在 D05 最终 gate 对整个提交切片验证。
- [ ] **5. 审查并提交：** `git diff --check`，仅 stage 本任务文件；提交标题 `feat(bcs): define group call input and public result models`。

## D02：GroupInvocationRepo、共享能力校验与公开 Gateway 群范围

**Files**

- Create：`apps/bcs/crates/service-api/bcs-service-api/src/port/repo/distributed/group_invocation.rs`、`apps/bcs/crates/service-api/bcs-service-api/src/application/dist_group_card.rs`。
- Create：`apps/bcs/crates/service-api/bcs-service-api/src/core/group_workflow_policy.rs`；C01 创建共享实现 crate。
- Modify：`apps/bcs/crates/service-api/bcs-service-api/Cargo.toml`、`apps/bcs/Cargo.lock`、D01 创建的本目录 `api-propagation.md`（补 Service API 的 consumers/implementations/migration）。
- Modify：`apps/bcs/crates/service-api/bcs-service-api/src/application/{dist_gateway,mod}.rs`、`apps/bcs/crates/service-api/bcs-service-api/src/core/dist_gateway.rs`、`apps/bcs/crates/service-api/bcs-service-api/src/port/{dist_agent.rs,repo/distributed/mod.rs}`。
- Modify：`apps/bcs/crates/contracts/bcs-dist-model/src/group_call/admission.rs`、`apps/bcs/crates/service-api/bcs-service-api/src/port/repo/distributed/{result,task_feed}.rs`。
- Test：`apps/bcs/crates/service-api/bcs-service-api/tests/group_call_contracts.rs`。

- [ ] **1. 写编译/序列化用例，检查 GroupId 必须经过 get/list/subscribe/observe/cancel。** 同时断言预留键不包含 sid，配置和 basis 可从已保存记录恢复；submit 返回已保存的有效配置，查询参数穿透至 repo，内部观察与公开订阅的终态行为可分别表达。

```rust
#[test]
fn request_key_does_not_include_client_context() {
    use bcs_dist_model::{GroupId, RequestId, TaskScope};
    use bcs_dist_model::group_call::RequestKey;
    let key = RequestKey {
        scope: TaskScope {
            tenant: bcs_dist_model::TenantId("tenant".into()),
            principal: bcs_dist_model::PrincipalId("caller".into()),
        },
        group: GroupId("group".into()),
        message_id: RequestId("message".into()),
    };
    let body = serde_json::to_value(key).unwrap();
    assert!(body.get("sid").is_none());
    assert_eq!(body["group"], "group");
}
```

- [ ] **2. RED：** `cargo test -p bcs-service-api --test group_call_contracts`。
- [ ] **3. 落地 repo 的所有状态和结果，而非通用 bool。** 定义 RequestKey、D01 的 RoundRef（scope/group/sid/tid/generation）、PreparedRequest、RequestRecord、RoundAdmission、DispatchObligation、StartConsumptionProof、RoundReleaseProof。Preparing/SlotHeld/InboxDurable/Accepted/Abandoned 状态保留在记录，Accepted 与 pending Start、Abandoned 与 pending Drop 各自同一原子写；activate/abandon 互斥。统一拒绝路径持久撤销精确准备 revision 后才返回 Busy，恢复器不得重新受理已拒绝准备。

```rust
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct RoundGeneration(pub u64);

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct RequestKey {
    pub scope: crate::TaskScope,
    pub group: crate::GroupId,
    pub message_id: crate::RequestId,
}
```

repo 精确方法：`lookup_request`、`reserve_request`、`read_context_basis`、`refresh_preparation`、`claim_round`、`record_inbox_durable`、`activate_round`、`read_round_admission`、`scan_recovery`、`confirm_start_consumed`、`abandon_round`、`confirm_start_absent`、`confirm_start_discarded`、`release_round`、`quarantine_round`、`confirm_stopped`、`reject_unclaimed`、`lookup_cancel`、`reserve_cancel`。每个写入校验适用的 preparation revision/request/round identity 和 expected generation；返回操作专用枚举。lookup 保存 winner 的完整 basis，不在 repo 内按当前 Card 重算。

`read_context_basis(scope, group, sid)` 返回同一控制 cut 的 PreparationContextBasis；底层真实缺失可返回 None，但 application 对 ClientSuppliedExisting 的 None 必须返回 ContextNotFound，与跨 scope/group/source 同一对外错误。读失败不得当成 None。仅 GeneratedForRequest 且原 raw 省略 contextId、持久预留证明 Sid 由服务端为同请求分配时允许初始化；显式给出“首轮 Sid”也不能进入新建分支。canonical SessionSpec 与 context 控制记录均须核对，后续 claim 再验证来源模式，缺行不能 fallback 创建。context 控制记录保存 initial_goal 和最近已释放成功轮的 PreviousSuccess；成功 release 与更新该指针/清空占用同一原子操作，失败/取消不覆盖最近成功。准备或刷新前保护所引用背景的 durable pin，确认刷新成功后才按证明释放废弃准备的 pin，不允许先写引用再补 pin。

冻结 `ClaimRoundOutcome::{Acquired, AlreadyOwned, Busy, Unavailable, Lost}`：Acquired/AlreadyOwned 均携带精确原 RequestRecord/RoundRef，前者新取得，后者是同 request/preparation 身份已经 claim 过的幂等恢复。provider 先解析该身份是否已 claim，再判断陈旧 expected generation；同请求响应丢失不能误报 Busy。原轮已 Accepted/Released 时 AlreadyOwned 仍返回原记录并交恢复分支，不新占用、不重写 Input、不把旧身份当当前 owner。SlotHeld 仅为记录状态，禁止另建同名结果变体；StoreError::OutcomeUnknown 与上述确定结果分开。

`refresh_preparation` 输入 RequestKey、expected preparation revision、新 PreparationContextBasis 及派生 GroupRoundInput；provider 原子要求 Preparing/从未 claim、context 仍空闲且 generation/最近成功关联匹配。结果为 Refreshed（新 revision 和记录）、AlreadyClaimed（转恢复）、Busy、Lost 或 OutcomeUnknown；不能先更新 generation 再单独改背景。`claim_round` 使用准备 revision、basis 中 generation 和完整背景关联一起 CAS。Lost 重读发现其他请求占用，和直接 Busy 一样调用 reject_unclaimed；发现空闲但 generation/背景依据已变则重读并条件刷新，即使 release 仅更新成功指针而未改变 generation 数值也必须刷新。刷新/claim 共用至多 3 次预算。背景不可用也先撤销准备再返回确定拒绝；存储未知不伪装为未受理。

`reject_unclaimed` 返回 Rejected/AlreadyRejected、AlreadyClaimed、Lost、OutcomeUnknown。只有前两者允许返回 Busy，并证明已原子撤销该 revision、解除请求键对旧准备的绑定；AlreadyClaimed 恢复同请求，Lost 重读解析，不清理新 revision。provider 保留不可复用的准备 revision 及拒绝证据，后续同 messageId 可以新 revision 重试，旧扫描者不能因 key 被删除/重建获得新准备的权限。非关键正文清理未完时恢复目录只暴露清理动作，不再暴露 claim 动作；逻辑解绑未知仍返回 OutcomeUnknown。

`scan_recovery` 页包含 Preparing/SlotHeld/InboxDurable、Accepted 待 Start 调度项、Abandoned 待封口/Drop 项及仅需拒绝清理的项，携带 `next_attempt_at`。`confirm_start_consumed` 验证 SessionStore 的提交证明和本轮 Start 身份。D03 定义 StartClosureProof：ClosedAbsent 时 confirm_start_absent 验证封口及不存在证明，无需 SessionSpec/checkpoint；ClosedPresent 时 confirm_start_discarded 验证精确 input/digest 的 progress-only 提交覆盖封口的完整 drain 边界。无输入不能用普通空读、caller bool 或超时确认；确认后保留拒绝墓碑但退出主动扫描。

`release_round` 验证归档、公共发布及停止证据，不以时间戳充当证明。`abandon_round` 独立校验 expected generation/request revision、未许可及无 Start 消费/动作，在同一次写中撤销准入并发布精确 DiscardStartProof/封口义务，再释放旧占用；已 Accepted 返回 AlreadyAccepted。未 claim 的 Preparing 放弃记录其 preparation identity，不伪造已占用轮次；D03 的封口证明支持该分支。

扩展现有 DistGatewayService，所有任务调用携带显式群范围。`submit` 接收 D01 请求并返回下列 application 类型；`AdmissionReceipt` 继续只证明受理，不承载返回策略：

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EffectiveResponseConfig {
    pub return_immediately: bool,
    pub history_length: u32,
    pub max_response_bytes: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GroupSubmitResult {
    pub receipt: bcs_dist_model::AdmissionReceipt,
    pub response: EffectiveResponseConfig,
}
```

`response` 来自原请求显式字段与已保存 basis 合并后的有效值，不是 basis 中的缺省值本身。history_length 必须在 0～100 内，max_response_bytes 为保存的预算 v1 上限；首次、重复、并发预留落败和恢复受理都返回同一有效配置，adapter 不访问 repo，也不按当前配置补默认值。配置/预算版本不可恢复时返回依赖错误，不能随意选择等待行为或放大预算。业务输入中的选定输出模式/includeProgress 仍由 D01 持久保存，此返回类型不改变它们的执行语义。

查询和观察请求在 `application/dist_gateway.rs` 定义，core/repo 定义各自协议无关的已解析查询，不从 application 反向导入类型：

| application 方法/类型 | 必需字段与可省略字段 | 返回及约束 |
| --- | --- | --- |
| `get_task(caller, GetTaskQuery)` | group、tid；history_length: Option<u32> | PublicTaskView；默认 20，0 不返回历史，1～100 取最近 N 条，>100 拒绝；不删除存储历史 |
| `list_tasks(caller, ListTasks)` | group；context_id、status、page_size、page_token、history_length、status_timestamp_after、include_artifacts 均可省略 | TaskPage 返回 tasks、next、有效 page_size 和分页前 total_size；include_artifacts 默认 false；page_size 缺省 50、范围 1～100 |
| `subscribe(caller, SubscribeTask)` | group、tid、原有订阅模式 | 对外 SubscribeToTask；恢复首帧时已终态则 UnsupportedOperation，已建立的流允许推进至终态并关闭 |
| `observe_task(caller, ObserveTask)` | group、tid、history_length: u32 | 0～100 且使用原有效值；供 send/stream 内部使用的 TaskSubscription，首帧为一致 Task@H，已终态则发快照后关闭，否则从 H follow |
| `cancel(caller, CancelTask)` | group、tid、可选 reason；内部稳定 request_id | `Result<CancelTaskResult, GatewayError>`，不再返回 AdmissionReceipt |

`observe_task` 与公开 subscribe 共用 Core restore/follow、授权和 Accepted 检查，但终态入口策略分开；HTTP 不新增观察路由，也不接受客户端传入“允许终态订阅”的开关。观察建立期间任务完成必须返回完整终态快照或随后终态事件，不能报公开订阅错误。TaskSubscription 的 Snapshot/Events 同步迁移为 typed 公共视图/事件，不能仍要求 adapter 解释内部 OutputEvent。

ListTasks 的可选字段分别采用 Sid、TaskStatus、u32、PageToken、u32、TimestampMillis、bool 类型。application 将 history/page 默认值解析为明确值；repo filter 显式表达 group/scope/source/Accepted、context、status 和状态更新时间下界，先筛选再按 `(status_updated_at DESC, tid DESC)` 排序分页。游标绑定主体、群及筛选条件；时间筛选使用状态更新时间，不能用 Artifact 写入时间。历史响应裁剪与 include_artifacts 作为读取投影传到 store；false 时不加载 Artifact 表/对象，true 时批量读取当前页，不能全量拉回再由 adapter 丢弃。新增参数及非法值转换、时间边界、同时间分页在 group_call_contracts 和 D05 suite 固定。

所有公开读取（包括 subscribe/cancel 的 Task）统一形成 D01 ResponseReadBudget，Core/repo 的 get/list/restore 及公开 history/Artifact 加载显式接收它；list 是整页共享 max_response_bytes，而非每 Task 各一份。持久大小/保守编码上界预检必须在加载大正文/对象之前；最终 wire 大小由 adapter 限长编码再次验证。预算不足返回 typed ResponseBudgetExceeded，不静默减 history_length 或拆掉产物；超过历史上限返回 HistoryLengthOutOfRange。新 send 校验在 reserve 前，已受理 send 的响应超限只终止传输并保留 ID；SSE 按帧限长，错误后关闭 reader、不取消任务。

```rust
#[derive(Debug, Clone, PartialEq)]
pub struct TaskPage {
    pub tasks: Vec<bcs_dist_model::group_call::PublicTaskView>,
    pub next: Option<bcs_dist_model::PageToken>,
    pub page_size: u32,
    pub total_size: u64,
}
```

CoreTaskPage 和 task_feed repo 的专用 TaskListPage 同步拥有上述四项，保持各层自有类型，不修改供其他扫描使用的通用 Page<T>。total_size 在 scope/group/source/Accepted 和全部业务筛选之后、分页之前统计，同 Tid 的 hot/archive 只计一次；单次结果的 page/count 使用同一逻辑索引 cut，下一页请求可因状态变化取得不同总量。page_size 是实际采用的上限而非 tasks.len()，空页/末页同样返回它。adapter 用 checked conversion 编码 SDK int32，越界明确错误，不把总量置零或截断。store 增加聚合查询及行数成本测试，不能为 COUNT 加载全部 Task/Artifact。

```rust
#[derive(Debug, Clone, PartialEq)]
pub struct CancelTaskResult {
    pub task: bcs_dist_model::group_call::PublicTaskView,
    pub cancel_request_id: bcs_dist_model::RequestId,
}
```

cancel 的内部 RequestId 由 reserve_cancel 分配，外部请求无需新字段。授权后先 lookup_cancel：存在则恢复原身份/固定内容并返回当前真实 Task，即使已终态也不拒绝或重建意图；尚未终态的部分投递按原身份补齐，终态则直接返回、不新增 Inbox 输入，已存在的迟到输入按关闭轮次规则消费。无意图且终态时重新核实并发 lookup 后才 TaskNotCancelable；无意图且非终态才 reserve_cancel，竞争落败复用 winner。lookup 失败不能视为 None。保留取消意图/去重证据至少覆盖 Task 可查询期；归档实际过期返回 ResultExpired，不伪造 Task。application 返回 CancelTaskResult，不返回 AdmissionReceipt；新增 typed Busy/ResultExpired/BackgroundUnavailable/ContextNotFound/CapabilityUnsupported/AdmissionAbandoned/AdmissionContentConflict/HistoryLengthOutOfRange/ResponseBudgetExceeded。Card application 合同只读；不支持工作流仍在 reserve/claim 前拒绝。

- [ ] **4. 同步迁移全部 Script trait 实现及结构体构造。** 用 `rg -n 'impl DistGateway|SubmitInput|AdmissionReceipt|TaskSubscription|ListTasks|TaskFilter|FinalTaskRecord' crates` 列出消费者，逐一更新，禁止 default 成功实现掩盖缺字段。新增 observe_task 的 recording 实现；ResultStore/TaskFeed filter 与读取投影同步迁移，scope 校验不能只在 adapter get 后临时判断。
- [ ] **5. GREEN：** `cargo check --workspace --all-targets` 、`cargo test -p bcs-service-api --test group_call_contracts`、`cargo test -p bcs-dist-store --all-targets` 及 `cargo test -p bcs --all-targets`；D02 同时将 `bcs-service-api/Cargo.toml` 从 6.1.0 升到 7.0.0，更新 `apps/bcs/Cargo.lock` 和所有 trait/模型构造者，记录 propagation analysis；提交 `feat(bcs): add scoped group invocation contracts`。

## D03：许可调度、逐输入屏障与 Start 提交证明

**Files**

- Modify：`apps/bcs/crates/service-api/bcs-service-api/src/port/repo/distributed/{inbox,session,group_invocation}.rs`。
- Modify：`apps/bcs/crates/contracts/bcs-dist-model/src/group_call/admission.rs`。
- Test：`apps/bcs/crates/service-api/bcs-service-api/tests/group_start_proofs.rs`。
- Modify：`apps/bcs/crates/test-support/bcs-test-support/tests/distributed_harness/{ports,inbox,state}.rs`。

- [ ] **1. 测试确认 RequestId、许可确认与 CommitReceipt 不可互换；请求发过 wake 不能清掉义务。** 增加错误轮次/错误 group/无 Start 的 proof 被拒用例，写入 D05 的参数化 contract suite。
- [ ] **2. RED：** `cargo test -p bcs-service-api --test group_start_proofs`。
- [ ] **3. 为 InboxSchedulingPort 增加 `reschedule`。** 输入是已验证 round + request identity + due 时间；存储内原子增加调度版本并提前 due，每次成功操作使既有 token 失效，即使 due 未改变。请求重放不能清掉未确认义务。

```text
reschedule(round, request):
  validate immutable request/round admission-or-discard proof and origin
  accept older Abandoned round Drop obligation without changing the current round slot
  atomic: schedule.version += 1; schedule.due = min(old_due, now)
  return durable reschedule receipt
settle(observed_old_token):
  if owner changed: Fenced
  if version changed: NeedsRecheck
  otherwise apply next_check
```

- [ ] **4. 消费边界必须足以提交合法前缀。** 当前 SessionBatch 只有整批 read_through；为每条 InputEnvelope 提供 backend position 或由 provider 返回可验证 prefix handle。定义 `ConsumedInput`/`StartConsumptionProof` 关联 sid/tid/request/generation；checkpoint 中保存 Start 接受证据，和 consumed/outbox 原子提交。不得对 opaque InboxPos 做排序；provider 解释边界。若 batch 第 k 条未许可，只允许确认 k 之前的前缀。
- [ ] **5. 为 InboxProducerPort 增加 `seal_abandoned_start`，补无输入结束合同。** 输入为精确准备/轮次/input/digest 的持久 Abandoned 证明，使用下列模型形状。不存在已占用轮次时必须验证精确准备已撤销且从未 claim。封口与该 Start 的实际物理 append 互斥，位置证据由 provider 维护且有界读取；只做 append 前普通查库不满足合同。扩展 `AdmitOutcome::StartClosed { proof: StartClosureProof }`，封口后的旧 Group Start 返回该 typed 拒绝，不再写入/激活，非 Group admission 行为不变。

```rust
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct GroupStartIdentity {
    pub request: RequestKey,
    pub preparation_revision: PreparationRevision,
    pub sid: crate::Sid,
    pub tid: crate::Tid,
    pub claimed_round: Option<RoundRef>,
    pub input_id: crate::InputId,
    pub business_digest: crate::Digest,
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum StartClosureProof {
    ClosedAbsent { identity: GroupStartIdentity, evidence: String },
    ClosedPresent {
        identity: GroupStartIdentity,
        drain_through: crate::InboxPos,
        evidence: String,
    },
}
```

这些类型放 model/group_call/admission；evidence 由 provider 生成并对持久封口事实校验，不因 caller 构造同名字段就授予确认权。AdmitRequest 新增 `group_start: Option<GroupStartIdentity>`；非 Group Start 为 None，Group Start 必须为 Some 且 claimed_round 非空，并匹配 InputEnvelope 和持久 claim 事实，不能通过省略该字段绕过封口。封口未 claim 的准备时 claimed_round 才允许 None。admit 的 Duplicate 分支也检查封口，不允许旧重试重新激活；现有构造者、producer 及其 Script/真实 conformance 同步迁移。

```text
Abandoned.PendingSeal -> seal_abandoned_start(original identity)
  ClosedAbsent -> confirm_start_absent(closure proof) -> TombstoneOnly
  ClosedPresent -> reschedule original Drop -> consume sealed prefix without Runner
                   -> confirm_start_discarded(closure proof, commit proof) -> TombstoneOnly
  OutcomeUnknown -> retain PendingSeal; retry/resolve with bounded backoff
```

ClosedPresent 覆盖同身份所有已追加副本的有限 drain 边界；输入已被丢弃时可以用保留的提交证据确认。ClosedAbsent 不要求构造 spec/task/Start/checkpoint。封口可在没有 Session 的情况下持久保存，重复调用返回同一封口结果，迟到交付不等于允许封口后新 append。两种确认都保留拒绝墓碑；无可执行 pending 的墓碑不进主动恢复索引。为“无任何输入”“append 先/封口先”“多个副本”“封口/确认响应丢失”建立 producer + invocation + consumer 联合合同测试，并扩展已有 `inbox_producer_port_contract_tests`；基础/生产 provider 无法提供写栅栏时不得开启该路径。

- [ ] **6. GREEN 与迁移：** 跑 group_start_proofs、`cargo test -p bcs-test-support --test distributed_harness`，确保共享分区连续确认原合同未退化；提交 `feat(bcs): define reliable group start scheduling and proofs`。

## D04：冻结能力、结果/完成状态机与版本登记

**Files**

- Modify：`apps/bcs/crates/service-api/bcs-service-api/src/{core/dist_runner.rs,port/dist_agent.rs,port/repo/distributed/result.rs,port/repo/distributed/session.rs}`。
- Modify：`apps/bcs/crates/contracts/bcs-dist-model/src/{group_call/public_task.rs,group_call/admission.rs,session.rs,task.rs}`。
- Test：`apps/bcs/crates/service-api/bcs-service-api/tests/group_finalization_shapes.rs`。
- Modify：`apps/bcs/crates/service-api/bcs-service-api/{Cargo.toml,CONTEXT.md}`、`apps/bcs/crates/service-api/bcs-service-api/src/port/repo/distributed/README.md`。
- Create：`apps/bcs/docs/adr/0001-distributed-group-call-identity-and-publication.md`；Modify：`apps/bcs/docs/adr/README.md`（执行前确认编号未被占用）。

- [ ] **1. 测试 completion decision 一旦冻结，不能被迟到 cancel 改成其他终态；同 result identity 不同内容是 Conflict。** 确认公开 archive 具备 group、版本、Artifact 顺序、public boundary、source commit、稳定 result digest。
- [ ] **2. RED：** `cargo test -p bcs-service-api --test group_finalization_shapes`，并核对 Start→重载→timeout 的绝对期限和 Finish→archive 的 Artifact 字段保持一致。
- [ ] **3. 定义 typed 完成阶段与归档证明，并显式迁移 `RunnerInput::Start { input: GroupRoundInput }`、`ActionIntent::FinishTask { tid, status, result: PublicTaskResult }`；不可仅扩展 EffectKind 留旧 Payload。** 状态为 Running、Closing、AwaitingArchive、Publishing、Released/Quarantined；映射到已有 TaskProgress，不增加另一个独立终态裁判。扩展 EffectKind 为固定 PublicResult 保存、最终发布与 ReleaseRound 的可恢复动作；所有动作保留原 EffectId/commit/content digest。

```text
Closing(decision, boundary, result_digest)
  -> AwaitingArchive(fixed SaveResult effect)
  -> Publishing(confirmed result_ref)
  -> release only with archive + public coverage + stopped proof
```

新增 `FrozenGroupCapabilities` 保存允许节点、最终节点、媒体类型、来源和公开策略，其值只能由 C01 的共享纯 validator 计算并带 workflow digest/compiler version。Card contract 只读取能力；上下文冻结依赖 exact workflow revision/digest。

Judge 作为可选扩展：定义 typed `EvaluateJudge` / `JudgeResult` 和 `JudgeAttemptId`，使用 Task/Node/attempt 及固定输入摘要；若本轮不交付 R04，能力集禁止 Judge。绝不通过向 payload 塞未定义 kind 绕过枚举。

- [ ] **4. GREEN：** `cargo test -p bcs-dist-model -p bcs-service-api`。将 D04 的模型/枚举/trait/构造者变更纳入同一发布切片的 bcs-dist-model 0.2.0、Service API 7.0.0 传播记录；消费者一起重编译，旧持久 v1 明确拒读，不默填归属。将分布式 canonical Sid、Group 请求许可与结果发布协议写入 app ADR，注明兼容路径、代价和采用状态；D0 评审前保留 proposed，不假报 accepted。冻结 `GroupWorkflowValidatorCoreService::validate` 的只读输入和 `ValidatedGroupWorkflow` 输出；C01 负责真实实现，G01/R01 无各自能力白名单。提交 `feat(bcs): freeze group result and capability contracts`。

## D06：Blob 生命周期、checkpoint 压缩与删除边界

**Files**

- Modify：`apps/bcs/crates/service-api/bcs-service-api/src/port/repo/distributed/result.rs`，新增 BlobLifecycleRepoPort、BlobStorePort::delete_immutable 和 get_bounded；生命周期/删除/有界读取的合同 owner 均为 D06，不是 D04。
- Create：`apps/bcs/crates/service-api/bcs-service-api/tests/group_lifecycle_contracts.rs`、`apps/bcs/crates/test-support/bcs-test-support/src/contract/distributed/group_call/lifecycle.rs`。
- Modify：`apps/bcs/crates/contracts/bcs-dist-model/src/group_call/{public_task,admission}.rs`，定义 manifest/pin/reference generation 和 checkpoint compaction proof。
- Modify：`apps/bcs/crates/service-api/bcs-service-api/src/port/repo/distributed/session.rs`，定义 closed round watermark、late event rejection 和 compaction proof。

- [ ] **1. 写故障测试：** put 后归档前崩溃可发现 pending manifest；归档失败保留 pin；引用移除后 GC 才能 claim；delete 响应丢失按 generation 重试；同 Sid 500 轮后 checkpoint 只保留 active/背景/closed watermark，旧 event/effect 被拒绝。
- [ ] **2. RED：** `cargo test -p bcs-service-api --test group_lifecycle_contracts`。
- [ ] **3. 冻结生命周期规则。** manifest 在 put 前写入，pin/引用与 archive 同事务；GC 只能 CAS 精确 generation/digest 且无 live/pending 引用；checkpoint 压缩保留初始目标、成功背景引用、fencing 墓碑和 closed generation，旧 round 的 late Start/Agent/effect 不能复活。受管分布式 context 删除首期禁止，旧入口在副作用前返回 `DISTRIBUTED_CONTEXT_DELETE_UNSUPPORTED`。

BlobStorePort 增加 `get_bounded(reference: &BlobRef, max_bytes: u64) -> Result<BlobReadOutcome, StoreError>`；`BlobReadOutcome` 为 `Found(Vec<u8>) / NotFound / TooLarge`，由 result.rs 定义。实现从 IO 开始限长（可多读至多 1 byte 判定超限），不得先调用无界 get 后检查 Vec 长度。预算先扣已选 history/Artifact 内容，未知对象大小仍按剩余预算读取；TooLarge 转 ResponseBudgetExceeded。测试边界 B-1/B/B+1、错误大小元数据、中文/转义膨胀、多个对象共享预算；保留原 get 供非公开内部消费者，不让公开读路径绕过 get_bounded。若需要低层能力变更，由 D06 登记插件版本与实现传播。
- [ ] **4. GREEN：** 更新 R25 lifecycle harness、workspace all-targets 和 API 版本传播记录；提交 `feat(bcs): define group blob lifecycle and checkpoint compaction contracts`。



## D05：共享夹具与消费者级 conformance

**Files**

- Create：`apps/bcs/crates/test-support/bcs-test-support/src/contract/distributed/group_call/{mod,admission,scheduling,results,scope}.rs`。
- Create：`apps/bcs/crates/test-support/bcs-test-support/src/group_call/{mod,fixtures,faults,recording}.rs`。
- Modify：`apps/bcs/crates/test-support/bcs-test-support/src/{lib.rs,contract/distributed/mod.rs}`。
- Create：`apps/bcs/crates/test-support/bcs-test-support/tests/conformance_group_call.rs`。
- Modify：受影响的 `bcs-dist-store/tests/{admission,building_blocks,failures,common/mod}.rs`、`bcs-service-api/tests/distributed_contracts.rs` 和 `bcs-test-support/tests/distributed_harness/` 结构体构造器；每次模型改变同步编译。
- Modify：`apps/bcs/scripts/ci/check-conformance-entries.sh` 的合同映射（如本脚本的数据表在其他文件，更新实际数据表，不放宽发现规则）。

- [ ] **1. 先用错误实现自测 suite 确实捕获违规。** `accepted_without_obligation_is_rejected`、`wrong_round_start_proof_is_rejected`、`same_id_changed_archive_is_rejected`、`list_filter_after_paging_is_rejected` 必须能失败，不写空 entry。
- [ ] **2. RED：** `cargo test -p bcs-test-support --test conformance_group_call`。
- [ ] **3. 建立具体 fixture API。** `request_fixture(message_id)`、`frozen_basis_text()`、`workflow_fixture()`、`public_result_fixture(tid)` 返回 D01～D06 完整类型；固定 clock、scope/group、Sid `s0042.session`、Tid `s0042.session.round1` 和稳定节点。fixtures 不使用随机生成 ID，不读取环境。

```rust
pub fn json_text_request(message_id: &str) -> serde_json::Value {
    serde_json::json!({
        "message": {
            "messageId": message_id,
            "role": "ROLE_USER",
            "parts": [{"text": "compare suppliers"}]
        },
        "configuration": {"returnImmediately": true},
        "metadata": {}
    })
}
```

公共 fault wrapper 支持“第 n 次写入前失败/写入后丢响应”、命名 barrier、控制时间、记录实际 repo/调度调用和 SQL 数量。所有后续任务可引用这些夹具；不能通过预填 expected output 代替调用被测服务。

- [ ] **4. 每条 suite 接受真实 `Arc<dyn GroupInvocationRepoPort>`、SessionStore/Scheduler、InboxProducer、ResultStore 和查询服务，由 provider fixture 装配。** `group_invocation_repo_port_contract_tests` 验证 Lost→Busy 撤销后恢复不执行、generation/背景刷新及旧 revision fencing、并发 claim/abandon、ClosedAbsent 无 checkpoint 结束和原有效返回配置；`inbox_producer_port_contract_tests` 验证封口与 append 互斥及完整副本边界；`inbox_scheduling_port_contract_tests` 跑 settle 前后两种顺序。`result_store_port_contract_tests` 跑内容冲突、完整归档及 Part 属性；`dist_gateway_service_contract_tests` 混入另一群数据后测时间/context/status 过滤、排序分页、page_size/total_size 及 history/includeArtifacts 投影。为观察恢复设置命名 barrier，覆盖终态发生在建立观察前后；记录消费者确实调用对应 port。
- [ ] **4a. 增补本轮负例。** 显式未知/跨群/跨主体/旧来源 contextId 不 reserve/claim/写 Inbox；同请求二次 claim 返回 AlreadyOwned 且不增加 generation；取消意图受理后丢响应、终态后重试返回原 ID/真实 Task，无意图终态首次取消才拒绝。预算测试覆盖默认/0/100/101、100 个 Task × 100 条历史及 includeArtifacts=true；超过字节预算不先加载全部正文，不返回截断成功；Blob suite 调用真实 get_bounded 验证 IO 读量。
- [ ] **5. GREEN：** 文件名使用 `conformance_group_call.rs`，入口调用 `bcs_test_support::contract::distributed::group_call::*`；跑 conformance_group_call、distributed_harness、`bash scripts/ci/check-r25-conformance.sh`、`bash scripts/ci/check-public-api.sh` 和 workspace all-targets；提交 `test(bcs): add reusable group invocation contract fixtures`。D0 评审需核对本计划所有方法已有精确类型后，才能开始后续子计划。

### D0 最终 GREEN 与 R25 文件命名

D01 模型 0.2.0 和 D02 Service API 7.0.0 升级已在相应任务列出，不等到合并前补版本。D05/D06 最终 GREEN 同时运行以下命令；gate 依赖缺失/原有失败逐项记录，不放宽 gate 或新增豁免；D01 对 gate 的唯一增量是模型覆盖及正确的 0.x 版本检查：

```bash
cargo check --workspace --all-targets
cargo test -p bcs-dist-model -p bcs-service-api -p bcs-dist-store --all-targets
cargo test -p bcs --all-targets
cargo test -p bcs-test-support --test distributed_harness --test conformance_group_call
bash scripts/ci/check-r25-conformance.sh
bash scripts/ci/tests/public_api_version_regression.sh
bash scripts/ci/check-public-api.sh
```

`PR_BODY_FILE` 在执行期指向包含“影响的 consumer”的 propagation 文本（仓库外临时文件），并分别核对 bcs-dist-model 0.2.0、bcs-service-api 7.0.0、Cargo.lock 和持久格式 v2。检查真实 gate 输出确实包含模型，不接受只跑原三个 Service API crate；API 工具不可用不算通过。新增 trait 的精确命名：

| Trait | 集中 harness | 真实入口 |
| --- | --- | --- |
| GroupInvocationRepoPort | group_invocation_repo_port_contract_tests | bcs-dist-store/tests/conformance_group_invocation.rs |
| GroupWorkflowValidatorCoreService | group_workflow_validator_core_service_contract_tests | bcs-dist-workflow-policy/tests/conformance_group_workflow.rs |
| DistGatewayService | dist_gateway_service_contract_tests | bcs-app-dist-gateway/tests/conformance_dist_gateway.rs |
| DistRunnerCoreService | dist_runner_core_service_contract_tests | bcs-dist-runner-fsm/tests/conformance_dist_runner.rs |
| InboxSchedulingPort | inbox_scheduling_port_contract_tests | bcs-dist-store/tests/conformance_group_scheduling.rs |
| InboxProducerPort | inbox_producer_port_contract_tests | bcs-dist-store/tests/conformance_group_start_closure.rs |
| ResultStorePort | result_store_port_contract_tests | bcs-dist-store/tests/conformance_group_results.rs |
| BlobStorePort | blob_store_port_contract_tests | bcs-dist-blob-local/tests/conformance_blob_store.rs |
| BlobLifecycleRepoPort | blob_lifecycle_repo_port_contract_tests | bcs-dist-store/tests/conformance_blob_lifecycle.rs |

入口直接引用实现类型和完整 `bcs_test_support::contract::` 路径，不能只包装私有函数名骗过扫描；既有同名 harness 扩展原入口，不另定义同名不同语义。每个新增 Service/Core/Port 的实际 trait 名必须出现在映射且有真实失败断言。

## C01：共享纯工作流能力校验实现

**Files**

- Create：`apps/bcs/crates/services/bcs-dist-workflow-policy/{Cargo.toml,CONTEXT.md,src/lib.rs,src/validator.rs}`、`tests/conformance_group_workflow.rs`。
- Modify：`apps/bcs/Cargo.toml`，注册无 HTTP/DB 依赖的 crate；D02 已定义 `core/group_workflow_policy.rs` 合同。
- Test：`apps/bcs/crates/services/bcs-dist-workflow-policy/tests/validation.rs`。

- [ ] **1. RED：** 用同一组 HumanInput/Loop/Judge/环/缺最终节点/错误 binding 夹具测试 shared validator，拒绝结果为 typed reason，运行 `cargo test -p bcs-dist-workflow-policy --test validation`。
- [ ] **2. 实现 validate：** 纯检查定义+binding+部署能力，生成确定拓扑/前驱/最终节点、workflow digest、compiler version 和 FrozenGroupCapabilities；同输入结果完全相同，无 clock/IO/random。G01 Card/准入和 R01 compile 只消费该结果。
- [ ] **3. GREEN：** 跑 `cargo test -p bcs-dist-workflow-policy --test validation --test conformance_group_workflow` 及 R25。Gateway 接口测试验证不支持定义在 reserve/claim/Inbox 前拒绝，Runner 编译校验同 digest/version；提交 `feat(bcs): share pure workflow admission and runner validation`。
