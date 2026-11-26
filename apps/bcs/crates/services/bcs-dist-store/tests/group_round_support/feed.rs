//! Independent publication using immutable permission in the authoritative
//! checkpoint. Effects use their own CAS document; publishing never seals or
//! advances the execution checkpoint. Digest-referenced artifacts stay pinned
//! until append+publish complete (retention pin gate lives in `encoding`).
use super::*;
use super::encoding::{WireBudgets, artifact_event, artifact_ref_event, encoded_len, terminal_event};
use super::lifecycle::digest;

/// Build a publication permission under explicit wire budgets. Over-budget
/// inline artifacts switch to digest-reference events (reference shells must
/// themselves pass their budgets); the batch and snapshot shells must also
/// pass or the caller gets no permission — no silent truncation, and never a
/// `Completed` commit on an unencodable publication.
pub(super) fn permission(key:&RoundKey, proof:&TerminalProof,
    artifact:Option<(&Value,&BlobRef)>, status:&str, reason:Option<&str>,
    record_ref:&BlobRef, previous:Option<Value>, budgets:&WireBudgets
)->Result<Value,StoreError> {
    let seq=proof.commit_seq.0;
    let mut events:Vec<Value>=vec![];
    let snapshot_result=match artifact {
        Some((value,store)) => {
            let inline=artifact_event(key,seq,value);
            if encoded_len(&inline)? > budgets.event_bytes {
                events.push(artifact_ref_event(key,seq,store));
                json!({"key":store.key,"digest":store.digest.0})
            } else { events.push(inline); value.clone() }
        }
        None => json!(record_ref),   // 失败收尾: 快照指向有界失败记录本身
    };
    let terminal=terminal_event(key,seq,status,reason);
    if encoded_len(&terminal)? > budgets.event_bytes { return Err(dependency("feed_event_budget")); }
    events.push(terminal);
    if encoded_len(&events)? > budgets.batch_bytes { return Err(dependency("feed_batch_budget")); }
    let mut snapshot=json!({"key":key,"result":snapshot_result,"status":status,
        "source_commit":seq,"snapshot_event_id":[seq,2]});
    if let Some(reason)=reason { snapshot["reason"]=json!(reason); }
    if encoded_len(&snapshot)? > budgets.snapshot_bytes { return Err(dependency("snapshot_budget")); }
    let value=json!({"key":key,"commit_id":proof.commit_id,"source_commit":proof.commit_seq,
        "previous":previous,"events_digest":digest(&json!(events))?,"snapshot_digest":digest(&snapshot)?,
        "events":events,"snapshot":snapshot});
    Ok(value)
}
impl Prototype {
    pub async fn feed_permission(&self,key:&RoundKey)->Result<Value,StoreError> {
        self.validate(key)?;
        let e=self.store.load(&key.sid).await?.ok_or_else(||dependency("checkpoint_missing"))?;
        let state=runner(&e.checkpoint)?;
        let encoded_key=encode(key)?;
        let permission = match records(state)?.iter().find(|r|
            r["accepted"]["input"]["key"]==encoded_key) {
            Some(record) => record["feed_permission"].clone(),
            None => {
                // 压缩后的轮次从摘要核验过的冷证明取许可。
                let entry = cold_list(state)?.iter().find(|entry| entry["tid"]==key.tid.0)
                    .ok_or_else(|| error("round_missing"))?;
                let Some(facts)=self.read_cold(entry).await? else {
                    return Err(dependency("cold_missing"));
                };
                facts.permission.unwrap_or(Value::Null)
            }
        };
        if permission.is_null() { return Err(error("no_permission")); }
        Ok(permission)
    }
    async fn verified_permission(&self,permit:&Value)->Result<RoundKey,StoreError> {
        let key:RoundKey=decode(permit["key"].clone())?;
        if self.feed_permission(&key).await?!=*permit { return Err(error("permission_mismatch")); }
        if encode(&digest(&permit["events"])?)?!=permit["events_digest"]
            || encode(&digest(&permit["snapshot"])?)?!=permit["snapshot_digest"] {
            return Err(error("permission_digest"));
        }
        Ok(key)
    }
    pub async fn append_permitted(&self,permit:&Value)->Result<(),StoreError> {
        let key=self.verified_permission(permit).await?;
        let path=format!("feed:{}",key.sid.0);
        for _ in 0..8 {
            let old=self.document(&path).await?;
            let (version,mut feed)=match old {Some((v,x))=>(Some(v),x),None=>(None,json!({"head":null,"batches":[]}))};
            let batches=feed["batches"].as_array().ok_or_else(||error("feed"))?;
            if let Some(existing)=batches.iter().find(|b|b["key"]==permit["key"]) {
                return if existing==permit {Ok(())} else {Err(error("feed_conflict"))};
            }
            if feed["head"]!=permit["previous"] {return Err(dependency("feed_predecessor"));}
            if batches.len()>=16 {return Err(dependency("feed_capacity"));}
            feed["head"]=permit["snapshot"]["snapshot_event_id"].clone();
            feed["batches"].as_array_mut().unwrap().push(permit.clone());
            if self.replace(&path,version,feed).await? {return Ok(());}
        }
        Err(dependency("feed_contention"))
    }
    pub async fn publish_permitted(&self,permit:&Value)->Result<(),StoreError> {
        let key=self.verified_permission(permit).await?;
        let (_,feed)=self.document(&format!("feed:{}",key.sid.0)).await?
            .ok_or_else(||dependency("append_required"))?;
        if !feed["batches"].as_array().ok_or_else(||error("feed"))?.iter().any(|b|b==permit) {
            return Err(dependency("append_required"));
        }
        let path=format!("feed-snapshot:{}",key.tid.0);
        match self.document(&path).await? {
            Some((_,value)) if value==permit["snapshot"]=>Ok(()),
            Some(_)=>Err(error("snapshot_conflict")),
            None=>if self.replace(&path,None,permit["snapshot"].clone()).await? {Ok(())}
                else {Err(dependency("snapshot_race"))},
        }
    }
    pub async fn feed_events(&self,sid:&Sid)->Result<Vec<Value>,StoreError> {
        let Some((_,feed))=self.document(&format!("feed:{}",sid.0)).await? else {return Ok(vec![])};
        let mut events=vec![];
        for b in feed["batches"].as_array().ok_or_else(||error("feed"))? {
            events.extend(b["events"].as_array().ok_or_else(||error("events"))?.clone());
        }
        Ok(events)
    }
    pub async fn feed_snapshot(&self,key:&RoundKey)->Result<Value,StoreError> {
        self.validate(key)?;
        self.document(&format!("feed-snapshot:{}",key.tid.0)).await?
            .map(|(_,v)|v).ok_or_else(||dependency("snapshot_missing"))
    }
}
