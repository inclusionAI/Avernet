# 实施接口清单与共享测试基座

所属计划：[plan.md](../plan.md)。这是 T01 起逐项交付的**新增接口设计**；同名旧类型必须显式复用或通过限定路径区分，不能把文中新增签名描述成现有接口。所有时间单位是 epoch milliseconds，内部 revision/generation 使用 checked_add，溢出返回 Conflict，不回绕。

## 1. 纯类型与文件归属

路径前缀为 `apps/bcs/crates/`。下表每项必须有 serde／边界测试或明确禁止序列化；不直接复用 HTTP DTO 作为 Core 输入。

| 文件（Create） | 类型／字段 |
| --- | --- |
| `contracts/bcs-domain/src/group_a2a/identity.rs` | `CallerId { issuer: String, subject: String }`；`CallScope { deployment: String, caller: CallerId, group_id: String }`；`TaskKey { scope: CallScope, task_id: String }` |
| `contracts/bcs-domain/src/group_a2a/input.rs` | `InputPart::Text(String)` / `Data(serde_json::Map<String, Value>)`；`CallMessage { message_id, context_id: Option<String>, task_id: Option<String>, parts: Vec<InputPart>, semantic_json: Value }`；`ReturnOptions { immediately: bool, history_length: Option<u32>, accepted_output_modes: Vec<String> }`；`CallRequest { scope, message: CallMessage, options: ReturnOptions, include_progress: bool, active_extensions: Vec<String>, canonical_request: Value }` |
| `contracts/bcs-domain/src/group_a2a/state.rs` | `TaskState::{Submitted,Working,Completed,Failed,Canceled,Rejected}`；`Phase::{Admitted,Starting,Running,Completing,Cancelling,Failing,Terminal}`；`ContextState::{Idle,Occupied,Isolated,HistoryExpired}`；`TaskView { key: TaskKey, context_id: String, state: TaskState, revision: u64, status_at_ms: u64, status_message: Option<PublicMessage>, artifacts: Vec<OutputArtifact>, history: Vec<PublicMessage> }` |
| `contracts/bcs-domain/src/group_a2a/output.rs` | `PartAttributes { media_type: Option<String>, filename: Option<String>, metadata: Option<Value> }`；`OutputPart::Text { text: String, attributes: PartAttributes }` / `Data { data: Value, attributes: PartAttributes }`；`OutputArtifact { id: String, position: u32, name: Option<String>, description: Option<String>, parts: Vec<OutputPart>, metadata: Option<Value>, extensions: Vec<String> }`；`PublicMessage { id: String, task_id: String, context_id: String, from_client: bool, parts: Vec<OutputPart> }`；`PublicEvent { sequence: u64, event_id: String, body: PublicEventBody }`，body 为 Status(TaskView 中的状态投影)/Artifact(OutputArtifact)/Message(PublicMessage)，`StatusProjection {state:TaskState,status_at_ms:u64,message:Option<PublicMessage>}` 不递归嵌入 events |
| `contracts/bcs-domain/src/group_a2a/configuration.rs` | `ExecutionSnapshot { group_revision: u64, config_hash: [u8;32], strategy: GroupStrategy, participants: Vec<Participant>, definition: Option<Value>, outputs: Vec<OutputSelector> }`；`OutputSelector { key: String, node_id: String, format: OutputFormat }`，format 为 Text/Data；definition 为固定版本已有 definition 的序列化快照，不是可变名称查找 |
| `contracts/bcs-domain/src/group_a2a/error.rs` | `CallError::{InvalidArgument,Unauthenticated,AuthUnavailable,GroupUnavailable,GroupNotReady,TaskNotFound,ContextNotFound,ContextBusy,ContextUnavailable,ContextConfigurationChanged,ContextHistoryExpired,MessageConflict,ResultExpired,Unsupported,NotCancelable,OutputInvalid,Capacity,WaitTimeout,StorageUnavailable,Conflict}`；variants 可携带已授权 ID，但 Display 不输出底层异常 |
| `contracts/bcs-domain/src/group_a2a/mod.rs` | 只声明并 re-export 上述纯类型；原 domain `src/lib.rs` 注册 `pub mod group_a2a` |

`semantic_json` 保留 Message 的标准字段；`canonical_request` 是**经结构校验、剔除传输头后**的完整语义 SendMessageRequest，不包含可信 scope。adapter 必须构造而非信任客户端传入一个 canonical 字段。CallRequest 是执行输入，不是历史公开投影。

## 2. 核心签名

下面 `CallResult<T> = Result<T, CallError>`（由domain error模块导出）。Core／Repo error 在 application 边界统一转换，adapter 只见 application error；可复用该错误族但不向 adapter 透传 DbError。

### 2.1 配置、资格、指纹与状态函数（T01／T06／T08）

```rust
pub fn validate_a2a_config(c: &GroupA2aConfig) -> Result<(), ConfigError>;
pub fn validate_eligibility(g: &Group, execution: &ExecutionSnapshot)
    -> Result<(), EligibilityError>;
pub fn fingerprint(request: &CallRequest) -> CallResult<[u8; 32]>;
pub fn transition(phase: Phase, event: LifecycleEvent) -> CallResult<Phase>;
```

- `GroupA2aConfig` 属于 `bcs-config-api/src/group_a2a.rs`：enabled、auth: GroupA2aAuthConfig（其chain: Vec<String>对应 `[a2a.auth].chain`）、wait_ms、execution_ms、settle_ms、retention_ms、heartbeat_ms、auth_timeout_ms、caller_active_limit、deployment_active_limit、caller_stream_limit、instance_stream_limit、request_bytes、artifact_bytes、artifact_count、history_message_bytes、history_count、history_bytes、history_default_count、history_response_max_count、page_default_size、page_max_size、page_bytes、scan_batch、unsettled_work_limit、input_part_limit、stream_event_limit、stream_byte_limit、manager_idle_ms、manager_grace_ms、recovery_attempt_limit、recovery_backoff_initial_ms、recovery_backoff_max_ms、token_default_ttl_ms、token_max_ttl_ms。值来自 spec §12，serde deny_unknown_fields。
- `ConfigError::{MissingAuth,UnsupportedAuth,InvalidLimit}`；`EligibilityError::{NotPublic,Disabled,WrongKind,UnsupportedStrategy,HumanMember,InvalidExecution}`。
- `LifecycleEvent::{Start,CompleteIntent,CancelIntent,FailIntent,CommitSuccess,CommitFailure,CommitCanceled}`。仅合法边可变；Completing+CancelIntent→NotCancelable，Terminal 不接纳新意图。
- `Group` 新 support_a2a bool／a2a_revision u64；执行快照及 output selector 必须通过资格校验才可写入 validated publication snapshot。

### 2.2 调用 application（T08／T14／T18）

Create `service-api/bcs-service-api/src/application/group_a2a.rs`：

```rust
#[async_trait]
pub trait GroupA2aService: Send + Sync {
    async fn send(&self, request: CallRequest) -> CallResult<Submission>;
    async fn get(&self, query: TaskQuery) -> CallResult<TaskView>;
    async fn list(&self, query: ListQuery) -> CallResult<TaskPage>;
    async fn cancel(&self, key: TaskKey) -> CallResult<TaskView>;
    async fn subscription(&self, key: TaskKey) -> CallResult<SubscriptionStart>;
    async fn events(&self, query: EventQuery) -> CallResult<Vec<PublicEvent>>;
}
```

| DTO | 精确含义 |
| --- | --- |
| Submission | `{ task: TaskView, duplicate: bool }`；send 在持久化受理后返回，HTTP 的阻塞等待由适配协调，不能在这个方法内部等待网络 |
| TaskQuery | `{ key: TaskKey, history_length: Option<u32> }` |
| ListQuery | `{ scope: CallScope, context_id: Option<String>, status: Option<TaskState>, page_size: u32, page_token: Option<String>, history_length: Option<u32>, status_after_ms: Option<u64>, include_artifacts: bool }` |
| TaskPage | `{ tasks: Vec<TaskView>, next_page_token: String, page_size: u32, total_size: u32 }` |
| SubscriptionStart | `{ task: TaskView, after_sequence: u64 }`；单个一致快照，首次 subscribe 拒绝终态 |
| EventQuery | `{ key: TaskKey, after_sequence: u64, limit: u32 }`；只返回已提交公开事件，连续有序 |

`GroupA2aService::send` 与 wire ReturnOptions 不矛盾：application 完成受理；adapter 用 `TaskWaitService` 等待／stream。Create 同文件 `TaskWaitService::wait(key: TaskKey, deadline_ms: u64) -> CallResult<TaskView>`，使用 application 的持久化读＋注入通知端口，达到 deadline 为 WaitTimeout。它不以 HTTP disconnect 触发业务取消。

### 2.3 Core 与组合 Repo（T07–T15）

Create `service-api/bcs-service-api/src/core/group_invocation.rs`：

```rust
#[async_trait]
pub trait GroupInvocationCoreService: Send + Sync {
    async fn admit(&self, request: CallRequest) -> CallResult<Submission>;
    async fn complete(&self, candidate: CompletionCandidate) -> CallResult<TaskView>;
    async fn cancel(&self, key: TaskKey) -> CallResult<TaskView>;
    async fn reconcile(&self, claim: ExecutionClaim, observation: ExecutionObservation)
        -> CallResult<TaskView>;
}
```

Create `service-api/bcs-service-api/src/port/repo/group_invocation.rs` 和按职责拆分的子模块。Repo 不是 HTTP API，不做 token 校验，**仍必须接受完整 TaskKey／CallScope 做数据隔离**。

```rust
#[async_trait]
pub trait GroupInvocationRepo: Send + Sync {
    async fn admit(&self, cmd: AdmitCall) -> CallResult<Submission>;
    async fn get(&self, query: TaskQuery) -> CallResult<TaskView>;
    async fn list(&self, query: ListQuery) -> CallResult<TaskPage>;
    async fn intent(&self, cmd: RecordIntent) -> CallResult<TaskView>;
    async fn commit_terminal(&self, cmd: CommitTerminal) -> CallResult<TaskView>;
    async fn snapshot(&self, key: TaskKey) -> CallResult<SubscriptionStart>;
    async fn events(&self, query: EventQuery) -> CallResult<Vec<PublicEvent>>;
    async fn project(&self, cmd: ProjectEvents) -> CallResult<u64>;
    async fn claim_due(&self, cmd: ClaimDue) -> CallResult<Vec<ExecutionClaim>>;
    async fn record_observation(&self, cmd: RecordObservation) -> CallResult<()>;
    async fn execution(&self, key: TaskKey) -> CallResult<ExecutionClaim>;
    async fn record_work(&self, claim: ExecutionClaim, evidence: WorkEvidence) -> CallResult<()>;
    async fn work(&self, claim: ExecutionClaim) -> CallResult<Vec<WorkEvidence>>;
    async fn release_isolation(&self, cmd: ReleaseIsolation) -> CallResult<()>;
    async fn expire(&self, cmd: ExpireTasks) -> CallResult<Vec<TaskKey>>;
    async fn purge(&self, cmd: PurgeTask) -> CallResult<bool>;
}
```

| DTO | 字段和约束 |
| --- | --- |
| AdmitCall | `{ request: CallRequest, fingerprint: [u8;32], fingerprint_version: u16, now_ms: u64, limits: GroupA2aConfig }`；Repo 在事务内取群快照／前轮背景，不能只信任调用方传来的 eligible |
| RecordIntent | `{ key, expected_revision: u64, generation: u64, kind: IntentKind, now_ms: u64, settle_deadline_ms: u64 }`；kind=Complete(Vec<OutputArtifact>)/Cancel/Fail(FailureReason) |
| CommitTerminal | `{ key, expected_revision, generation, state: TaskState, artifacts: Vec<OutputArtifact>, stopped: bool, source_boundaries: Vec<SourcePosition>, now_ms }`；stopped=false 只允许 FAILED+isolated |
| CompletionCandidate | `{ key, generation, execution_id: String, artifacts: Vec<OutputArtifact>, source_boundaries: Vec<SourcePosition>, evidence: Vec<WorkEvidence> }`；由受信执行关联构造，不直接用工具 payload 创建 |
| SourcePosition | `{ stream: String, sequence: u64, event_id: String }`；跨 stream 不比较 sequence |
| ProjectEvents | `{ key, generation, expected_revision, sources: Vec<SourcePosition>, events: Vec<PublicEvent> }`；cursor 与事件同事务，去重不重复追加 |
| ClaimDue | `{ owner: String, now_ms: u64, lease_ms: u64, limit: u32 }`；limit≤100，owner 不是凭据 |
| ExecutionClaim | `{ key, context_id: String, generation, revision, execution_id: String, phase: Phase, lease_token: u64, execution_deadline_ms, settle_deadline_ms: Option<u64>, snapshot: ExecutionSnapshot, input: Value }`；input是已保存的本轮initial/current/previous执行envelope，只 trusted worker 获得 |
| RecordObservation | `{ claim: ExecutionClaim, observation: ExecutionObservation, now_ms, next_check_at_ms }`，必须 fence lease_token |
| ReleaseIsolation | `{ key, generation, expected_revision, evidence: Vec<WorkEvidence>, now_ms }`；所有未终结关联被覆盖，不能传 stopped=true 冒充证据 |
| ExpireTasks | `{ now_ms, limit: u32 }`；原子逻辑删除，跳过恢复／新轮依赖 |
| PurgeTask | `{ key, row_limit: u32 }`；已逻辑删除才可物理回收，false 表示尚有分片 |
| FailureReason | StartFailed / ExecutionFailed / DeadlineExceeded / MissingCompletion / OutputInvalid / StopUnconfirmed |

隔离释放还须在同一事务完成原 Session 的对应 activation（如果尚未完成）、清除 active 指针、释放容量；FAILED 的 Task 不改终态。检查 Group/Session 原 mutation 对这些字段和表的读取不能绕过。

### 2.4 执行端口（T09／T10／T12）

Create `service-api/bcs-service-api/src/port/group_execution.rs`：

```rust
#[async_trait]
pub trait GroupExecutionPort: Send + Sync {
    async fn start(&self, claim: ExecutionClaim) -> CallResult<ExecutionObservation>;
    async fn observe(&self, claim: ExecutionClaim) -> CallResult<ExecutionObservation>;
    async fn stop(&self, claim: ExecutionClaim) -> CallResult<ExecutionObservation>;
}
```

`ExecutionObservation::{NotStarted,Running(Vec<WorkEvidence>),Complete(CompletionCandidate,Vec<WorkEvidence>),Failed(FailureReason,Vec<WorkEvidence>),Stopped(Vec<WorkEvidence>),Unknown(Vec<WorkEvidence>)}`。

`WorkEvidence { work_id: String, generation: u64, state: WorkState, proof_id: Option<String> }`；`WorkState::{Queued,Started,Stopped,NotStarted,Unknown}`。只有后端能核对的停止／未启动证据可以构造 stopped observation，端口不凭 Run.Aborted 认定 Stopped；proof_id 不是秘密，也不是可由请求者选择的权限。

实现留在现有 MessageFlow／Runtime 的 application 方法后，通过 Bootstrap 的路由桥选 strategy，业务 application 不直接依赖两套具体服务。异步 lifecycle worker 消费 port、把观察交回 Core；Core 本身不调用 port。

### 2.5 认证及凭据（T03–T05）

Create `service-api/bcs-service-api/src/application/a2a_auth.rs`、`port/a2a_credentials.rs`、`port/repo/a2a_token.rs`：

```rust
pub struct PresentedToken(secrecy::Secret<String>); // 不 derive serde/Debug
pub struct AuthenticatedA2aCaller {
    pub caller: CallerId,
    pub deployment: String,
    pub expires_at_ms: u64,
    pub credential_id: String, // runtime-only 复验关联，不进入业务日志
}
pub enum AuthFailure { Rejected, Unavailable }

#[async_trait]
pub trait A2aAuthenticationService: Send + Sync {
    async fn authenticate(&self, token: PresentedToken)
        -> Result<AuthenticatedA2aCaller, AuthFailure>;
    async fn revalidate(&self, caller: &AuthenticatedA2aCaller)
        -> Result<(), AuthFailure>;
}
#[async_trait]
pub trait A2aCredentialVerifierPort: Send + Sync {
    async fn verify(&self, token: &PresentedToken, now_ms: u64)
        -> Result<AuthenticatedA2aCaller, AuthFailure>;
    async fn revalidate(&self, caller: &AuthenticatedA2aCaller, now_ms: u64)
        -> Result<(), AuthFailure>;
}
#[async_trait]
pub trait A2aTokenRepo: Send + Sync {
    async fn create_client(&self, client: ClientRecord) -> Result<(), AuthFailure>;
    async fn issue(&self, record: TokenRecord) -> Result<(), AuthFailure>;
    async fn lookup(&self, digest: [u8;32], now_ms: u64)
        -> Result<Option<AuthenticatedA2aCaller>, AuthFailure>;
    async fn revoke(&self, credential_id: &str, now_ms: u64) -> Result<(), AuthFailure>;
    async fn disable_client(&self, client_id: &str, now_ms: u64) -> Result<(), AuthFailure>;
    async fn list(&self, client_id: &str) -> Result<Vec<TokenMetadata>, AuthFailure>;
    async fn current(&self, credential_id: &str, now_ms: u64)
        -> Result<Option<AuthenticatedA2aCaller>, AuthFailure>;
}
```

ClientRecord={deployment,client_id,enabled,created_at_ms}；TokenRecord={credential_id,client_id,deployment,digest:[u8;32],purpose:A2aCall,issued_at_ms,expires_at_ms,revoked_at_ms:Option<u64>}；TokenMetadata 仅包含 ID／用途／时间／状态，不包含 digest。同 scope client ID 不重用；令牌摘要唯一。

Plugin API `bcs-auth-api/src/a2a.rs` 定义独立 `A2aAuthPlugin`（not-applicable/authenticated/rejected/unavailable 四结果）和 `A2aAuthChain`；类型在该 Plugin API 自持，不依赖 Service API。Bootstrap 的 bridge 做逐字段转换，`A2aCredentialVerifierPort` 不 re-export Plugin API。Credential lookup 同样通过 plugin-owned trait，在 Bootstrap 桥接 A2aTokenRepo；避免 plugin 引用具体 Store。

签发用例 `A2aCredentialAdminService::{create_client,issue,revoke,disable_client,list}`，与 Repo 名称对应；`issue(client_id: &str, ttl_ms: u64) -> Result<IssuedToken, AuthFailure>`，IssuedToken={metadata:TokenMetadata, secret:PresentedToken}。CSPRNG 使用现有 rand::rngs::OsRng，填充 32 bytes，再 base64url/no-pad；不要用 fastrand。token 发出前必须完成持久化，失败不显示明文。

### 2.6 Card、运维及测试端口（T13／T16／T18）

- `GroupA2aCardService::card(scope: CallScope) -> CallResult<CardProjection>`；CardProjection 为已校验的无凭据宣告（name、description、version、base_url、modes、skills、security），HTTP adapter 编成标准 DTO；Fuse 用同源投影。不能让 Core 持有标准 HTTP DTO。
- `GroupA2aPublicationRepo::{claim,read_desired,ack,retry}` 使用 PublicationClaim={deployment:String,group_id:String,revision:u64,lease_token:u64}；PublicationDesired={revision,card:Option<CardProjection>}，None 表示 DELETE；ack 必须 fence revision，retry 保存 next_check_at 和安全错误类别。
- `GroupA2aAdminService::inspect(key)` 返回未停止关联；`reconcile(key)` 触发重新核实；`release(key,evidence_ids)` 只查服务端证据并调用 ReleaseIsolation，不能接受 force flag。
- `A2aChangeNotifier::wait(key:&TaskKey,after_sequence:u64,until_ms:u64)->CallResult<()>` 仅唤醒，返回后重读 Repo；注册通知和初始高水位要避免间隙。Memory watch 通知跨实例不可靠，worker 的有界持久化轮询仍必需。
- `A2aClock::now_ms()->u64` 注入时钟；不使用网络或系统睡眠写期限测试。

## 3. 测试基座：不会以 mock 成功替代实际实现

共享纯 fixture 定义于 `bcs-test-support/src/contract/group_a2a/fixtures.rs`；组装在各 crate `tests/support/mod.rs`，公共 prelude re-export 所用新增类型。task 里的 `world()` 是该 crate 的**新增测试构造函数**，不是未知的生产 API。

| helper | 返回／行为；首次实现任务 |
| --- | --- |
| `caller(name:&str)->CallerId` | issuer="bcs-a2a"，subject=name；T01 |
| `scope(name:&str)->CallScope` | deployment="test"，group_id="group-fixture"，caller(name)；T01 |
| `request(name:&str,id:&str)->CallRequest` | 新 context、文本 question、returnImmediately=true、空扩展、includeProgress=false；canonical_request 由同一输入构造；T01 |
| `eligible_group()->Group` | 用既有 Group fixture 构建 Normal/Public/ManagerWorker、两个 Bot、support_a2a=true；不用 Human 冒充 Bot；T06 |
| `test_token_store()->Arc<dyn A2aTokenRepo>` | SQLite memory 连接＋完整 migration；T03；另有 Memory、MySQL wrappers |
| `world()->TestWorld` | T07 首先只装真实 Repo，Group fixture使用不创建初始Session的路径（create_initial_session=false或直接Group Repo upsert）；T08 注入真实 Core/application 与 RecordingExecution；T09/T10 的专属 world 替换为真实 MessageFlow／Runtime 加 fake Bot boundary |
| `TestWorld::{repo,app,core}` | Arc 对应合同；app/core T08 后可用；repo 必须来自被测实现 |
| `TestWorld::advance(ms:u64)` | 推进 fake clock；不隐式消费 worker；T08 |
| `TestWorld::drain()->CallResult<()>` | 单批 claim≤100，执行一轮 application worker；T08 |
| `TestWorld::start_count()->usize` | RecordingExecution 真实 start 调用次数；T08 |
| `TestWorld::set_stop_unknown(bool)` | fake 下游在精确 stop/observe 上返回 Unknown/Stopped；不直接改 Task 状态；T12 |
| `TestWorld::fail_at(point:FaultPoint)` | 一次性在真正持久化／dispatch 边界抛错；T07 起分阶段实现 |
| `TestWorld::restart()->TestWorld` | 同 SQLite 文件新建连接、Core/application/worker，保留 DB，清空进程内队列；T13 |
| `TestWorld::set_support_a2a(value:bool)` | 调真实 Group mutation 并增加 revision、同步意图；T06/T07，不裸改测试字段绕过路径 |
| `TestWorld::pending_records()->(usize,usize,usize)` | DB 统计 Task／reservation／intent，用于 rollback 验证；T07 |
| `key(task:&TaskView)->TaskKey` | 返回 task.key.clone()；T01 |

FaultPoint 枚举：AfterSessionWrite、AfterReservationWrite、BeforeAdmissionCommit、AfterAdmissionCommit、BeforeDispatch、AfterDispatchBeforeAck、BeforeTerminalCommit、AfterTerminalCommit、BeforeExpiryCommit、AfterLogicalExpiry、BeforePublicationAck。只在测试配置注入，不加 production 环境变量分支。

Token 测试 helper `issue_fixture(store, client_id, secret, now_ms, ttl_ms)`：真实 create_client＋SHA-256(secret)＋Repo.issue，返回 credential_id；仅测试可用确定字符串，生产签发不接受调用方自选秘密。`now()` 固定 1_000_000ms。

其他专属 fixture（HTTP、manager、state machine、catalog）的新增方法在所属任务明确列出，必须实现为对真实应用／存储的调用。共享 conformance 函数取 `&dyn Repo/Service` 与这些 recorders，不让 test-support import 具体服务造成依赖环。

## 4. 认证 Plugin API 的精确桥接类型

以下全部归 `bcs-auth-api/src/a2a.rs`，不引用 Service API 类型，避免 plugin-api → service-api 逆向依赖。

```rust
pub struct PresentedCredential(secrecy::Secret<String>);
pub struct PluginPrincipal {
    pub issuer: String, pub subject: String, pub deployment: String,
    pub credential_id: String, pub expires_at_ms: u64,
}
pub enum PluginVerdict {
    NotApplicable, Authenticated(PluginPrincipal), Rejected, Unavailable,
}
pub enum PluginAuthError { Rejected, Unavailable }
#[async_trait]
pub trait CredentialLookup: Send + Sync {
    async fn by_digest(&self, digest: [u8;32], now_ms: u64)
        -> Result<Option<PluginPrincipal>, PluginAuthError>;
    async fn current(&self, credential_id: &str, now_ms: u64)
        -> Result<Option<PluginPrincipal>, PluginAuthError>;
}
#[async_trait]
pub trait A2aAuthPlugin: Send + Sync {
    fn name(&self) -> &'static str;
    async fn verify(&self, token: &PresentedCredential, now_ms: u64) -> PluginVerdict;
    async fn revalidate(&self, caller: &PluginPrincipal, now_ms: u64) -> PluginVerdict;
}
```

`A2aAuthChain::new(Vec<Box<dyn A2aAuthPlugin>>)->Self` 保序；`verify(&PresentedCredential,u64)->Result<PluginPrincipal,PluginAuthError>`／`revalidate(&PluginPrincipal,u64)` 同结果类型。运行配置组装负责禁止空链、重复名或无鉴权实现；测试可直接构造链证明边界。桥接时用受信类型逐字段复制，不JSON序列化秘密；PresentedToken/PresentedCredential 的 Debug 固定为 redacted，expose只返回借用，不提供Serialize。

## 5. 任务中附加的方法归属补充

- `GroupA2aWorker`、`GroupA2aRetention` 的构造都显式注入 clock/limits/ports，run_batch 不读取全局配置。
- `SqlGroupInvocationRepo` 分阶段实现未到达任务的方法时返回 `CallError::Unsupported`，生产组装在T19只允许全能力实例；每个后续任务必须删除该方法的临时拒绝分支，最终conformance逐方法调用证明没有剩余缺口。这不是允许成功noop。
- `MemoryA2aUnitOfWork` 需要真实共享 Group/Session/Task 状态，不另造第二份同名数据；T07在组合Store内实现GroupRepoPort/SessionRepoPort的memory适配器并从composition注入，旧Store不新增对该具体crate的依赖；已有独立Memory构造继续用于非A2A测试。
- 各crate测试 `TestWorld` 的 `task(key)` 调实际Repo.get；`context_occupied/is_isolated/capacity_used/pending_records` 用精确SQL／Memory读，不自己维护影子状态。
- `running_call(id)` = app.send(request)+drain一次，fake下游保持Running；`running_stopped_call(id)` 再向fake执行端送停止观察，但Task本身仍未终态。
- `completed_call(caller,id)` = send+drain+合法completion candidate+真实Core.complete+终态commit，不能直接UPDATE Task。
- `finish/complete(key,summary)` 使用已关联execution_id和stopped evidence构造CompletionCandidate。T11以前仅专属Runtime输入测试允许受控终态fixture，T11必须替换成此真实闭环。
- `now()->u64` 为测试固定时钟1_000_000；`sha256(&str)->[u8;32]` 通过已存在sha2，错误／日志不输出其原文或摘要。
- `start_call_with_data(id,data)` 创建CallRequest的Data object并通过真实服务调用；`claim(key)` 是受信测试读取execution意图，不允许外部请求者构造lease token。
- publication接口参数不省略：`claim(owner:String,now_ms:u64,limit:u32)->CallResult<Vec<PublicationClaim>>`；`read_desired(claim:PublicationClaim)->CallResult<PublicationDesired>`；`ack(claim:PublicationClaim)->CallResult<bool>`；`retry(claim:PublicationClaim,next_check_at_ms:u64,uncertain:bool)->CallResult<()>`。初始claim获取DB租约，ack=false即旧版本不能当作同步完成。
- `GroupA2aAdminService::{inspect,reconcile}` 参数为TaskKey；返回分别为 `CallResult<Vec<WorkEvidence>>`、`CallResult<TaskView>`；`release(key:TaskKey,evidence_ids:Vec<String>)->CallResult<()>`。管理身份在CLI/管理application入口核验，与A2A调用身份分离。
- `GroupA2aCatalogPort::put(group_id:&str,card:CardProjection)->CallResult<()>`、`delete(group_id:&str)->CallResult<()>`；返回成功含远端有效ack，超时/ack=false不能当成功。该trait放Service API port，bootstrap映射Fuse类型。


## 6. 有界 schema 与 fixture 审核要求

所有公开DTO还需携带标准Part/Artifact可选属性并按spec公开allowlist处理；上述PartAttributes不得直接复制内部工具metadata。`InputPart`的执行投影可以简化，但原始Message必须完整保存在semantic_json，wire往返测试覆盖mediaType、filename、metadata和extensions。首期raw/url只在持久化schema中允许版本化的未来表达，输入及公开结果校验不放行文件。

Pure helpers的位置：transition及其LifecycleEvent在domain/state.rs；ConfigError在config-api/group_a2a.rs；EligibilityError在domain/configuration.rs，validator在Group Core的a2a_eligibility.rs；fingerprint在bcs-group-a2a/fingerprint.rs。测试按这几个明确export导入，不通过未注册的便利函数猜测。

Fixture代码不提交生产常量秘密，测试crate不得反向进入production依赖。新增测试目标文件必须被cargo发现；过滤运行显示0 tests即失败。有await的helper都声明async，TestWorld的控制方法只有advance、fail_at、set_stop_unknown、clear_memory_queue不async，其余DB／服务读取async；测试代码随定义保持一致。
