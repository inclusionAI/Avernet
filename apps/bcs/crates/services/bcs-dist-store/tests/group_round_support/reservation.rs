//! Request intents live in ONE bounded registry document (`t1-request-registry`):
//! one physical key, one CAS boundary, no multi-key transaction. Reservation
//! intent is never execution authority. Abandonment first advances the exact
//! checkpoint head (or replaces the owner seal) so a delayed acceptance cannot
//! land, then marks the same row abandoned; the row stays as fence evidence and
//! is only removed by the gated `recycle_abandoned` operation. Expired
//! tombstones are permanent rows in this prototype: they keep `Admission`'s
//! Expired answer readable and are not recycled.
use super::*;

pub struct Reserved {
    pub(super) request: GroupStartRequest,
    pub(super) snapshot: AcquiredSession,
    pub(super) intent_version: Version,
    pub(super) intent: Value,
}
const REQUEST_REGISTRY: &str = "t1-request-registry";
impl Prototype {
    /// Bounded registry read: `(document version, rows)`. Rows are
    /// `{rid,state,request,head,owner}` with state prepared|abandoned|expired.
    pub(super) async fn request_registry(&self) -> Result<Option<(Version, Vec<Value>)>, StoreError> {
        match self.document(REQUEST_REGISTRY).await? {
            None => Ok(None),
            Some((version, value)) => {
                let rows = value.as_array().cloned().ok_or_else(|| error("request_registry"))?;
                Ok(Some((version, rows)))
            }
        }
    }
    pub(super) async fn write_registry(&self, expected: Option<Version>, rows: Vec<Value>) -> Result<bool, StoreError> {
        self.replace(REQUEST_REGISTRY, expected, Value::Array(rows)).await
    }
    pub(super) async fn request_entry(&self, rid: &str) -> Result<Option<Value>, StoreError> {
        Ok(self.request_registry().await?.and_then(|(_, rows)|
            rows.into_iter().find(|row| row["rid"] == json!(rid))))
    }
    /// 过期墓碑: CAS 把 prepared 行改为 expired; 已过期幂等, 墓碑先于删除可读。
    pub(super) async fn mark_request_expired(&self, rid: &str) -> Result<(), StoreError> {
        for _ in 0..8 {
            let Some((version, mut rows)) = self.request_registry().await? else {
                return Err(dependency("request_missing"));
            };
            let Some(row) = rows.iter_mut().find(|row| row["rid"] == json!(rid)) else {
                return Err(dependency("request_missing"));
            };
            if row["state"] == json!("expired") { return Ok(()); }
            if row["state"] != json!("prepared") { return Err(error("request_state")); }
            row["state"] = json!("expired");
            if self.write_registry(Some(version), rows).await? { return Ok(()); }
        }
        Err(dependency("request_contention"))
    }
    pub async fn reserve_only(&self, request: &GroupStartRequest) -> Result<Reserved, StoreError> {
        self.validate(&request.input.key)?;
        self.prepare(request).await?;
        let snapshot=self.acquired(&request.input.key.sid).await?;
        let state=runner(&snapshot.envelope.checkpoint)?;
        if records(state)?.iter().any(|r|r["terminal"].is_null()) { return Err(dependency("busy")); }
        if state["generation"].as_u64().and_then(|v|v.checked_add(1))!=Some(request.input.key.generation.get()) {
            return Err(error("stale_generation"));
        }
        if records(state)?.len()+cold_list(state)?.len()>=self.bounds.max_rounds_per_sid {
            return Err(dependency("round_capacity"));
        }
        let head=snapshot.envelope.checkpoint.commit_seq.0;
        let owner=&snapshot.ownership.owner;
        for _ in 0..8 {
            let old=self.request_registry().await?;
            let (version,mut rows)=match old { Some((v,x))=>(Some(v),x), None=>(None,vec![]) };
            let fresh=json!({"rid":request.request_id.0,"state":"prepared","request":request,
                "head":head,"owner":owner});
            if let Some(index)=rows.iter().position(|row| row["rid"]==json!(request.request_id.0)) {
                let intent=rows[index].clone();
                if intent["state"]=="abandoned" {
                    // 栅栏在先: 该行已废弃并证实旧 head/owner 不能再写, 同消息
                    // 重试按原语义复用同一槽位, 不静默放开其他请求。
                    rows[index]=fresh;
                    if !self.write_registry(version,rows).await? { continue; }
                    return self.capture_reserved(request,snapshot).await;
                }
                if intent["state"]=="expired" { return Err(dependency("request_expired")); }
                if intent["state"]!="prepared" { return Err(error("request_state")); }
                if intent["request"]!=encode(request)? { return Err(dependency("request_pending")); }
                if intent["head"]!=json!(head) || intent["owner"]!=encode(owner)? {
                    // acquired() has sealed this owner and loaded the exact
                    // current head. A prior owner/head can no longer commit.
                    // Retarget only the same original request and generation;
                    // stale generation was rejected before this loop.
                    let mut recovered=intent;
                    recovered["head"]=json!(head);
                    recovered["owner"]=encode(owner)?;
                    rows[index]=recovered;
                    if !self.write_registry(version,rows).await? { continue; }
                    continue;
                }
                return Ok(Reserved { request:request.clone(),snapshot,
                    intent_version:version.ok_or_else(|| error("request_registry"))?,intent });
            }
            let active=rows.iter().filter(|row| row["state"]!=json!("abandoned")).count();
            if active>=self.bounds.max_requests { return Err(dependency("request_capacity")); }
            if rows.len()>=self.bounds.max_request_rows() {
                return Err(dependency("request_rows_capacity"));
            }
            rows.push(fresh);
            if self.write_registry(version,rows).await? {
                return self.capture_reserved(request,snapshot).await;
            }
        }
        Err(dependency("request_contention"))
    }
    /// Read back the stored row after a registry CAS and verify identity; an
    /// uncertain write never becomes a reservation without confirmation.
    async fn capture_reserved(&self,request:&GroupStartRequest,snapshot:AcquiredSession)
        -> Result<Reserved,StoreError> {
        let (intent_version,rows)=self.request_registry().await?
            .ok_or_else(||dependency("request_missing"))?;
        let stored=rows.into_iter().find(|row| row["rid"]==json!(request.request_id.0))
            .ok_or_else(||dependency("request_missing"))?;
        if stored["request"]!=encode(request)? || stored["state"]!=json!("prepared")
            || stored["head"]!=json!(snapshot.envelope.checkpoint.commit_seq.0)
            || stored["owner"]!=encode(&snapshot.ownership.owner)? {
            return Err(error("request_raced"));
        }
        Ok(Reserved { request:request.clone(),snapshot,intent_version,intent:stored })
    }
    async fn release_intent(&self,reserved:&Reserved)->Result<(),StoreError> {
        for _ in 0..8 {
            let Some((version,mut rows))=self.request_registry().await? else {
                return Err(dependency("request_missing"));
            };
            let Some(row)=rows.iter_mut().find(|row| row["rid"]==json!(reserved.request.request_id.0)) else {
                return Err(dependency("request_missing"));
            };
            if row["state"]=="abandoned" { return Ok(()); }
            if row.clone()!=reserved.intent { return Err(error("request_replaced")); }
            row["state"]=json!("abandoned");
            if self.write_registry(Some(version),rows).await? { return Ok(()); }
        }
        Err(dependency("request_contention"))
    }
    pub async fn abandon(&self,reserved:&Reserved)->Result<(),StoreError> {
        let current=self.acquired(&reserved.request.input.key.sid).await?;
        if self.find_request(&reserved.request).await?.is_some() { return Err(error("already_accepted")); }
        // A seal fences old owners; a same-owner no-op business commit fences a
        // late acceptance with the reserved expected sequence. No expiry guess.
        if current.ownership.owner==reserved.snapshot.ownership.owner
            && current.envelope.checkpoint.commit_seq==reserved.snapshot.envelope.checkpoint.commit_seq {
            let state=runner(&current.envelope.checkpoint)?.clone();
            self.commit(current,state,"abandon-fence").await?;
        }
        self.release_intent(reserved).await
    }
    pub async fn commit_reserved(&self,reserved:&Reserved)->Result<Admission,StoreError> {
        let request=&reserved.request;
        if let Some(original)=self.find_request(request).await? {
            return Ok(self.replay(request,original));
        }
        // 共用注册表里其他请求的行会推进文档版本; 只有本行内容相等才有效,
        // 不把并发登记的红利误判成本行替换。
        let (_,rows)=self.request_registry().await?.ok_or_else(||dependency("request_missing"))?;
        let current=rows.into_iter().find(|row| row["rid"]==json!(request.request_id.0))
            .ok_or_else(||dependency("request_missing"))?;
        if current!=reserved.intent { return Err(dependency("abandoned_attempt")); }
        let mut state=runner(&reserved.snapshot.envelope.checkpoint)?.clone();
        state["generation"]=json!(request.input.key.generation.get());
        // status_seq: 权威受理提交的序号, 列表排序把它当作该行的
        // status_updated_at 通道 (随后的终态 CAS 会覆盖为 terminal commit_seq)。
        let status_seq=reserved.snapshot.envelope.checkpoint.commit_seq.0+1;
        records_mut(&mut state)?.push(json!({"accepted":request,"status_seq":status_seq,"terminal":null,
            "dispatch_intent":false,"dispatched":false,"append":false,"published":false}));
        let snapshot=AcquiredSession { ownership:reserved.snapshot.ownership.clone(),
            envelope:reserved.snapshot.envelope.clone(),last_commit:reserved.snapshot.last_commit.clone() };
        match self.commit(snapshot,state,&request.request_id.0).await {
            Ok(())=>Ok(Admission::New{accepted:request.clone()}),
            Err(StoreError::NotApplied{code}) if code=="commit_conflict"=>{
                if let Some(original)=self.find_request(request).await? { return Ok(self.replay(request,original)); }
                let now=self.acquired(&request.input.key.sid).await?;
                if now.envelope.checkpoint.commit_seq==reserved.snapshot.envelope.checkpoint.commit_seq
                    && now.ownership.owner==reserved.snapshot.ownership.owner {
                    return Err(dependency("unresolved_conflict"));
                }
                self.release_intent(reserved).await?;
                if records(runner(&now.envelope.checkpoint)?)?.iter().any(|r|r["terminal"].is_null()) {
                    Ok(Admission::Busy)
                } else { Err(error("stale_generation")) }
            }
            Err(e)=>Err(e), // unknown acceptance must never release its reservation
        }
    }
    pub(super) fn replay(&self,request:&GroupStartRequest,original:AcceptedRound)->Admission {
        if original.request_digest==request.request_digest && original.input.key.sid==request.input.key.sid {
            Admission::Replay{accepted:original}
        } else { Admission::Conflict }
    }
}
