# Gateway Group A2A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 BCS Gateway 完成群调用应用服务与 A2A Server adapter，并用独立协议/应用测试证明其符合本轮修订规范（复审通过后）。

**Architecture:** adapter 仅调 DistGatewayService/Card application；application 解析可信身份、规范化与编排 Core/port；Core 通过 GroupInvocation/TaskFeed/结果 repo 保持原子语义。HTTP/SSE 连接不持有业务执行权。

**Tech Stack:** Rust、Axum 0.8、Tokio、futures、serde、仓库锁定 A2A 模型；scope 来自已有认证链。

---

前置：[D01～D06](plan-0-contracts.md) 完成；G01/R01 共同依赖同一 shared workflow validator 实现。G01 还依赖 C01 的共享 validator，G02 依赖 S01 的内存 GroupInvocation provider，G03 依赖 S04 的内存结果与基础 TaskFeed；这些不等待生产存储；G04～G06 可针对记录 application 调用的替身测试。新 crate 的 Cargo.toml、CONTEXT.md 与根 workspace 注册在首次任务完成；不引入新的外部依赖版本。

本期 Gateway 的唯一实现任务为 G01～G06，接管旧计划 G1～G6 的重叠职责，映射见 [总计划 §2](plan.md#2-分拆与依赖)。Core 统一位于 `crates/services/bcs-dist-gateway/`，不并行创建旧计划的 `bcs-dist-kernel/src/gateway/`。基础 TaskFeed/Session/Inbox provider 仍由原 S2/S3 交付，不再依赖另一条旧 G2/G3 实现线。

## G01：按已保存依据规范化、群目录与能力读取

**Files**

- Create：`apps/bcs/crates/application/bcs-app-dist-gateway/{Cargo.toml,CONTEXT.md,src/lib.rs,src/normalize.rs,src/directory.rs,src/card.rs}`。
- Create：`apps/bcs/crates/services/bcs-dist-gateway/{Cargo.toml,CONTEXT.md,src/lib.rs,src/request_policy.rs}`。
- Create：`apps/bcs/crates/application/bcs-app-dist-gateway/tests/{normalization,directory,conformance_dist_gateway}.rs`，conformance 直接调用 `dist_gateway_service_contract_tests`。
- Modify：`apps/bcs/Cargo.toml`，新模块使用现有 sha2/hex/serde/async-trait 依赖。

- [ ] **1. 写请求重试用例，先不接 Worker。** 用 D05 json_text_request 生成 raw 输入，保存 basis 后修改目录为 JSON-only/默认 historyLength=0/禁用扩展，再用原请求重试；规范化结果必须等于原指纹。旧 basis 版本不可读返回依赖错误，不能新建。

```rust
#[test]
fn saved_defaults_survive_new_configuration() {
    use bcs_app_dist_gateway::normalize::apply_defaults;
    let raw = serde_json::json!({"metadata": {}});
    let saved = serde_json::json!({"returnImmediately": false, "historyLength": 20});
    let newer = serde_json::json!({"returnImmediately": true, "historyLength": 0});
    assert_eq!(apply_defaults(&raw, &saved)["configuration"]["historyLength"], 20);
    assert_ne!(apply_defaults(&raw, &saved), apply_defaults(&raw, &newer));
}
```

- [ ] **2. RED：** `cargo test -p bcs-app-dist-gateway --test normalization`。新 crate 注册后确认目标测试因逻辑缺失失败。
- [ ] **3. 实现纯规范化核心及调用顺序。** apply_defaults 只补配置字段，不覆盖显式值；递归 canonical JSON 的所有 metadata/configuration 参与 digest，激活扩展集合排序去重，Parts 保序。逐 Part 的 mediaType/filename/metadata 也保留并参与比较，不提升到 Message/Artifact 顶层；adapter 保留原始字段存在性，基础格式校验不依赖 Card。

```text
authenticate + current resource access + structural validation
  -> lookup RequestKey
  -> if found: normalize(raw, saved basis); compare; recover/read
  -> if absent: freeze appropriate workflow; validate capability;
                normalize(raw, frozen defaults); reserve
  -> if reservation lost: normalize again against winner basis
```

`apply_defaults` 的最小实现只处理已通过结构校验的配置合并；完整规范化版本检查和指纹由同模块独立方法完成：

```rust
pub fn apply_defaults(raw: &serde_json::Value, defaults: &serde_json::Value) -> serde_json::Value {
    let mut normalized = raw.clone();
    let mut config = defaults.as_object().cloned().unwrap_or_default();
    if let Some(explicit) = raw.get("configuration").and_then(serde_json::Value::as_object) {
        config.extend(explicit.clone());
    }
    normalized["configuration"] = serde_json::Value::Object(config);
    normalized
}
```

基础格式校验已拒绝非对象 raw/configuration，函数不拿异常输入猜默认值。版本化 normalize 函数输出 D01 GroupCallInput。沿用现有 digest_fields/digest_json 的公开算法或提取共享纯函数，不让 application 依赖具体 bcs-dist-store。基础格式错误先拒绝；当前权限撤销即使请求已存在也拒绝。

目录实现以已有群 Core、工作流/绑定端口读取，必须能取 exact revision。能力和 Card 结果只调用 C01 的 `GroupWorkflowValidatorCoreService`，不另写节点白名单；部署能力/编译版本与 workflow digest 不符时受理前失败。`card` 方法纯读，不调用新建会话 resolve；冻结后的 context 不随群更新重算能力。记录 ports 调用以证明读 Card 没有保存 spec/claim。

- [ ] **4. GREEN：** normalization/directory 测试，增加配置改变冲突、send/stream 同语义、metadata 改值、权限撤销、并发 winner basis、expired tombstone 用例。`cargo test -p bcs-app-dist-gateway --test normalization --test directory`。
- [ ] **5. 提交：** `feat(bcs): normalize group retries from saved request basis`。

## G02：准备记录、独占轮次、可靠受理与恢复

**Files**

- Create：`apps/bcs/crates/application/bcs-app-dist-gateway/src/{submit,recovery,identity}.rs`。
- Create：`apps/bcs/crates/services/bcs-dist-gateway/src/{admission,identity}.rs`。
- Create：`apps/bcs/crates/application/bcs-app-dist-gateway/tests/{admission,retry_recovery}.rs`、`tests/common/mod.rs`。

- [ ] **1. 先构造真实内存 GroupInvocation repo + 记录 Inbox wrapper 的 application fixture。** fixture `gateway_world()` 在 `tests/common/mod.rs` 创建，返回 service、目录控制器、repo、Inbox 调用记录、固定 clock。两个不同新 messageId 使用同 sid 的并发提交，只有一个 Task/Input；同消息并发返回同 tid。

```rust
#[tokio::test]
async fn busy_never_writes_a_second_start() {
    let world = common::gateway_world().await;
    let first = world.submit_in_context("one").await.unwrap();
    let second = world.submit_in_context("two").await;
    assert!(matches!(second, Err(bcs_service_api::application::dist_gateway::GatewayError::Busy)));
    assert_eq!(world.recording_inbox.user_starts().await, vec![first.receipt.tid]);
}
```

上述 GatewayError::Busy 在 D02 冻结；如最终采用带字段枚举，测试使用 D0 的精确形状同步修改。

- [ ] **2. RED：** `cargo test -p bcs-app-dist-gateway --test admission busy_never_writes_a_second_start -- --exact`。
- [ ] **3. 实现顺序 prepare → claim → InboxDurable → Accepted+pending obligation。** 原请求记录保存绝对期限和稳定派生 ID，并以 PreparationContextBasis 绑定读取的 generation/背景；claim 同时核实 preparation revision 和该 basis。并发失败者不写 Inbox。activate 返回的 durable obligation 或已消费证明可用于确认受理，通知是否送达不影响受理成功。不要 await Worker 执行后才确认受理。

```text
claim result Lost:
  reread exact preparation/current round, sharing a 3-retry budget with refresh/claim
  if same request owns it: recover the original record
  if another request owns it: use reject_before_return(Busy) below
  if idle with unchanged generation/basis: retry claim using the current valid preparation revision
  if idle with a changed generation or background basis:
    load that generation's latest successful background and immutable initial goal
    if background unavailable: use reject_before_return(BackgroundUnavailable)
    refresh_preparation(expected preparation revision, new context basis, derived input)
    if refreshed: claim with the returned revision and basis; never just change generation
    if AlreadyClaimed: recover that original round
    if Busy: use reject_before_return(Busy); if Lost: reread within the same budget
    if OutcomeUnknown: resolve the refresh before any further write
  if budget exhausted: return OutcomeUnknown without Inbox/abort
claim result Busy:
  use reject_before_return(Busy)
reject_before_return(reason):
  reject_unclaimed(request_key, preparation_revision)
  if Rejected/AlreadyRejected: return the definite reason
  if AlreadyClaimed: recover that request, never return Busy for accepted work
  if Lost: resolve rejection of the original revision; never erase a newer preparation
  if unresolved: OutcomeUnknown, never pretend no state was written
Inbox error Quarantined/ContentConflict:
  invoke abandon_round(expected generation, request revision)
  if Abandoned: return CONTEXT_UNAVAILABLE/ADMISSION_CONTENT_CONFLICT
  if AlreadyAccepted: recover normal activation
  if Unknown: retain occupation and recovery obligation
Inbox result StartClosed:
  verify closure against the original Abandoned record; return its saved failure
  if verification is unknown: return OutcomeUnknown; never activate a closed Start
claim result SlotHeld:
  admit identical InputEnvelope
  record inbox evidence
  activate same RoundRef atomically with pending obligation
  attempt reschedule; preserve pending obligation on failure
  on permanent activation failure, abandon only after no-license/no-Start proof
  if abandonment confirmed: return ADMISSION_ABANDONED; otherwise Unknown
  return GroupSubmitResult(receipt, saved effective response config)
```

恢复器公平分页扫描 Preparing/SlotHeld/InboxDurable、Accepted.pending、Abandoned 的封口/Drop 项以及拒绝清理项，按动作类别处理并指数退避至配置最大间隔。已拒绝的 preparation revision 只补清理，永不 claim；同 messageId 重试的新准备必须使用新 revision，旧扫描者不可沿用权限。Preparing 的恢复同样重新核实背景 generation 并条件刷新；SlotHeld 起不再改 GroupRoundInput。刷新不修改原 raw/basis/指纹、sid/tid/execution_id 或绝对 deadline，不因重试续期。新 session 身份使用基线 KeyLayout 可接受的 Sid/Tid；相同首轮请求跨 Gateway 派生一致。id 编码增加 cell/路由信息的需求在 D0 配置决策中登记，不能随意改变已持久身份。

Abandoned 先调用 D03 的 seal_abandoned_start：ClosedAbsent 交 confirm_start_absent 关闭主动义务；ClosedPresent 持久保留 drain 边界并重调度 Worker 丢弃；结果未知退避解析。没有 spec/Start 时不调用普通 Worker Start，不制造假输入来得到消费证明。此恢复路径不能转回 activate。

GroupSubmitResult.response 从保存的原请求与 basis 得到，显式字段优先于保存的默认值；重复/恢复请求以及预留落败者均复用获胜记录。它不是 Task 快照，handler 后续通过 get_task/observe_task 读取真实公共状态，不能把 receipt 包装成虚构 Task。

- [ ] **4. GREEN：** admission/retry_recovery 覆盖每步写前失败/写后丢响应、claim 清理未知、Inbox 先到、许可后通知丢失、原请求恢复读取；`cargo test -p bcs-app-dist-gateway --test admission --test retry_recovery`。命名 barrier 验证：Lost→其他请求占用→拒绝成功→Busy→释放→恢复扫描不会产生 Start；拒绝响应丢失返回 Unknown；同 messageId 新 revision 不受旧扫描影响。另使 A 在 T0 背景准备后暂停、B 完成 T1 并释放，断言 A 重试使用 T1，刷新/claim 再竞争仍有界；背景被清理时先撤销准备。覆盖 Quarantined/ContentConflict、无输入 Abandoned 封口、已有输入封口/消费和响应丢失。
- [ ] **5. 提交：** `feat(bcs): orchestrate recoverable exclusive group admission`。

## G03：公开查询、范围过滤、多轮背景和取消

**Files**

- Create：`apps/bcs/crates/application/bcs-app-dist-gateway/src/{query,subscription,cancel,background}.rs`。
- Create：`apps/bcs/crates/services/bcs-dist-gateway/src/{query,projection,cancel}.rs`。
- Create：`apps/bcs/crates/application/bcs-app-dist-gateway/tests/{queries,cancellation,background}.rs`。
- Modify：`apps/bcs/crates/application/bcs-app-dist-gateway/tests/common/mod.rs`，添加真实内存 TaskFeed/Result fixture。

- [ ] **1. 测试同 caller 不同群不能访问 Task；列表过滤和 total_size 计数发生在分页之前。** 加入未 Accepted 的热 Task，不出现在列表/计数/查询；同 Tid 同时在 hot/archive 时只算一次。覆盖 context/status/statusTimestampAfter 组合筛选、同状态更新时间的稳定分页、空页/末页仍保留有效 page_size 和正确总量、historyLength 缺省/0/正数，以及 includeArtifacts 缺省/false/true。终态归档热记录已清理仍可读取。下一轮拿最初目标、本轮 data、最近成功结果；已清理背景明确拒绝。
- [ ] **2. RED：** `cargo test -p bcs-app-dist-gateway --test queries --test cancellation --test background`。
- [ ] **3. 实现 D02 的 typed 查询参数与单一公开投影。** get 优先遵循 repository restore 返回的完整 cut/Final；归档终态不能拼旧 hot 正文。list 在 repo 内先按 group/scope/source/Accepted、context、status、statusTimestampAfter 过滤，再按状态更新时间及 tid 倒序分页；从同一逻辑索引 cut 返回分页前去重 total_size 和有效 page_size，不能先截页再筛选或用当前页长度当总量。历史仅按 historyLength 裁剪响应，不改存储；Part 属性与顶层 metadata 独立保留。includeArtifacts 默认 false，不加载产物表/对象；true 时一次批量载入当前页。query 本身不驱动执行。

```text
cancel(scope, group, tid):
  authorize exact task and group
  if terminal: TaskNotCancelable
  reserve/reuse stable cancel identity for this tid
  admit cancel intent once
  return CancelTaskResult { task: restored current public task, cancel_request_id }, not AdmissionReceipt-as-Canceled
```

subscribe/observe_task 都校验群/主体/任务和 Accepted，并共享 Snapshot@H 后从 H follow、Gap 重建的实现。公开 subscribe 在首个恢复 cut 已终态时拒绝；内部 observe_task 返回终态首帧后关闭。若首帧非终态而任务随即完成，从该 cut 跟随终态事件，不再重新检查“可否新建公开订阅”。内部观察不要求先 get_task，也不调用公开 subscribe 来绕回终态拒绝逻辑。public cursor 不直接序列化物理 offset。取消/完成的胜负由 Worker checkpoint 决定，application 不写另一个终态。

- [ ] **4. GREEN：** 验证重复 cancel 的同一 request_id、not terminal until confirmed、failed quarantine 不释放、跨群/跨筛选条件游标拒绝、Query 不调用 dispatch、无 per-event get_task。用调用记录确认 includeArtifacts=false 不读产物，true 按页批量读；用 barrier 覆盖任务在 observe restore 前/后完成，内部观察均成功，公开终态 subscribe 仍拒绝。提交 `feat(bcs): expose scoped group tasks and cancellation intents`。

## G04：独立 A2A HTTP adapter、Card 与版本化路由

**Files**

- Create：`apps/bcs/crates/adapters/http/bcs-dist-gateway-http/{Cargo.toml,CONTEXT.md,src/lib.rs,src/state.rs,src/router.rs,src/dto.rs,src/error.rs,src/card.rs}`。
- Create：`apps/bcs/crates/adapters/http/bcs-dist-gateway-http/tests/{routes,request_validation,card}.rs`、`tests/common/mod.rs`。
- Create/Modify：`docs/specs/a2a/extensions/call-metadata/{v1.md,v1.schema.json}`，保留已有时精确增量，定义 includeProgress 所属键及激活行为。
- Modify：`apps/bcs/Cargo.toml`。

- [ ] **1. 用 recording DistGatewayService 和 Card service 建 HTTP tests。** `tests/common` 提供 `router_fixture()`，通过 tower::ServiceExt oneshot 实际请求；记录调用的 caller/group、请求原样配置及 typed 查询参数。对路径 suffix、百分号编码、跨群、媒体头和 413 做真实路由断言。Get/List 逐项验证 historyLength、contextId、status、pageSize/pageToken、statusTimestampAfter、includeArtifacts 的解析、缺省和非法值；时间转为领域 TimestampMillis，不在 adapter 筛选分页结果。golden 响应必须含 tasks/nextPageToken/pageSize/totalSize，测试总量大于页长、空页、末页、无 token 编码空字符串及数值越界明确失败；Part filename/metadata 通过请求和响应的真实 DTO 往返。

```rust
#[tokio::test]
async fn cancel_suffix_preserves_full_task_id() {
    let fixture = common::router_fixture();
    let response = fixture.post("/api/v1/collaboration/a2a/groups/g/tasks/s0042.x.t:cancel", "{}").await;
    assert_eq!(response.status(), axum::http::StatusCode::OK);
    assert_eq!(fixture.calls.cancel_task_ids(), vec!["s0042.x.t"]);
    assert_eq!(fixture.calls.groups(), vec!["g"]);
}
```

- [ ] **2. RED：** `cargo test -p bcs-dist-gateway-http --test routes --test request_validation --test card`。
- [ ] **3. Router 只调用应用边界，使用 SDK 固定版本模型。** 用 `GET/POST /tasks/{task_action}` 解析后缀，非空检查，URL 解码一次；Card base URL 来自可信配置。标准 DTO 在本 crate 内，不 re-export SDK 给 service-api。请求必须完整转换 D01 raw 请求和逐 Part 属性，不能提前补当前默认值。ListTasksResponse 直接编码 TaskPage 的四项；page_size/total_size checked-convert 到 SDK int32，失败返回安全错误，禁止额外遍历页或读取 repo 来补总数。

```rust
pub fn split_task_action(value: &str) -> Option<(&str, &str)> {
    for action in [":cancel", ":subscribe"] {
        if let Some(id) = value.strip_suffix(action) {
            if !id.is_empty() {
                return Some((id, &action[1..]));
            }
        }
    }
    None
}
```

检查 application/a2a+json、A2A-Version=1.0；text/event-stream 为响应。静态 1 MiB 限制在解析和业务受理前；可变 capability 验证留在新请求分支。known includeProgress 键无激活头时应用拒绝，未知 metadata 不成为 principal。public raw/url 未实现时不输出 Card 能力。group Card 读取是纯读并匹配已装配认证，匿名模式不隐式启用。

- [ ] **4. typed 错误完整映射。** 覆盖 Busy、Conflict、NotFound、ContextNotFound、ResultExpired、CapabilityUnsupported、BackgroundUnavailable、AdmissionUnknown、UnsupportedOperation、TaskNotCancelable 和安全 dependency failure；禁止将原始 SQL/upstream body 放进 response。按旧规范标准错误结构做 JSON golden assertions。
- [ ] **5. GREEN 后提交：** routes/request_validation/card 全通过，schema 是合法 JSON；`feat(bcs): add group A2A HTTP routes and card publication`。

## G05：同步等待、默认配置和无取消断线

**Files**

- Create：`apps/bcs/crates/adapters/http/bcs-dist-gateway-http/src/{send,wait}.rs`。
- Create：`apps/bcs/crates/adapters/http/bcs-dist-gateway-http/tests/send_wait.rs`。
- Modify：`apps/bcs/crates/adapters/http/bcs-dist-gateway-http/tests/common/mod.rs`，增加可控制 task stream。

- [ ] **1. 使用 Tokio 控制时间验证默认 false 等待、60 秒 504、true 返回当前 Task。** 返回前任务已终态也允许真实终态；stream 的 returnImmediately 不改变首帧行为；断开 HTTP 等待不调用 cancel。用 barrier 在 submit 成功后、observe_task 恢复前完成 Task，再测试首帧恢复后、follow 前完成；两者均返回真实终态，不报 UnsupportedOperation。
- [ ] **2. RED：** `cargo test -p bcs-dist-gateway-http --test send_wait`。
- [ ] **3. 使用 GroupSubmitResult.response 和内部 observe_task，超时只结束传输。** 不从 raw 请求或当前默认值重新推导有效配置。已知 tid/context 放入安全 details；不能在超时路径成功返回 WORKING，也不能把公开订阅拒绝当成 send 的完成结果。

```text
submitted = service.submit(caller, raw_request)
effective = submitted.response
if effective.return_immediately:
  return service.get_task(caller, GetTaskQuery(group, submitted.receipt.tid,
                                             history_length=Some(effective.history_length)))
updates = service.observe_task(caller, ObserveTask(group, submitted.receipt.tid,
                                                 history_length=effective.history_length))
read first Task snapshot; if terminal: return it
follow that same cut until terminal or HTTP wait deadline; return terminal Task
on deadline: 504 SEND_WAIT_TIMEOUT; do not cancel
on client drop: drop reader only; do not drop durable task
```

Abandoned 请求返回持久失败墓碑，不生成对外 Task；相同 messageId 不能重新启动该轮，新 messageId 在占用释放后可继续。

同请求的再次阻塞 send 继续等待原 Task，不能因 Duplicate 改成立即返回。配置更新不改变原 GroupSubmitResult.response；例如原请求省略 returnImmediately/historyLength，保存为 false/20，部署默认值改为 true/0 后，原样重试仍等待并至多返回最近 20 条历史。显式传入的值必须优先于原 basis 默认值。共享执行期限仍使用原记录，HTTP 重试不续期。

- [ ] **4. GREEN 与提交：** 验证 fake clock 前后 call count、cancel_calls=0、deadline 不变；在 HTTP 层断言默认值变更后等待行为及 historyLength 仍取原有效配置，内部 observe_calls 与公开 subscribe_calls 分开记录。上述两种完成竞态都返回成功终态；`feat(bcs): support bounded synchronous group send waits`。

## G06：SSE 顺序、Gap 恢复与背压

**Files**

- Create：`apps/bcs/crates/adapters/http/bcs-dist-gateway-http/src/{stream,encode}.rs`。
- Create：`apps/bcs/crates/adapters/http/bcs-dist-gateway-http/tests/{streaming,wire_golden}.rs`。
- Modify：`apps/bcs/crates/application/bcs-app-dist-gateway/src/subscription.rs`。

- [ ] **1. 注入真实 application 流序列：snapshot@H、progress、final Artifact、terminal。** 检查首帧 Task、顺序、15 秒 keep-alive 注释、终态关闭、终态新订阅标准错误，及 send 命中终态仍可返回一次快照关闭。对 message:stream 同样设置 submit 后/observe restore 前及首帧后/follow 前完成的 barrier，断言完整终态可达。
- [ ] **2. RED：** `cargo test -p bcs-dist-gateway-http --test streaming --test wire_golden`。
- [ ] **3. 实现 A2A 标准 SSE 编码，不发内部 Reset。** message:stream 在 submit 后调用内部 observe_task，historyLength 使用 GroupSubmitResult.response，忽略 returnImmediately 对等待的影响；`tasks/:subscribe` 调用公开 subscribe，不能复用 handler 导致终态策略互换。includeProgress 的公共过滤已在 Worker/投影前完成，adapter 再次拒绝任何内部 OutputClass::Thinking/Tool 泄漏但不以此代替源头过滤。

```text
snapshot -> emit standard Task
public progress -> emit standard status/message update
committed final artifacts -> emit artifact updates in position order
committed terminal -> emit terminal status and close
Gap -> application restores a fresh snapshot/cut and continues observation; adapter emits Task
```

首期不承诺 Last-Event-ID 精确重放；若提供 SSE id，采用绑定 caller/group/tid 的不透明逻辑令牌，不输出物理 Stream ID。慢连接缓冲达配置界限时关闭 reader，保留后台任务；普通事件不得逐条 get_task。

- [ ] **4. GREEN：** 保活不写 history、不续期；客户端断开不取消；含内部工具/思考/堆栈及私有 Part metadata 的 fixture 在 get/list/SSE 三出口都不出现，允许的 Part filename/metadata 与 Artifact 顶层 metadata 各自保持不变。提交 `feat(bcs): stream committed group task views over SSE`。

最终验证：`cargo test -p bcs-app-dist-gateway -p bcs-dist-gateway -p bcs-dist-gateway-http`。这些测试证明各边界行为，不是三角色联调；真实闭环见 [集成计划](plan-4-integration.md)。
