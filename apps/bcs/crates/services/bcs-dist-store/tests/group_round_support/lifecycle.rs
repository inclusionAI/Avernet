use super::*;
use bcs_service_api::port::repo::distributed::group_round::RoundBudgetProfile;
use sha2::{Digest as _, Sha256};

/// Bounded failure reasons must themselves stay bounded: the failure record
/// carries the reason only, never oversized result bytes.
pub const MAX_FAILURE_REASON_BYTES: usize = 1024;

pub(super) fn digest(value: &Value) -> Result<Digest, StoreError> {
    let bytes = serde_json::to_vec(value).map_err(|_| error("encode"))?;
    Ok(Digest(format!("{:x}", Sha256::digest(bytes))))
}
fn permission_tail(state: &Value, key: &RoundKey) -> Result<Value, StoreError> {
    let encoded = encode(key)?;
    let record = records(state)?.iter().find(|r| r["accepted"]["input"]["key"] == encoded)
        .ok_or_else(|| error("round_missing"))?;
    Ok(record["feed_permission"]["snapshot"]["snapshot_event_id"].clone())
}

impl Prototype {
    /// Simulated external receiver: durable idempotent accept keyed by full round.
    /// This proves a prototype effect executes; it is NOT a real A2A Connector.
    pub async fn dispatch(&self, key: &RoundKey) -> Result<(), StoreError> {
        self.validate(key)?;
        let a = self.acquired(&key.sid).await?;
        let mut state = runner(&a.envelope.checkpoint)?.clone();
        let r = matching(&mut state, key)?;
        if !r["terminal"].is_null() { return Err(error("terminal_dispatch")); }
        r["dispatch_intent"] = json!(true);
        let sink = format!("sink:{}", key.tid.0);
        let content = json!({"key": key, "input": r["accepted"]["input"]});
        self.commit(a,state,"dispatch-intent").await?;
        let a=self.acquired(&key.sid).await?;
        let mut state=runner(&a.envelope.checkpoint)?.clone();
        let r=matching(&mut state,key)?;
        match self.document(&sink).await? {
            Some((_, previous)) if previous != content => return Err(error("sink_conflict")),
            Some(_) => (),
            None => { if !self.replace(&sink, None, content).await? { return Err(dependency("sink_race")); } }
        }
        r["dispatched"] = json!(true);
        self.commit(a, state, "dispatch").await
    }
    pub async fn dispatch_without_ack(&self, key: &RoundKey) -> Result<(), StoreError> {
        self.validate(key)?;
        let a = self.acquired(&key.sid).await?;
        let mut state = runner(&a.envelope.checkpoint)?.clone();
        let r = matching(&mut state, key)?;
        if !r["terminal"].is_null() { return Err(error("terminal_dispatch")); }
        r["dispatch_intent"] = json!(true);
        let sink = format!("sink:{}", key.tid.0);
        let content = json!({"key": key, "input": r["accepted"]["input"]});
        self.commit(a,state,"dispatch-intent").await?;
        if !self.replace(&sink,None,content).await? { return Err(dependency("sink_race")); }
        Ok(())
    }
    pub async fn corrupt_stop(&self,key:&RoundKey)->Result<(),StoreError> {
        self.replace(&format!("stopped:{}",key.tid.0),None,json!({"wrong":true})).await?; Ok(())
    }
    pub async fn restore_stop(&self,key:&RoundKey)->Result<(),StoreError> {
        let path=format!("stopped:{}",key.tid.0);
        let (v,_)=self.document(&path).await?.unwrap();
        self.replace(&path,Some(v),encode(key)?).await?; Ok(())
    }
    pub async fn corrupt_result(&self,key:&RoundKey)->Result<(),StoreError> {
        let path=format!("archive:{}",key.tid.0);
        let (v,mut content)=self.document(&path).await?.unwrap(); content["result"]=json!("corrupt");
        self.replace(&path,Some(v),content).await?; Ok(())
    }
    pub async fn archive(&self, key: &RoundKey, text: &str) -> Result<(), StoreError> {
        self.archive_value(key, json!(text)).await
    }
    pub async fn finish(&self, key: &RoundKey) -> Result<(), StoreError> {
        self.finish_profiled(key, &RoundBudgetProfile::v1()).await
    }
    /// Finish within an explicit encoding profile (default = candidate v1).
    /// 完整 wire 表示与内部 Feed 外壳必须在提交 Completed 之前全部可编码;
    /// 超限一律进入有界失败收尾 (B+1 永不公开 Completed)。
    pub async fn finish_profiled(&self, key: &RoundKey,
        profile: &RoundBudgetProfile) -> Result<(), StoreError> {
        self.validate(key)?;
        let a = self.acquired(&key.sid).await?;
        let mut state = runner(&a.envelope.checkpoint)?.clone();
        let previous = state.get("feed_tail").filter(|v| !v.is_null()).cloned();
        let r = matching(&mut state, key)?;
        if !r["terminal"].is_null() { return Ok(()); }
        let archive_key = format!("archive:{}", key.tid.0);
        let (_, archive) = self.document(&archive_key).await?.ok_or_else(|| dependency("archive_missing"))?;
        if archive["key"] != encode(key)? { return Err(error("archive_scope")); }
        let result_digest = digest(&archive["result"])?;
        if archive["digest"] != json!(result_digest.0) { return Err(error("archive_digest")); }
        // Fixture completion is explicitly recorded by the simulated receiver,
        // not inferred from having sent a request. Tests can leave it unresolved.
        // 统一停止屏障: 每个 dispatch 过的 attempt 都要独立核验的停止事实。
        self.assert_all_stopped(&r, key).await?;
        let seq = a.envelope.checkpoint.commit_seq.0 + 1;
        // 完整 wire 表示先于 Completed 校验: B+1 一律进入有界失败收尾。
        let preview = encoding::frame_value(
            &r["accepted"]["input"]["ordered_parts"], &json!([]),
            &archive["result"], key, "completed", seq)?;
        if encoding::encoded_len(&preview)? > profile.public_frame_bytes {
            return self.fail_with_reason(key, "public_frame_budget").await;
        }
        let closure_key = format!("closure:{}", key.tid.0);
        let closure = json!({"key":key,"stopped":true,"dispatched":r["dispatched"]});
        let closure_digest = digest(&closure)?;
        let store = BlobRef { key: archive_key.clone(), digest: result_digest.clone() };
        let proof=TerminalProof { schema_version: RoundSchemaVersion::V1,
            key:key.clone(), commit_id:CommitId(format!("terminal-{seq}")), commit_seq:CommitSeq(seq),
            status:TerminalStatus::Completed, result_ref:store.clone(),
            closure_ref:BlobRef { key:closure_key.clone(),digest:closure_digest } };
        // 校验先于证明写: 有界失败收尾不会再与已写的 closure 冲突。
        let budgets = encoding::WireBudgets::from_profile(profile);
        let permission = match feed::permission(key, &proof,
            Some((&archive["result"], &store)), "completed", None,
            &store, previous, &budgets) {
            Ok(permit) => permit,
            Err(e) => {
                let reason = match e {
                    StoreError::Invalid { code } | StoreError::NotApplied { code } => code,
                    _ => "feed_encoding".into(),
                };
                return self.fail_with_reason(key, &reason).await;
            }
        };
        if let Some((_, old)) = self.document(&closure_key).await? {
            if old != closure { return Err(error("closure_conflict")); }
        } else if !self.replace(&closure_key, None, closure).await? { return Err(dependency("closure_race")); }
        r["feed_permission"]=permission;
        r["terminal"]=encode(&proof)?;
        state["feed_tail"] = permission_tail(&state, key)?;
        // Retained record is part of the same checkpoint CAS: both proof and
        // append/publish obligations survive the next head and owner seal.
        self.commit(a,state,"terminal").await
    }
    pub async fn stop_remote(&self, key: &RoundKey) -> Result<(), StoreError> {
        let path = format!("stopped:{}",key.tid.0);
        let content = encode(key)?;
        match self.document(&path).await? {
            Some((_, old)) if old == content => Ok(()),
            Some(_) => Err(error("stop_identity")),
            None => if self.replace(&path,None,content).await? { Ok(()) } else { Err(dependency("stop_race")) },
        }
    }
    pub async fn append_feed(&self, key: &RoundKey) -> Result<(), StoreError> {
        let permit=self.feed_permission(key).await?;
        self.append_permitted(&permit).await
    }
    pub async fn publish_snapshot(&self,key:&RoundKey) -> Result<(),StoreError> {
        let permit=self.feed_permission(key).await?;
        self.publish_permitted(&permit).await
    }
    pub async fn deliveries(&self) -> Result<Vec<RoundKey>,StoreError> {
        let mut delivered=vec![];
        for key in self.keys().await? {
            if self.document(&format!("sink:{}",key.tid.0)).await?.is_some() { delivered.push(key); }
        }
        Ok(delivered)
    }
    /// 统一停止屏障: 受理后派发过的每个 attempt 都要独立、经身份核验的停止事实;
    /// 未记 attempt 的旧式整轮派发仍核对整轮停止文档。
    pub(super) async fn assert_all_stopped(&self, record: &Value,
        key: &RoundKey) -> Result<(), StoreError> {
        if let Some(attempts) = record["attempts"].as_array() {
            for id in attempts {
                let Some(id) = id.as_str() else { return Err(error("attempt_id")); };
                let path = format!("stopped:{}#{}", key.tid.0, id);
                let Some((_, fact)) = self.document(&path).await? else {
                    return Err(dependency("stop_unconfirmed"));
                };
                if fact != json!({"key": key, "attempt": id}) {
                    return Err(dependency("stop_unconfirmed"));
                }
            }
            return Ok(());
        }
        if record["dispatch_intent"] == true {
            let stopped = self.document(&format!("stopped:{}", key.tid.0)).await?
                .map(|(_, v)| v);
            if stopped != Some(encode(key)?) { return Err(dependency("stop_unconfirmed")); }
        }
        Ok(())
    }
    /// Attempt-scoped dispatch fixture: records the in-flight attempt ids in
    /// the checkpoint record and writes one per-attempt receiver effect
    /// document, so the terminal barrier can demand an independent,
    /// identity-verified stop fact per attempt.
    pub async fn dispatch_attempts(&self, key: &RoundKey, ids: &[&str]) -> Result<(), StoreError> {
        self.validate(key)?;
        let a = self.acquired(&key.sid).await?;
        let mut state = runner(&a.envelope.checkpoint)?.clone();
        let r = matching(&mut state, key)?;
        if !r["terminal"].is_null() { return Err(error("terminal_dispatch")); }
        r["dispatch_intent"] = json!(true);
        r["attempts"] = json!(ids);
        self.commit(a, state, "dispatch-attempt-intents").await?;
        for id in ids {
            let path = format!("sink:{}#{}", key.tid.0, id);
            let content = json!({"key": key, "attempt": id});
            match self.document(&path).await? {
                Some((_, previous)) if previous != content => return Err(error("sink_conflict")),
                Some(_) => (),
                None => if !self.replace(&path, None, content).await? {
                    return Err(dependency("sink_race"));
                },
            }
        }
        let a = self.acquired(&key.sid).await?;
        let mut state = runner(&a.envelope.checkpoint)?.clone();
        matching(&mut state, key)?["dispatched"] = json!(true);
        self.commit(a, state, "dispatch-attempts").await
    }
    /// Independently persisted, identity-verified per-attempt stop fact.
    /// Existing correct content is idempotent; a conflicting document at the
    /// same path is an identity error, never overwritten.
    pub async fn stop_attempt(&self, key: &RoundKey, id: &str) -> Result<(), StoreError> {
        self.validate(key)?;
        let path = format!("stopped:{}#{}", key.tid.0, id);
        let content = json!({"key": key, "attempt": id});
        match self.document(&path).await? {
            Some((_, old)) if old == content => Ok(()),
            Some(_) => Err(error("stop_identity")),
            None => if self.replace(&path, None, content).await? { Ok(()) }
                else { Err(dependency("stop_race")) },
        }
    }
    /// Fault injection: replace an attempt stop fact with wrong content.
    pub async fn corrupt_attempt_stop(&self, key: &RoundKey, id: &str) -> Result<(), StoreError> {
        self.validate(key)?;
        let path = format!("stopped:{}#{}", key.tid.0, id);
        let content = json!({"wrong": true});
        let applied = match self.document(&path).await? {
            Some((version, _)) => self.replace(&path, Some(version), content).await?,
            None => self.replace(&path, None, content).await?,
        };
        if !applied { return Err(dependency("stop_race")); }
        Ok(())
    }
    /// Recovery fixture: re-write the authoritative stop-identity content over
    /// a corrupted attempt stop fact (mirrors `restore_stop` for rounds).
    pub async fn restore_attempt_stop(&self, key: &RoundKey, id: &str) -> Result<(), StoreError> {
        self.validate(key)?;
        let path = format!("stopped:{}#{}", key.tid.0, id);
        let content = json!({"key": key, "attempt": id});
        let applied = match self.document(&path).await? {
            Some((version, _)) => self.replace(&path, Some(version), content).await?,
            None => self.replace(&path, None, content).await?,
        };
        if !applied { return Err(dependency("stop_race")); }
        Ok(())
    }
    /// Test observation: the raw stored stop fact (identity) for one attempt.
    pub async fn attempt_stop_fact(&self, key: &RoundKey, id: &str) -> Result<Option<Value>, StoreError> {
        self.validate(key)?;
        Ok(self.document(&format!("stopped:{}#{}", key.tid.0, id)).await?
            .map(|(_, v)| v))
    }
    /// Bounded failure finalization: independently verified stop facts first,
    /// then a bounded failure event with stable identity closes the round.
    /// The failure record carries the reason only -- never oversized result
    /// bytes, which are not stop evidence. Failed rounds keep a bounded result
    /// record (result_ref required), readable through the normal proof path,
    /// never through a corruption quarantine.
    pub async fn fail_with_reason(&self, key: &RoundKey, reason: &str) -> Result<(), StoreError> {
        self.validate(key)?;
        if encoding::encoded_len(&reason)? > MAX_FAILURE_REASON_BYTES {
            return Err(error("failure_reason_budget"));
        }
        let a = self.acquired(&key.sid).await?;
        let mut state = runner(&a.envelope.checkpoint)?.clone();
        let previous = state.get("feed_tail").filter(|v| !v.is_null()).cloned();
        let r = matching(&mut state, key)?;
        if !r["terminal"].is_null() { return Ok(()); }
        self.assert_all_stopped(&r, key).await?;
        let failure = json!({"failed": true, "reason": reason});
        let failure_digest = digest(&failure)?;
        let failure_record = json!({"key": key, "result": failure, "digest": failure_digest.0});
        let failure_key = format!("failure:{}", key.tid.0);
        match self.document(&failure_key).await? {
            Some((_, old)) if old == failure_record => (),
            Some(_) => return Err(error("failure_conflict")),
            None => if !self.replace(&failure_key, None, failure_record).await? {
                return Err(dependency("failure_race"));
            },
        }
        let seq = a.envelope.checkpoint.commit_seq.0 + 1;
        let closure = json!({"key": key, "stopped": true, "failed": true, "reason": reason});
        let closure_digest = digest(&closure)?;
        let closure_key = format!("closure:{}", key.tid.0);
        match self.document(&closure_key).await? {
            Some((_, old)) if old == closure => (),
            Some(_) => return Err(error("closure_conflict")),
            None => if !self.replace(&closure_key, None, closure).await? {
                return Err(dependency("closure_race"));
            },
        }
        let record_ref = BlobRef { key: failure_key, digest: failure_digest };
        let proof = TerminalProof { schema_version: RoundSchemaVersion::V1,
            key: key.clone(), commit_id: CommitId(format!("terminal-{seq}")), commit_seq: CommitSeq(seq),
            status: TerminalStatus::Failed, result_ref: record_ref.clone(),
            closure_ref: BlobRef { key: closure_key, digest: closure_digest } };
        let budgets = encoding::WireBudgets::from_profile(&RoundBudgetProfile::v1());
        r["feed_permission"] = feed::permission(key, &proof, None, "failed", Some(reason),
            &record_ref, previous, &budgets)?;
        r["terminal"] = encode(&proof)?;
        state["feed_tail"] = permission_tail(&state, key)?;
        self.commit(a, state, "fail").await
    }
    /// Accepted-round guard that tolerates a lost/corrupted result blob: the
    /// origin may re-transfer (recreate) a physically lost archive document,
    /// but archiving a round with no record at all stays refused.
    async fn require_accepted(&self, key: &RoundKey) -> Result<(), StoreError> {
        match self.get(key).await {
            Ok(Some(_)) => Ok(()),
            Ok(None) => Err(error("not_accepted")),
            Err(StoreError::NotApplied { code }) if code == "archive_missing" => {
                let e = self.store.load(&key.sid).await?
                    .ok_or_else(|| error("not_accepted"))?;
                let encoded_key = encode(key)?;
                let state = runner(&e.checkpoint)?;
                if records(state)?.iter().any(|r| r["accepted"]["input"]["key"] == encoded_key)
                    || cold_list(state)?.iter().any(|entry| entry["tid"] == key.tid.0) {
                    Ok(())
                } else { Err(error("not_accepted")) }
            }
            Err(e) => Err(e),
        }
    }
    /// Archive an arbitrary JSON result (create-if-absent, content digested).
    pub async fn archive_value(&self, key: &RoundKey, result: Value) -> Result<(), StoreError> {
        self.validate(key)?;
        self.require_accepted(key).await?;
        let content = json!({"key": key, "result": result, "digest": digest(&result)?.0});
        let path = format!("archive:{}", key.tid.0);
        match self.document(&path).await? {
            Some((_, old)) if old == content => Ok(()),
            Some(_) => Err(error("archive_conflict")),
            None => if self.replace(&path, None, content).await? { Ok(()) }
                else { Err(dependency("archive_race")) },
        }
    }
    /// Recovery fixture: re-transfer the authoritative archive content after
    /// corruption; the digest is recomputed for the restored content.
    pub async fn repair_result(&self, key: &RoundKey, result: Value) -> Result<(), StoreError> {
        self.validate(key)?;
        let path = format!("archive:{}", key.tid.0);
        let Some((version, _)) = self.document(&path).await? else {
            return Err(dependency("archive_missing"));
        };
        let content = json!({"key": key, "result": result, "digest": digest(&result)?.0});
        if !self.replace(&path, Some(version), content).await? {
            return Err(dependency("archive_race"));
        }
        Ok(())
    }
    /// Fault injection: physical loss of the archived artifact document
    /// (the T4 blob-loss window), without any tombstone flow.
    pub async fn lose_archive(&self, key: &RoundKey) -> Result<(), StoreError> {
        self.validate(key)?;
        self.delete_doc(&format!("archive:{}", key.tid.0)).await
    }
    pub async fn published(&self) -> Result<Vec<RoundKey>,StoreError> {
        let mut result=vec![];
        for key in self.keys().await? {
            if self.document(&format!("feed-snapshot:{}",key.tid.0)).await?.is_some() { result.push(key); }
        }
        Ok(result)
    }
}
