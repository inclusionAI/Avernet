//! Bounded T1 protocol prototype only. Never linked into a production binary.
//! Real CoordinationStore handles owner seals and checkpoint CAS. Model supplies
//! document/lease primitives, not durability. All IDs in this fixture share scope.
//!
//! # Physical keys, unique writers and CAS boundaries
//!
//! The checkpoint is the only execution-authority source; every other key is
//! recovery/effect evidence and must never authorize execution by itself.
//!
//! | 切面 | 物理 key (test Model 文档) | 唯一写者 | CAS/转存边界 |
//! | --- | --- | --- | --- |
//! | 发现目录 | `t1-group-registry` (Sid 行数组) | 受理 Worker (发现先于许可, 无需 owner seal) | 整文档 CAS; 行数 ≤ `max_sids`, 不用 TTL |
//! | 请求意图 (准备/废弃/过期墓碑) | `t1-request-registry` 内条目 `{rid,state,request,head,owner}` | 受理 Worker (绑定预期 head/owner) | 整文档 CAS, 无多键事务; 废弃先栅栏后标记, 墓碑长期可读且在原型中不回收 |
//! | 执行许可+轮次热证据 | checkpoint `runner_state` (CoordinationStore `layout.ckpt(sid)`) | 已 seal 的 shard owner | 精确 `commit_seq` CAS, 受理/终态/压缩/删除同源 |
//! | 冷证明 | `cold:<tid>` 文档 + checkpoint `state["cold"]` 索引 `{tid,digest}` | seal 后的 owner 先转存 | create-if-absent + 读回相符后才允许 checkpoint CAS 压缩热证据; 读回每次重算摘要 |
//! | 归档结果/closure | `archive:<tid>` / `closure:<tid>` | 模拟接收端 / finish 时的 owner | create-if-absent, 内容摘要读回核对 |
//! | 派发效果/停止事实 | `sink:<tid>` / `stopped:<tid>` | 模拟接收端 | create-if-absent, 精确内容 |
//! | attempt 效果/停止事实 | `sink:<tid>#<attempt>` / `stopped:<tid>#<attempt>` | 模拟接收端 | create-if-absent, 身份=encode(key)+attempt; 终态屏障要求每个 attempt 独立核验 |
//! | 失败收尾记录 | `failure:<tid>` | 收尾 Worker (经停止屏障) | create-if-absent, 只含有界 reason, 绝不含超限字节 |
//! | 历史快照 Blob | `t1-history*` | 受理 Worker / 测试夹具 | create-if-absent, 内容寻址, 摘要在 `history_cut` 引用中核验 |
//! | Feed 许可与效果 | checkpoint 记录 `feed_permission` → `feed:<sid>` / `feed-snapshot:<tid>` | 独立 publisher 凭 checkpoint 许可 | 许可随终态 CAS 固定; 效果文档独立 CAS, 不需要执行权 |
//! | 列表投影 | `projection:<sid>` | 列表采集器 | 单调 `seq` CAS, cut 时必须追平 |
//! | 列表快照/游标 | 首页一次性物化 `snapshot:<cut>`, 续页只读该文档 | 列表采集器 (create-if-absent) / 保留释放走版本删 | 续页不重跑实时采集; 释放或过期即明确拒绝, 不静默回退实时视图 |
//! | List 读权限 | `t1-read-acl` 行 `{principal,groups}` | 授权夹具操作 | 首页与每次续页都重验; 撤销后旧游标不再披露 |
//!
//! 过期顺序: 先在请求登记处写墓碑并读回 (期间重试一律 `Expired`), 再以
//! checkpoint CAS 摘除热记录/冷索引, 最后才物理删除 `cold:/archive:/closure:`
//! 文档; 整个删除窗口不存在可重执行间隙。回收只删登记行, 从不删 checkpoint。
#![allow(dead_code, clippy::unwrap_used, clippy::expect_used, reason = "prototype fixture")]
mod lifecycle;
mod feed;
mod listing;
/// Pagination/cursor extensions are `pub` so test targets can name their
/// types directly (`support::pagination::{...}`); sibling supports stay `mod`.
pub mod pagination;
/// S1.3 actual-encoding budget fixtures: `pub` because test targets calibrate
/// exact encoded byte positions (B−1/B/B+1) with the same builders the
/// prototype itself uses for inbound, feed and public frames.
pub mod encoding;
mod reservation;
mod retention;
#[cfg(test)]
mod review_regressions;
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_dist_store::{coordination::CoordinationStore, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::{common::*, ownership::*, session::*, group_round::*};
use bcs_store_api::{document::DocumentStorePlugin, Version, WriteOutcome};
use bcs_test_support::store_model::Model;
use serde_json::{Value, json};
use std::{sync::Arc, time::Duration};

/// Prototype-only capacity knobs. Defaults preserve the pre-existing prototype
/// caps (16/16/16 plus a 128 KiB record window). Tests override them with small
/// explicit limits to exercise saturation and recovery; these numbers are NOT
/// production capacity evidence (production scale/recycling is T2/T4/T7).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Bounds {
    /// Sid rows retained by the bounded discovery directory.
    pub max_sids: usize,
    /// Live rows in the request registry: prepared intents plus expired
    /// tombstones. Abandoned rows are excluded (fence evidence, recycled only
    /// by the explicit gated recycle operation).
    pub max_requests: usize,
    /// Retained rounds per Sid: hot checkpoint records plus cold proofs.
    pub max_rounds_per_sid: usize,
    /// Encoded bytes of one round record, enforced at admit and at its cold
    /// transfer. Oversized records are rejected, never silently truncated.
    pub max_record_bytes: usize,
}
impl Default for Bounds {
    fn default() -> Self {
        Self { max_sids: 16, max_requests: 16, max_rounds_per_sid: 16,
            max_record_bytes: 128 * 1024 }
    }
}
impl Bounds {
    /// Fail-closed physical row cap for the request registry; abandoned rows
    /// are retained fence evidence and are only removed by recycle_abandoned.
    pub const fn max_request_rows(&self) -> usize { self.max_requests + 16 }
}

pub struct Prototype {
    model: Arc<Model>,
    store: CoordinationStore,
    lease: MembershipLease,
    claim: ShardClaim,
    bounds: Bounds,
    /// Logical milliseconds for cursor validity windows. Prototype-only clock:
    /// tests advance it explicitly; production cursor deadlines come from real
    /// clocks (T8), not from this fixture field.
    clock: std::sync::atomic::AtomicU64,
    lose_commit: std::sync::atomic::AtomicBool,
    lose_cold: std::sync::atomic::AtomicBool,
}

fn error(code: &str) -> StoreError { StoreError::Invalid { code: code.into() } }
fn dependency(code: &str) -> StoreError { StoreError::NotApplied { code: code.into() } }
fn encode<T: serde::Serialize>(value: &T) -> Result<Value, StoreError> {
    serde_json::to_value(value).map_err(|_| error("encode"))
}
fn decode<T: serde::de::DeserializeOwned>(value: Value) -> Result<T, StoreError> {
    serde_json::from_value(value).map_err(|_| error("decode"))
}
/// Container identity for the prototype's multi-round record store. The real
/// port ties runner state to exactly one active task; this fixture keeps all
/// bounded round records in one state value instead.
fn records_tid() -> Tid { Tid("s0042.records".into()) }
fn initial() -> Checkpoint {
    Checkpoint { format_version: 1, commit_seq: CommitSeq(0), consumed: None,
        tasks: Default::default(), deadline_history: Default::default(),
        runner_state: Some(TaskRunnerState { tid: records_tid(), state: RunnerState
            { format_version: 1, value: json!({"records": [], "generation": 0, "cold": []}) } }),
        dispatch_seq: Default::default(), deadlines: vec![], view_drafts: Default::default(),
        last_event_id: None, outbox: None }
}
/// Cold-proof index held by the same checkpoint state: `[{tid,digest}]`. Every
/// read from a cold document must re-verify the indexed digest first.
fn cold_list(state: &Value) -> Result<&Vec<Value>, StoreError> {
    state["cold"].as_array().ok_or_else(|| error("cold_index"))
}
fn cold_list_mut(state: &mut Value) -> Result<&mut Vec<Value>, StoreError> {
    state["cold"].as_array_mut().ok_or_else(|| error("cold_index"))
}
fn records(state: &Value) -> Result<&Vec<Value>, StoreError> {
    state["records"].as_array().ok_or_else(|| error("records"))
}
fn records_mut(state: &mut Value) -> Result<&mut Vec<Value>, StoreError> {
    state["records"].as_array_mut().ok_or_else(|| error("records"))
}
/// The record store lives in the checkpoint's runner state; absent state is an
/// error here, never an empty store, so a decode gap cannot look like consent.
fn runner(checkpoint: &Checkpoint) -> Result<&Value, StoreError> {
    checkpoint.runner_state.as_ref().map(|r| &r.state.value)
        .ok_or_else(|| error("runner_state_missing"))
}
fn expected_scope() -> TaskScope {
    TaskScope { tenant: TenantId("tenant".into()), principal: PrincipalId("caller".into()) }
}
/// Immutable default history blob behind every fixture round's `history_cut`;
/// seeded at construction and digest-verified on every dereference. Tests pin
/// additional (e.g. non-empty) history blobs via `encoding::pin_history`.
pub fn history_blobref() -> BlobRef {
    let empty = json!({"entries": []});
    BlobRef { key: "t1-history".into(), digest: lifecycle::digest(&empty).expect("hash") }
}
pub fn request(session: &str, message: &str, generation: u64) -> GroupStartRequest {
    AcceptedRound {
        input: RoundInput { schema_version: RoundSchemaVersion::V1,
            source: GroupRoundSource::GroupA2a,
            key: RoundKey { scope: expected_scope(), group: GroupId("group".into()),
                sid: Sid(format!("s0042.{session}")), tid: Tid(format!("s0042.{session}.{message}")),
                generation: generation.try_into().unwrap() },
            ordered_parts: vec![RoundPart::Text { text: message.into() }],
            workflow_ref: WorkflowRef { definition_id: "flow".into(), revision: "1".into(), digest: Digest("wf".into()) },
            history_cut: history_blobref(),
            execution_deadline: TimestampMillis(100_000) },
        request_id: RequestId(message.into()), request_digest: Digest(format!("digest-{message}")),
        normalization_ref: BlobRef { key: "norm-v1".into(), digest: Digest("norm-digest".into()) },
        return_configuration: json!({"mode":"text"}),
    }
}

impl Prototype {
    pub async fn new() -> Self { Self::with_bounds(Bounds::default()).await }
    pub async fn with_bounds(bounds: Bounds) -> Self {
        Self::from_model(Arc::new(Model::default()), 1, bounds).await
    }
    async fn from_model(model: Arc<Model>, epoch: u64, bounds: Bounds) -> Self {
        let store = CoordinationStore::new(model.clone(), model.clone(), model.clone(),
            KeyLayout::new("t1", "positive").unwrap()).unwrap();
        let me = Instance { node: "worker".into(), incarnation: epoch.to_string() };
        let lease = store.register(me.clone(), RuntimeRole::Worker, "http://worker.example.test".into(),
            Duration::from_secs(600)).await.unwrap();
        let claim = match store.claim(ClaimShard { shard: ShardId(42), me,
            expected_epoch: if epoch == 1 { None } else { Some(WorkerEpoch(epoch-1)) },
            request_id: RequestId(format!("claim-{epoch}")) }).await.unwrap() {
            LeaseClaim::Claimed(c) | LeaseClaim::AlreadyClaimed(c) => c,
            other => panic!("{other:?}"),
        };
        let prototype = Self { model, store, lease, claim, bounds,
            clock: std::sync::atomic::AtomicU64::new(0),
            lose_commit: std::sync::atomic::AtomicBool::new(false),
            lose_cold: std::sync::atomic::AtomicBool::new(false) };
        // Seed the default List read permission for the fixture principal so
        // unauthorized access is the explicit, observable outcome of a revoke.
        prototype.grant_read(&expected_scope().principal.0).await.unwrap();
        // Seed the default (empty) history blob so every round's `history_cut`
        // is a real, digest-verified dereference rather than a dangling ref.
        if prototype.document("t1-history").await.unwrap().is_none() {
            prototype.model.create_document("t1-history", &json!({"entries": []}))
                .await.map_err(|_| dependency("history_seed")).expect("seed history");
        }
        prototype
    }
    pub async fn takeover(&self) -> Self {
        self.store.retire(&self.lease).await.unwrap();
        Self::from_model(self.model.clone(), self.claim.owner.epoch.0 + 1, self.bounds).await
    }
    /// 无活跃 owner 激活: 接任实例只从权威 shard 记录学习上一个 epoch,
    /// 不从垂死实例的内存继承任何状态 (受理与唤醒覆盖同一路径)。
    pub async fn recover(&self) -> Self {
        self.store.retire(&self.lease).await.unwrap();
        let previous = self.store.current(ShardId(42)).await.unwrap()
            .map(|record| record.claim.owner.epoch.0);
        let epoch = previous.map_or(1, |e| e + 1);
        Self::from_model(self.model.clone(), epoch, self.bounds).await
    }
    pub async fn initialize_checkpoint(&self, sid: &Sid) -> Result<(), StoreError> {
        self.acquired(sid).await.map(|_| ())
    }
    pub fn lose_next_commit_reply(&self) {
        self.lose_commit.store(true, std::sync::atomic::Ordering::SeqCst);
    }
    async fn document(&self, key: &str) -> Result<Option<(Version, Value)>, StoreError> {
        self.model.read_document(key).await.map(|v| v.map(|v| (v.version, v.value)))
            .map_err(|_| dependency("document_read"))
    }
    async fn replace(&self, key: &str, old: Option<Version>, value: Value) -> Result<bool, StoreError> {
        let outcome = match old {
            Some(v) => self.model.replace_document(key, v, &value).await,
            None => self.model.create_document(key, &value).await,
        }.map_err(|_| StoreError::OutcomeUnknown { operation_id: key.into() })?;
        Ok(matches!(outcome, WriteOutcome::Applied { .. }))
    }
    fn validate(&self, key: &RoundKey) -> Result<(), StoreError> {
        if key.scope != expected_scope() || key.group.0 != "group" { return Err(error("scope")); }
        if KeyLayout::shard_of(&key.sid)? != ShardId(42) { return Err(error("shard")); }
        Ok(())
    }
    async fn acquired(&self, sid: &Sid) -> Result<AcquiredSession, StoreError> {
        match self.store.acquire(sid, &self.claim, initial()).await? {
            AcquireOutcome::Acquired(a) => Ok(a),
            _ => Err(dependency("fenced")),
        }
    }
    async fn commit(&self, a: AcquiredSession, state: Value, operation: &str) -> Result<(), StoreError> {
        let mut next = a.envelope.checkpoint;
        let expected = next.commit_seq;
        next.commit_seq = CommitSeq(expected.0.checked_add(1).ok_or_else(|| error("overflow"))?);
        next.runner_state.as_mut().map(|r| r.state.value = state)
            .ok_or_else(|| error("runner_state_missing"))?;
        let id = CommitId(format!("{operation}-{}", next.commit_seq.0));
        let digest = Digest(serde_json::to_string(&next.runner_state).map_err(|_| error("encode"))?);
        next.outbox = Some(CommittedBatch { created_at: None, commit_id: id.clone(),
            source_commit: next.commit_seq, content_digest: digest.clone(), effects: vec![] });
        if self.lose_commit.swap(false, std::sync::atomic::Ordering::SeqCst) {
            self.model.lose_next_response();
        }
        match self.store.commit(CommitRequest { purpose: CommitPurpose::Ordinary { control_revision: 0 },
            owner: a.ownership, expected_commit_seq: expected,
            commit_id: id, content_digest: digest, candidate: next }).await? {
            CommitOutcome::Committed(_) | CommitOutcome::AlreadyCommitted(_) => Ok(()),
            _ => Err(dependency("commit_conflict")),
        }
    }
    /// Registry is written before any checkpoint mutation, in this isolated scope.
    /// It is finite; directory rows are only freed by the explicit fenced
    /// recycle operation and saturation fails closed.
    pub async fn prepare(&self, request: &GroupStartRequest) -> Result<(), StoreError> {
        self.validate(&request.input.key)?;
        if let Some(envelope) = self.store.load(&request.input.key.sid).await? {
            if runner(&envelope.checkpoint)?["directory_retired"] == true {
                return Err(dependency("session_retired"));
            }
        }
        for _ in 0..8 {
            let old = self.document("t1-group-registry").await?;
            let (version, mut rows) = match old { Some((v,x)) => (Some(v), x), None => (None, json!([])) };
            let rows_array = rows.as_array_mut().ok_or_else(|| error("registry"))?;
            let sid = &request.input.key.sid.0;
            if rows_array.iter().any(|v| v.as_str() == Some(sid)) { return Ok(()); }
            if rows_array.len() >= self.bounds.max_sids { return Err(dependency("registry_capacity")); }
            rows_array.push(json!(sid));
            if self.replace("t1-group-registry", version, rows).await? { return Ok(()); }
        }
        Err(dependency("registry_contention"))
    }
    async fn find_request(&self, request: &GroupStartRequest) -> Result<Option<AcceptedRound>, StoreError> {
        self.validate(&request.input.key)?;
        let Some((_, registry)) = self.document("t1-group-registry").await? else { return Ok(None) };
        for sid in registry.as_array().ok_or_else(|| error("registry"))? {
            let sid = Sid(sid.as_str().ok_or_else(|| error("sid"))?.into());
            let Some(e) = self.store.load(&sid).await? else { continue };
            let state = runner(&e.checkpoint)?;
            for record in records(state)? {
                let accepted: AcceptedRound = decode(record["accepted"].clone())?;
                if accepted.request_id == request.request_id { return Ok(Some(accepted)); }
            }
            // 压缩后的求证走冷证明, 每次读回重算索引摘要。
            for entry in cold_list(state)? {
                let Some(facts) = self.read_cold(entry).await? else { continue };
                if facts.accepted.request_id == request.request_id { return Ok(Some(facts.accepted)); }
            }
        }
        Ok(None)
    }
    pub async fn submit(&self, request: &GroupStartRequest, authorized: bool, current_supported: bool)
        -> Result<Admission, StoreError>
    {
        if !authorized { return Err(error("unauthorized")); }
        let identity=GroupRequestIdentity::strict(&request.input.key.scope,&request.input.key.group,&request.request_id);
        match self.lookup(&identity).await? {
            GroupRequestRecord::Accepted(_) | GroupRequestRecord::Prepared(_) => return self.admit(request).await,
            GroupRequestRecord::Expired => return Ok(Admission::Expired),
            GroupRequestRecord::Absent => (),
        }
        if !current_supported { return Err(error("capability")); }
        self.admit(request).await
    }
}
fn matching<'a>(state: &'a mut Value, key: &RoundKey) -> Result<&'a mut Value, StoreError> {
    let encoded = encode(key)?;
    records_mut(state)?.iter_mut().find(|r| r["accepted"]["input"]["key"] == encoded)
        .ok_or_else(|| error("round_missing"))
}
#[async_trait]
impl GroupRoundRepo for Prototype {
    async fn lookup(&self, identity: &GroupRequestIdentity<'_>) -> Result<GroupRequestRecord, StoreError> {
        let parts=identity.components();
        if parts[1]!="tenant" || parts[2]!="caller" || parts[3]!="group" { return Err(error("scope")); }
        let Some(entry)=self.request_entry(&parts[4]).await? else { return Ok(GroupRequestRecord::Absent) };
        // 废弃=未占用; 过期墓碑先读回, 不进入任何求证路径。
        if entry["state"]=="abandoned" { return Ok(GroupRequestRecord::Absent); }
        if entry["state"]=="expired" { return Ok(GroupRequestRecord::Expired); }
        let request:AcceptedRound=decode(entry["request"].clone())?;
        if let Some(accepted)=self.find_request(&request).await? { return Ok(GroupRequestRecord::Accepted(accepted)); }
        Ok(GroupRequestRecord::Prepared(request))
    }
    async fn admit(&self, request: &GroupStartRequest) -> Result<Admission, StoreError> {
        self.validate(&request.input.key)?;
        // 墓碑先于一切读回: 结果过期不能再以任何路径重跑。
        if self.request_entry(&request.request_id.0).await?
            .is_some_and(|entry| entry["state"]=="expired") { return Ok(Admission::Expired); }
        if let Some(original) = self.find_request(request).await? {
            return Ok(if original.request_digest == request.request_digest
                && original.input.key.sid == request.input.key.sid {
                Admission::Replay { accepted: original }
            } else { Admission::Conflict });
        }
        // Force the real read/CAS interleaving that an in-memory ready future
        // otherwise hides. This is a scheduler yield, not a synchronization lock.
        tokio::task::yield_now().await;
        self.prepare(request).await?;
        let a = self.acquired(&request.input.key.sid).await?;
        let state = runner(&a.envelope.checkpoint)?.clone();
        // A winner may have committed after the initial scope-wide lookup.
        for record in records(&state)? {
            let original: AcceptedRound = decode(record["accepted"].clone())?;
            if original.request_id == request.request_id {
                return Ok(if original.request_digest == request.request_digest {
                    Admission::Replay { accepted: original }
                } else { Admission::Conflict });
            }
        }
        if records(&state)?.iter().any(|r| r["terminal"].is_null()) { return Ok(Admission::Busy); }
        if state["generation"].as_u64().and_then(|v| v.checked_add(1)) != Some(request.input.key.generation.get()) {
            return Err(error("stale_generation"));
        }
        // 热记录与冷证明合计占用每 Sid 保留轮次配额。
        if records(&state)?.len() + cold_list(&state)?.len() >= self.bounds.max_rounds_per_sid {
            return Err(dependency("round_capacity"));
        }
        // 单记录字节量有界: 超限在提交前拒绝, 不静默截断。
        let candidate = json!({"accepted":request,"status_seq":0,"terminal":null,
            "dispatch_intent":false,"dispatched":false,"append":false,"published":false});
        if serde_json::to_vec(&candidate).map_err(|_| error("encode"))?.len() > self.bounds.max_record_bytes {
            return Err(dependency("record_budget"));
        }
        // Reserve and commit against one exact checkpoint head. Conflict
        // classification reads authority again; a pending intent is not Busy.
        let reserved = match self.reserve_only(request).await {
            Ok(reserved) => reserved,
            Err(e) => {
                if let Some(original) = self.find_request(request).await? {
                    return Ok(self.replay(request, original));
                }
                return Err(e);
            }
        };
        self.commit_reserved(&reserved).await
    }
    async fn get(&self, key: &RoundKey) -> Result<Option<GroupRoundView>, StoreError> {
        if self.validate(key).is_err() { return Ok(None); }
        let Some(e) = self.store.load(&key.sid).await? else { return Ok(None) };
        let key_value = encode(key)?;
        let state = runner(&e.checkpoint)?;
        let Some(r) = records(state)?.iter()
            .find(|r| r["accepted"]["input"]["key"] == key_value) else {
            // 压缩后的轮次只能从冷证明求值, 索引摘要不符即读失败。
            return self.cold_view(key, state).await;
        };
        let terminal: Option<TerminalProof> = decode(r["terminal"].clone())?;
        let result = if let Some(t) = &terminal {
            let (_, value) = self.document(&t.result_ref.key).await?.ok_or_else(|| dependency("archive_missing"))?;
            if value["key"] != key_value || lifecycle::digest(&value["result"])? != t.result_ref.digest {
                return Err(error("archive_mismatch"));
            }
            let (_,closure)=self.document(&t.closure_ref.key).await?.ok_or_else(||dependency("closure_missing"))?;
            if closure["key"] != key_value || lifecycle::digest(&closure)? != t.closure_ref.digest {
                return Err(error("closure_mismatch"));
            }
            Some(Payload::Inline(value["result"].clone()))
        } else { None };
        Ok(Some(GroupRoundView { accepted: decode(r["accepted"].clone())?, terminal, result }))
    }
}
