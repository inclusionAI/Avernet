//! Heartbeats discover failure; ownership records (never TTLs) fence writes.
use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use bcn_runtime_model::*;
use bcn_runtime_storage_api::NodeLivenessPort;
use crate::{RuntimeStore, keys::{digest, PARTITION}, persistence::{condition, encode, decode}};
#[derive(Serialize, Deserialize)]
struct Member { node: NodeIdentity, expires_ms: u64, draining: bool }
fn key(node: &NodeIdentity) -> String { format!("member/{}", digest(&[&node.node_id, &node.incarnation])) }
fn error(e: RuntimeError) -> StorageError { StorageError::Unavailable(e.to_string()) }
fn observation(node: NodeIdentity, member: Option<Member>, now: u64) -> NodeObservation {
    NodeObservation { node, liveness: if member.as_ref().is_some_and(|m| m.expires_ms > now) { Liveness::Alive } else { Liveness::Dead }, draining: member.is_some_and(|m| m.draining) }
}
impl RuntimeStore {
    async fn heartbeat(&self, node: &NodeIdentity, ttl_ms: u64, register: bool, draining: bool) -> StorageResult<()> {
        if ttl_ms == 0 && !draining { return Err(StorageError::LimitExceeded("TTL must be positive".into())); }
        let now = self.atomic.server_time_ms().await?;
        let key = key(node);
        let row = self.atomic.read_many(&node.scope, PARTITION, &[key.clone()]).await?.remove(0);
        let old: Option<Member> = row.as_ref().map(decode).transpose().map_err(error)?;
        if old.is_none() && !register { return Err(StorageError::Unavailable("incarnation not registered".into())); }
        let expires_ms = if draining { old.as_ref().map_or(0, |v| v.expires_ms) } else { now.checked_add(ttl_ms).ok_or(StorageError::RevisionExhausted)? };
        let member = Member { node: node.clone(), expires_ms, draining: draining || old.is_some_and(|v| v.draining) };
        self.atomic.transact(&node.scope, PARTITION, AtomicBatch { conditions: vec![condition(key.clone(), &row)], mutations: vec![Mutation::Put { key, value: encode(&member).map_err(error)? }] }).await?;
        Ok(())
    }
}
#[async_trait]
impl NodeLivenessPort for RuntimeStore {
    async fn register(&self, node: NodeIdentity, ttl_ms: u64) -> StorageResult<()> { self.heartbeat(&node, ttl_ms, true, false).await }
    async fn renew(&self, node: &NodeIdentity, ttl_ms: u64) -> StorageResult<()> { self.heartbeat(node, ttl_ms, false, false).await }
    async fn inspect_batch(&self, nodes: &[NodeIdentity]) -> StorageResult<Vec<NodeObservation>> {
        if nodes.is_empty() { return Ok(vec![]); }
        if nodes.len() > MAX_READ_KEYS { return Err(StorageError::LimitExceeded("member batch".into())); }
        let scope = &nodes[0].scope;
        if nodes.iter().any(|n| &n.scope != scope) { return Err(StorageError::InvalidKey("mixed scopes".into())); }
        let unknown = || nodes.iter().cloned().map(|node| NodeObservation { node, liveness: Liveness::Unknown, draining: false }).collect();
        let Ok(now) = self.atomic.server_time_ms().await else { return Ok(unknown()); };
        let Ok(rows) = self.atomic.read_many(scope, PARTITION, &nodes.iter().map(key).collect::<Vec<_>>()).await else { return Ok(unknown()); };
        Ok(nodes.iter().cloned().zip(rows).map(|(node, row)| {
            match row.as_ref().map(decode::<Member>).transpose() {
                Ok(member) => observation(node, member, now),
                Err(_) => NodeObservation { node, liveness: Liveness::Unknown, draining: false },
            }
        }).collect())
    }
    async fn list_members(&self, scope: &Scope, cursor: Option<String>, limit: u32) -> StorageResult<Page<NodeObservation>> {
        let cursor = cursor.map(|s| serde_json::from_str::<ScanCursor>(&s).map_err(|_| StorageError::InvalidCursor)).transpose()?;
        let page = self.atomic.scan_page(scope, PARTITION, "member/", cursor, limit as usize).await?;
        let now = self.atomic.server_time_ms().await;
        let mut items = Vec::new();
        for entry in page.entries {
            let member: Member = decode(&entry.entry).map_err(error)?;
            items.push(match now {
                Ok(now) => observation(member.node.clone(), Some(member), now),
                Err(_) => NodeObservation { node: member.node, liveness: Liveness::Unknown, draining: member.draining },
            });
        }
        Ok(Page { items, next_cursor: page.next_cursor.map(|c| serde_json::to_string(&c).map_err(|e| StorageError::Unavailable(e.to_string()))).transpose()? })
    }
    async fn mark_draining(&self, node: &NodeIdentity) -> StorageResult<()> { self.heartbeat(node, 0, false, true).await }
}
