//! Cold-proof transfer, checkpoint compaction, expiry tombstones and fenced
//! recycling. All operations sit on the test Model documents plus the single
//! checkpoint CAS authority; production SQL/blob GC is T2/T4/T7, not here.
//! Delete order is contract-mandated: tombstone readable first, then the
//! checkpoint CAS drops hot/cold evidence, then stored proof documents go; no
//! step in that window may let an expired round re-execute.
use super::*;
use super::lifecycle::digest;
use bcs_store_api::json::{JsonMutation, JsonStorePlugin};
use bcs_store_api::{Expiry, WriteCondition};

/// Verified cold proof facts. Every read of a cold document re-computes the
/// indexed digest; a corrupted document is an error, never accepted evidence.
pub(super) struct ColdFacts {
    pub content: Value,
    pub accepted: AcceptedRound,
    pub terminal: Option<TerminalProof>,
    pub permission: Option<Value>,
}
impl Prototype {
    /// Read one cold proof through its checkpoint cold-index entry, verifying
    /// the stored content against the index digest before use. Missing is
    /// None (the entry may name a not-yet-written document only mid-transfer).
    pub(super) async fn read_cold(&self, entry: &Value) -> Result<Option<ColdFacts>, StoreError> {
        let tid = entry["tid"].as_str().ok_or_else(|| error("cold_index"))?;
        let indexed = entry["digest"].as_str().ok_or_else(|| error("cold_index"))?;
        let Some((_, content)) = self.document(&format!("cold:{tid}")).await? else {
            return Ok(None);
        };
        if digest(&content)?.0 != indexed { return Err(error("cold_digest")); }
        Ok(Some(ColdFacts {
            accepted: decode(content["accepted"].clone())?,
            terminal: decode(content["terminal"].clone())?,
            permission: if content["feed_permission"].is_null() { None }
                else { Some(content["feed_permission"].clone()) },
            content,
        }))
    }
    /// Resolve a compacted round through its cold proof only; a hot record
    /// must never shadow the digest check.
    pub(super) async fn cold_view(&self, key: &RoundKey, state: &Value)
        -> Result<Option<GroupRoundView>, StoreError>
    {
        let Some(entry) = cold_list(state)?.iter().find(|entry| entry["tid"] == key.tid.0) else {
            return Ok(None);
        };
        let key_value = encode(key)?;
        let facts = self.read_cold(entry).await?.ok_or_else(|| dependency("cold_missing"))?;
        if facts.content["key"] != key_value { return Err(error("cold_scope")); }
        let terminal = facts.terminal.clone();
        let result = if let Some(t) = &terminal {
            let (_, value) = self.document(&t.result_ref.key).await?
                .ok_or_else(|| dependency("archive_missing"))?;
            if value["key"] != key_value || digest(&value["result"])? != t.result_ref.digest {
                return Err(error("archive_mismatch"));
            }
            let (_, closure) = self.document(&t.closure_ref.key).await?
                .ok_or_else(|| dependency("closure_missing"))?;
            if closure["key"] != key_value || digest(&closure)? != t.closure_ref.digest {
                return Err(error("closure_mismatch"));
            }
            Some(Payload::Inline(value["result"].clone()))
        } else { None };
        Ok(Some(GroupRoundView { accepted: facts.accepted, terminal, result }))
    }
    /// 冷转存: owner 先把证明按 Tid 写入冷文档, 读回相符后才允许 checkpoint
    /// CAS 压缩热证据; 之后所有求值都必须经冷摘要核对。
    pub async fn compact(&self, key: &RoundKey) -> Result<(), StoreError> {
        self.validate(key)?;
        let a = self.acquired(&key.sid).await?;
        let mut state = runner(&a.envelope.checkpoint)?.clone();
        if cold_list(&state)?.iter().any(|entry| entry["tid"] == key.tid.0) { return Ok(()); }
        let (accepted, terminal, permission) = {
            let r = matching(&mut state, key)?;
            if r["terminal"].is_null() { return Err(error("compact_not_terminal")); }
            (r["accepted"].clone(), r["terminal"].clone(), r["feed_permission"].clone())
        };
        let content = json!({"key":key,"accepted":accepted,"terminal":terminal,
            "feed_permission":permission});
        if serde_json::to_vec(&content).map_err(|_| error("encode"))?.len()
            > self.bounds.max_record_bytes {
            return Err(dependency("record_budget"));
        }
        let path = format!("cold:{}", key.tid.0);
        if self.lose_cold.swap(false, std::sync::atomic::Ordering::SeqCst) {
            self.model.lose_next_response();
        }
        match self.document(&path).await? {
            Some((_, old)) => if old != content { return Err(error("cold_conflict")); },
            None => { if !self.replace(&path, None, content.clone()).await? {
                return Err(dependency("cold_race"));
            } }
        }
        // 读回确认: persisted bytes must match the hot evidence before the
        // checkpoint may drop it. A lost reply leaves compaction undecided.
        let (_, readback) = self.document(&path).await?.ok_or_else(|| dependency("cold_missing"))?;
        if readback != content { return Err(error("cold_readback")); }
        let proof_digest = digest(&content)?;
        cold_list_mut(&mut state)?.push(json!({"tid":key.tid.0,"digest":proof_digest.0}));
        let encoded_key = encode(key)?;
        records_mut(&mut state)?.retain(|r| r["accepted"]["input"]["key"] != encoded_key);
        self.commit(a, state, "compact").await
    }
    /// 过期第一步: 墓碑写入请求登记行并保持可读; 从此同请求一律 Expired。
    pub async fn expire_tombstone(&self, key: &RoundKey) -> Result<(), StoreError> {
        self.validate(key)?;
        let _ = self.acquired(&key.sid).await?;
        let view = self.get(key).await?.ok_or_else(|| error("round_missing"))?;
        if view.terminal.is_none() { return Err(error("expire_not_terminal")); }
        self.mark_request_expired(&view.accepted.request_id.0).await
    }
    /// 过期第二步: 以 checkpoint CAS 摘除热记录/冷索引, 之后才物理删除
    /// 冷证明与归档/closure 文档。墓碑不在删除范围内。
    pub async fn expire_cleanup(&self, key: &RoundKey) -> Result<(), StoreError> {
        self.validate(key)?;
        let a = self.acquired(&key.sid).await?;
        let mut state = runner(&a.envelope.checkpoint)?.clone();
        let encoded_key = encode(key)?;
        let rid = match records_mut(&mut state)?.iter_mut().find(|r|
            r["accepted"]["input"]["key"] == encoded_key) {
            Some(r) => decode::<AcceptedRound>(r["accepted"].clone())?.request_id.0,
            None => {
                let entry = cold_list(&state)?.iter().find(|e| e["tid"] == key.tid.0)
                    .ok_or_else(|| error("round_missing"))?;
                self.read_cold(entry).await?.ok_or_else(|| dependency("cold_missing"))?
                    .accepted.request_id.0
            }
        };
        // 墓碑先可读, 否则进入删除即违规。
        match self.request_entry(&rid).await? {
            Some(entry) if entry["state"] == "expired" => (),
            Some(_) => return Err(dependency("tombstone_missing")),
            None => return Err(dependency("request_missing")),
        }
        // 大产物引用 pin: 带 digest 引用的发布物证在 append+publish 完成
        // 前保持 pin, 过期清理拒绝物理删除 (无依据不解锁)。
        let permit = self.feed_permission(key).await?;
        if encoding::permission_has_digest_refs(&permit)
            && !self.publication_complete(key, &permit).await? {
            return Err(dependency("archive_pinned"));
        }
        cold_list_mut(&mut state)?.retain(|e| e["tid"] != key.tid.0);
        records_mut(&mut state)?.retain(|r| r["accepted"]["input"]["key"] != encoded_key);
        self.commit(a, state, "expire-cleanup").await?;
        // 后删证明/结果; 删除不能影响重跑语义 (墓碑保持可读)。
        self.delete_doc(&format!("cold:{}", key.tid.0)).await?;
        self.delete_doc(&format!("archive:{}", key.tid.0)).await?;
        self.delete_doc(&format!("closure:{}", key.tid.0)).await
    }
    /// 目录回收: 只有当前 sealed owner 可以回收一个 Sid 行, 且要求该 Sid 没有
    /// 残留轮次、没有仍指向它的未决预留; 不删 checkpoint, 不依赖 TTL。
    pub async fn recycle_directory(&self, sid: &Sid) -> Result<(), StoreError> {
        if KeyLayout::shard_of(sid)? != ShardId(42) { return Err(error("shard")); }
        let a = self.acquired(sid).await?;
        let state = runner(&a.envelope.checkpoint)?;
        if !records(state)?.is_empty() || !cold_list(state)?.is_empty() {
            return Err(dependency("recycle_pending"));
        }
        if let Some((_, rows)) = self.request_registry().await? {
            for row in &rows {
                if row["state"] != "prepared" { continue; }
                let request: AcceptedRound = decode(row["request"].clone())?;
                if request.input.key.sid.0 == sid.0 { return Err(dependency("recycle_pending")); }
            }
        }
        for _ in 0..8 {
            let old = self.document("t1-group-registry").await?;
            let (version, rows) = match old {
                Some((version, value)) => (Some(version),
                    value.as_array().cloned().ok_or_else(|| error("registry"))?),
                None => return Ok(()),
            };
            if !rows.iter().any(|row| row == &json!(sid.0)) { return Ok(()); }
            let retained: Vec<Value> = rows.into_iter().filter(|row| row != &json!(sid.0)).collect();
            if self.replace("t1-group-registry", version, Value::Array(retained)).await? {
                return Ok(());
            }
        }
        Err(dependency("registry_contention"))
    }
    /// 请求登记的物理回收: 只删除能被证明已栅栏阻断的废弃行 (owner 或 head
    /// 与其预留时不同), 未证栅栏的行保留; 墓碑行永不由它回收。
    pub async fn recycle_abandoned(&self) -> Result<usize, StoreError> {
        for _ in 0..8 {
            let Some((version, rows)) = self.request_registry().await? else { return Ok(0) };
            let mut removed = 0;
            let mut retained = vec![];
            for row in &rows {
                if row["state"] != "abandoned" { retained.push(row.clone()); continue; }
                let request: AcceptedRound = decode(row["request"].clone())?;
                let stale = match self.store.load(&request.input.key.sid).await? {
                    Some(e) => {
                        let head = row["head"].as_u64().unwrap_or(u64::MAX);
                        let owner: ShardOwner = decode(row["owner"].clone())?;
                        head != e.checkpoint.commit_seq.0 || owner != e.owner
                    }
                    None => true,
                };
                if stale { removed += 1 } else { retained.push(row.clone()) }
            }
            if removed == 0 { return Ok(0) }
            if self.write_registry(Some(version), retained).await? { return Ok(removed); }
        }
        Err(dependency("request_contention"))
    }
    /// 无 hint 自动发现: 从有界目录和权威 checkpoint 推导全部待执行 RoundKey
    /// (已受理、未终态、未派发), 调用方不注入任何已知 Sid。
    pub async fn pending_work(&self) -> Result<Vec<RoundKey>, StoreError> {
        let mut work = vec![];
        for sid in self.directory_sids().await? {
            let sid = Sid(sid.into());
            let Some(e) = self.store.load(&sid).await? else { continue };
            let state = runner(&e.checkpoint)?;
            for r in records(state)? {
                if !r["terminal"].is_null() || r["dispatched"] == true { continue; }
                let accepted: AcceptedRound = decode(r["accepted"].clone())?;
                work.push(accepted.input.key);
            }
        }
        Ok(work)
    }
    /// Bounded Sid directory rows, test-facing (directory scan only, no hints).
    pub(super) async fn directory_sids(&self) -> Result<Vec<String>, StoreError> {
        match self.document("t1-group-registry").await? {
            None => Ok(vec![]),
            Some((_, value)) => value.as_array().map(|rows| rows.iter()
                .filter_map(|row| row.as_str().map(str::to_string)).collect())
                .ok_or_else(|| error("registry")),
        }
    }
    // ---- bounded-capacity probes and fault injection (test fixtures) ----
    pub async fn hot_round_count(&self, sid: &Sid) -> Result<usize, StoreError> {
        match self.store.load(sid).await? {
            Some(e) => Ok(records(runner(&e.checkpoint)?)?.len()),
            None => Ok(0),
        }
    }
    pub async fn cold_round_count(&self, sid: &Sid) -> Result<usize, StoreError> {
        match self.store.load(sid).await? {
            Some(e) => Ok(cold_list(runner(&e.checkpoint)?)?.len()),
            None => Ok(0),
        }
    }
    pub async fn request_registry_rows(&self) -> Result<usize, StoreError> {
        Ok(self.request_registry().await?.map(|(_, rows)| rows.len()).unwrap_or(0))
    }
    pub fn lose_next_document_reply(&self) {
        self.lose_cold.store(true, std::sync::atomic::Ordering::SeqCst);
    }
    pub async fn corrupt_cold(&self, key: &RoundKey) -> Result<(), StoreError> {
        let path = format!("cold:{}", key.tid.0);
        let tampered = json!({"tampered":true});
        let applied = match self.document(&path).await? {
            Some((version, _)) => self.replace(&path, Some(version), tampered).await?,
            None => self.replace(&path, None, tampered).await?,
        };
        if !applied { return Err(dependency("cold_race")); }
        Ok(())
    }
    pub async fn repair_cold(&self, key: &RoundKey) -> Result<(), StoreError> {
        self.delete_doc(&format!("cold:{}", key.tid.0)).await
    }
    // ---- internal helpers ----
    /// Version-gated physical deletion (test Model only). A tombstone must be
    /// readable before any proof document reaches this path; a failed delete
    /// propagates as an error, never a silent success.
    pub(super) async fn delete_doc(&self, path: &str) -> Result<(), StoreError> {
        let Some((version, _)) = self.document(path).await? else { return Ok(()) };
        let outcome = self.model.mutate_json(path, WriteCondition::Version(version),
            JsonMutation::Delete, Expiry::Persistent).await;
        match outcome {
            Ok(WriteOutcome::Applied { .. }) => Ok(()),
            Ok(_) => Err(error("delete_conflict")),
            Err(_) => Err(StoreError::OutcomeUnknown { operation_id: path.into() }),
        }
    }
}