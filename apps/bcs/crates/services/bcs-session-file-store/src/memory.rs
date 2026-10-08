//! In-memory SessionFileRepo implementation.
//!
//! Intended for tests and local single-node development.
//! Production deployments use [`crate::mysql::MySqlSessionFileStore`].

use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

use async_trait::async_trait;
use tokio::sync::RwLock;

use bcs_domain::{FileStatus, SessionFile};
use bcs_service_api::port::repo::{
    NewSessionFileParams, SessionFileListPage, SessionFileListParams, SessionFileRepoPort,
};
use bcs_service_api::types::{BotActionAuditRecord, BotOperationContext};
use bcs_service_api::{ServiceError, ServiceResult};

use crate::action_audit::{
    create_file_audit_record, delete_file_audit_record, update_file_audit_record,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

// ---------------------------------------------------------------------------
// Public type
// ---------------------------------------------------------------------------

/// In-memory implementation of [`SessionFileRepoPort`].
///
/// All state is held in a single `RwLock<HashMap>`. Suitable for tests and
/// local single-node development; not suitable for multi-node deployments.
#[derive(Default)]
pub struct MemorySessionFileRepo {
    rows: Arc<RwLock<HashMap<(String, String), SessionFile>>>,
    /// Published ordinary-business audit rows (the in-memory counterpart of
    /// `bcs_bot_action_audits`), kept SEPARATE from `rows` so an armed audit
    /// failure can be observed without touching business state.
    action_audits: RwLock<Vec<BotActionAuditRecord>>,
    action_audit_failure_armed: AtomicBool,
}

impl MemorySessionFileRepo {
    /// Create a new empty in-memory session file repository.
    pub fn new() -> Self {
        Self::default()
    }

    /// TEST-ONLY: arm a one-shot failure of the next ordinary-business audit
    /// append; the co-published business mutation must be discarded too.
    pub fn arm_action_audit_write_failure(&self) {
        self.action_audit_failure_armed
            .store(true, Ordering::SeqCst);
    }

    /// TEST/diagnostic observation of the published ordinary-business audit
    /// rows (the in-memory counterpart of querying `bcs_bot_action_audits`).
    /// The audit table is not a permission fact source and no public query
    /// API is added.
    pub async fn session_file_action_audit_records(
        &self,
    ) -> ServiceResult<Vec<BotActionAuditRecord>> {
        let audits = self.action_audits.read().await;
        Ok(audits.clone())
    }

    fn audit_env(&self) -> String {
        "memory".to_string()
    }

    /// STAGE one ordinary-business audit row (spec §12.5): every fallible
    /// decision — the armed test failure AND the `(env, operation_id,
    /// step_key)` slot conflict check — happens HERE, BEFORE any business
    /// side effect, so an audit failure leaves no partial success: the
    /// metadata mutation and the audit publish together or not at all.
    /// A same-slot record with identical content stages as an idempotent
    /// no-op; different content under the same slot is a Conflict.
    async fn stage_action_audit(
        &self,
        record: &BotActionAuditRecord,
    ) -> ServiceResult<tokio::sync::RwLockWriteGuard<'_, Vec<BotActionAuditRecord>>> {
        if self.action_audit_failure_armed.swap(false, Ordering::SeqCst) {
            return Err(ServiceError::InternalError(
                "injected file action audit append failure".to_string(),
            ));
        }
        let mut audits = self.action_audits.write().await;
        if let Some(existing) = audits.iter().find(|existing| existing.same_slot(record)) {
            if existing.content_conflicts(record) {
                return Err(ServiceError::Conflict(format!(
                    "file action audit slot '{}' already carries different content",
                    record.step_key
                )));
            }
        }
        Ok(audits)
    }

    /// Publish a staged audit row synchronously inside the caller's critical
    /// section; a slot already carrying identical content stays a no-op.
    fn publish_staged_action_audit(
        audits: &mut tokio::sync::RwLockWriteGuard<'_, Vec<BotActionAuditRecord>>,
        record: &BotActionAuditRecord,
    ) {
        if !audits.iter().any(|existing| existing.same_slot(record)) {
            audits.push(record.clone());
        }
    }
}

// ---------------------------------------------------------------------------
// SessionFileRepoPort impl
// ---------------------------------------------------------------------------

#[async_trait]
impl SessionFileRepoPort for MemorySessionFileRepo {
    async fn insert(&self, params: NewSessionFileParams) -> ServiceResult<SessionFile> {
        let now = now_secs();
        let row = SessionFile {
            file_id: params.file_id.clone(),
            session_id: params.session_id.clone(),
            file_name: params.file_name,
            mime_type: params.mime_type,
            size: params.size,
            sha256: None,
            owner: params.owner,
            storage_backend: params.storage_backend,
            object_handle: params.object_handle,
            status: FileStatus::Pending,
            created_at: now,
            updated_at: now,
        };
        let key = (params.session_id.clone(), params.file_id.clone());
        let mut rows = self.rows.write().await;
        if rows.contains_key(&key) {
            return Err(ServiceError::InternalError(format!(
                "duplicate session file {} {}",
                params.session_id, params.file_id
            )));
        }
        // Same-critical-section audit (spec §12.5): stage first so an armed
        // audit failure discards the INSERT; the row publishes WITH the
        // metadata INSERT (all-or-nothing).
        let record =
            create_file_audit_record(&params.operation, &self.audit_env(), &row.file_id);
        let mut staged_audit = self.stage_action_audit(&record).await?;
        rows.insert(key, row.clone());
        Self::publish_staged_action_audit(&mut staged_audit, &record);
        Ok(row)
    }

    async fn get(&self, session_id: &str, file_id: &str) -> ServiceResult<Option<SessionFile>> {
        Ok(self
            .rows
            .read()
            .await
            .get(&(session_id.to_string(), file_id.to_string()))
            .cloned())
    }

    async fn get_by_file_id(&self, file_id: &str) -> ServiceResult<Option<SessionFile>> {
        Ok(self
            .rows
            .read()
            .await
            .values()
            .find(|r| r.file_id == file_id)
            .cloned())
    }

    async fn update_object_handle_and_status(
        &self,
        session_id: &str,
        file_id: &str,
        object_handle: &str,
        status: FileStatus,
        size: u64,
        operation: &BotOperationContext,
    ) -> ServiceResult<Option<SessionFile>> {
        let mut rows = self.rows.write().await;
        let Some(existing) = rows.get(&(session_id.to_string(), file_id.to_string())).cloned() else {
            return Ok(None);
        };
        if existing.object_handle == object_handle
            && existing.status == status
            && existing.size == size
        {
            // Idempotent no-change update: NO audit row (spec §12.5), mirroring
            // the SQL twin's conditional UPDATE + stop-on-no-rows.
            return Ok(Some(existing));
        }
        // Same-critical-section `update/session_file/applied` audit.
        let record = update_file_audit_record(operation, &self.audit_env(), file_id);
        let mut staged_audit = self.stage_action_audit(&record).await?;
        if let Some(r) = rows.get_mut(&(session_id.to_string(), file_id.to_string())) {
            r.object_handle = object_handle.to_string();
            r.status = status;
            r.size = size;
            r.updated_at = now_secs();
            let updated = r.clone();
            Self::publish_staged_action_audit(&mut staged_audit, &record);
            Ok(Some(updated))
        } else {
            Ok(None)
        }
    }

    async fn update_status(
        &self,
        session_id: &str,
        file_id: &str,
        status: FileStatus,
        operation: &BotOperationContext,
    ) -> ServiceResult<Option<SessionFile>> {
        let mut rows = self.rows.write().await;
        let Some(existing) = rows.get(&(session_id.to_string(), file_id.to_string())).cloned() else {
            return Ok(None);
        };
        if existing.status == status {
            // Idempotent no-change update: NO audit row (spec §12.5).
            return Ok(Some(existing));
        }
        // Same-critical-section `update/session_file/applied` audit.
        let record = update_file_audit_record(operation, &self.audit_env(), file_id);
        let mut staged_audit = self.stage_action_audit(&record).await?;
        if let Some(r) = rows.get_mut(&(session_id.to_string(), file_id.to_string())) {
            r.status = status;
            r.updated_at = now_secs();
            let updated = r.clone();
            Self::publish_staged_action_audit(&mut staged_audit, &record);
            Ok(Some(updated))
        } else {
            Ok(None)
        }
    }

    async fn delete(
        &self,
        session_id: &str,
        file_id: &str,
        operation: &BotOperationContext,
    ) -> ServiceResult<bool> {
        // Final metadata DELETE + `delete/session_file/completed` audit in
        // the SAME critical section (spec §12.5): an armed audit failure
        // keeps the row in place with no `completed` row. A missing row is an
        // idempotent no-op that records nothing.
        let key = (session_id.to_string(), file_id.to_string());
        let mut rows = self.rows.write().await;
        if !rows.contains_key(&key) {
            return Ok(false);
        }
        let record = delete_file_audit_record(
            operation,
            &self.audit_env(),
            file_id,
            bcs_service_api::types::BotActionAuditPhase::Completed,
            None,
        );
        let mut staged_audit = self.stage_action_audit(&record).await?;
        let removed = rows.remove(&key).is_some();
        Self::publish_staged_action_audit(&mut staged_audit, &record);
        Ok(removed)
    }

    /// Standalone phase recorder (spec §12.5, plan Task 11): the ONLY audit
    /// lane outside the atomic mutations, used by the file service to
    /// persist `admitted` (before external backend I/O) and explicit
    /// `failed`/`unknown` outcomes. A slot already carrying this
    /// byte-identical record is an idempotent no-op; different content under
    /// the same slot is a Conflict.
    async fn record_operation_phase(&self, audit: BotActionAuditRecord) -> ServiceResult<()> {
        let mut staged_audit = self.stage_action_audit(&audit).await?;
        Self::publish_staged_action_audit(&mut staged_audit, &audit);
        Ok(())
    }

    async fn list(
        &self,
        session_id: &str,
        params: SessionFileListParams,
    ) -> ServiceResult<SessionFileListPage> {
        let rows = self.rows.read().await;
        let mut items: Vec<SessionFile> = rows
            .values()
            .filter(|r| r.session_id == session_id)
            .filter(|r| {
                params
                    .prefix
                    .as_deref()
                    .map_or(true, |p| r.file_name.starts_with(p))
            })
            .filter(|r| {
                params
                    .status
                    .map_or(true, |s| r.status == s)
            })
            .cloned()
            .collect();
        items.sort_by(|a, b| b.created_at.cmp(&a.created_at).then(b.file_id.cmp(&a.file_id)));

        let total = items.len() as u64;

        let limit = if params.limit == 0 {
            100
        } else {
            params.limit.min(1000)
        } as usize;

        let page_items: Vec<SessionFile> = items
            .into_iter()
            .skip(params.offset as usize)
            .take(limit)
            .collect();

        Ok(SessionFileListPage {
            items: page_items,
            total,
        })
    }

    async fn list_expired_pending(
        &self,
        now: u64,
        limit: u32,
    ) -> ServiceResult<Vec<SessionFile>> {
        let rows = self.rows.read().await;
        let mut out: Vec<SessionFile> = rows
            .values()
            .filter(|r| r.status == FileStatus::Pending)
            .filter_map(|r| {
                // expires_at lives inside the object_handle JSON envelope.
                // Semantically aligned with MySQL's JSON_EXTRACT(object_handle,'$.expires_at').
                let v: serde_json::Value = serde_json::from_str(&r.object_handle).ok()?;
                let exp = v.get("expires_at")?.as_u64()?;
                (exp < now).then(|| r.clone())
            })
            .collect();
        out.truncate(limit as usize);
        Ok(out)
    }

    async fn delete_all_for_session(
        &self,
        session_id: &str,
    ) -> ServiceResult<Vec<SessionFile>> {
        let mut rows = self.rows.write().await;
        let keys: Vec<(String, String)> = rows
            .keys()
            .filter(|(s, _)| s == session_id)
            .cloned()
            .collect();
        let removed = keys.into_iter().filter_map(|k| rows.remove(&k)).collect();
        Ok(removed)
    }
}

// ---------------------------------------------------------------------------
// Unit tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_domain::{ActorKind, ActorRef};

    fn params(id: &str, sess: &str, expires_at: u64) -> NewSessionFileParams {
        NewSessionFileParams {
            file_id: id.into(),
            session_id: sess.into(),
            file_name: format!("f-{id}"),
            mime_type: "text/plain".into(),
            size: 10,
            owner: ActorRef {
                actor_kind: ActorKind::Human,
                actor_id: "human_1".into(),
            },
            storage_backend: "local".into(),
            object_handle: serde_json::json!({ "expires_at": expires_at }).to_string(),
            expires_at,
            operation: test_operation(),
        }
    }

    fn test_operation() -> BotOperationContext {
        BotOperationContext {
            operation_id: "memory-file-store-tests".into(),
            actor: bcs_service_api::types::BotOperationActor::System {
                system_id: "memory-file-store-tests".into(),
                effective_actor_id: "memory-file-store-tests".into(),
            },
        }
    }

    #[tokio::test]
    async fn insert_get_list_update_delete() {
        let repo = MemorySessionFileRepo::new();
        let r = repo.insert(params("f1", "s1", 1005)).await.unwrap();
        assert_eq!(r.file_id, "f1");
        assert_eq!(r.status, FileStatus::Pending);

        // get
        let got = repo.get("s1", "f1").await.unwrap().unwrap();
        assert_eq!(got.file_id, "f1");

        // list
        let page = repo
            .list(
                "s1",
                SessionFileListParams {
                    prefix: None,
                    status: None,
                    limit: 100,
                    offset: 0,
                },
            )
            .await
            .unwrap();
        assert_eq!(page.items.len(), 1);
        assert_eq!(page.total, 1);

        // update_object_handle_and_status
        let updated = repo
            .update_object_handle_and_status(
                "s1",
                "f1",
                r#"{"expires_at":1}"#,
                FileStatus::Ready,
                10,
                &test_operation(),
            )
            .await
            .unwrap()
            .unwrap();
        assert_eq!(updated.status, FileStatus::Ready);

        // delete
        assert!(repo.delete("s1", "f1", &test_operation()).await.unwrap());
        assert!(repo.get("s1", "f1").await.unwrap().is_none());
    }

    #[tokio::test]
    async fn expired_pending_filtered() {
        let repo = MemorySessionFileRepo::new();
        repo.insert(params("f2", "s2", 1005)).await.unwrap(); // expires_at 1005

        // now=2000 → expired (1005 < 2000)
        let expired = repo.list_expired_pending(2000, 10).await.unwrap();
        assert_eq!(expired.len(), 1);

        // now=500 → not expired (1005 >= 500)
        let none = repo.list_expired_pending(500, 10).await.unwrap();
        assert!(none.is_empty());
    }

    #[tokio::test]
    async fn offset_pagination() {
        let repo = MemorySessionFileRepo::new();
        // Insert 5 files for session s3 — created_at is monotonic but
        // rapid insertion within the same second is possible, so second
        // page handling must be robust.
        for i in 1u8..=5 {
            let p = NewSessionFileParams {
                file_id: format!("f{i}"),
                session_id: "s3".into(),
                file_name: format!("file-{i}"),
                mime_type: "text/plain".into(),
                size: 10,
                owner: ActorRef {
                    actor_kind: ActorKind::Human,
                    actor_id: "human_1".into(),
                },
                storage_backend: "local".into(),
                object_handle: serde_json::json!({ "expires_at": 9999u64 }).to_string(),
                expires_at: 9999,
            };
            repo.insert(p).await.unwrap();
        }

        // First page: limit=2, offset=0
        let p1 = repo
            .list(
                "s3",
                SessionFileListParams {
                    prefix: None,
                    status: None,
                    limit: 2,
                    offset: 0,
                },
            )
            .await
            .unwrap();
        assert_eq!(p1.items.len(), 2);
        assert_eq!(p1.total, 5);

        // Second page: limit=2, offset=2
        let p2 = repo
            .list(
                "s3",
                SessionFileListParams {
                    prefix: None,
                    status: None,
                    limit: 2,
                    offset: 2,
                },
            )
            .await
            .unwrap();
        assert_eq!(p2.items.len(), 2);
        assert_eq!(p2.total, 5);

        // Third page: last item (limit=2, offset=4 -> only 1 remaining)
        let p3 = repo
            .list(
                "s3",
                SessionFileListParams {
                    prefix: None,
                    status: None,
                    limit: 2,
                    offset: 4,
                },
            )
            .await
            .unwrap();
        assert_eq!(p3.items.len(), 1);
        assert_eq!(p3.total, 5);
    }

    #[tokio::test]
    async fn list_defaults_to_newest_first() {
        // Default order is created_at DESC, file_id DESC — newest uploads first.
        // f5 is inserted last, so it must be the first item returned.
        let repo = MemorySessionFileRepo::new();
        for i in 1u8..=5 {
            repo.insert(params(&format!("f{i}"), "s_order", 9999)).await.unwrap();
        }
        let page = repo
            .list(
                "s_order",
                SessionFileListParams {
                    prefix: None,
                    status: None,
                    limit: 100,
                    offset: 0,
                },
            )
            .await
            .unwrap();
        let ids: Vec<&str> = page.items.iter().map(|f| f.file_id.as_str()).collect();
        // Last-inserted file_id is first; tie-broken by file_id DESC.
        assert_eq!(ids.first().copied(), Some("f5"), "newest upload must lead: {ids:?}");
        // And the full order is f5..f1 (reverse insertion), not insertion order.
        assert_eq!(ids, vec!["f5", "f4", "f3", "f2", "f1"], "order should be DESC: {ids:?}");
    }

    #[tokio::test]
    async fn delete_all_for_session_returns_removed() {
        let repo = MemorySessionFileRepo::new();
        repo.insert(params("a1", "s9", 1005)).await.unwrap();
        repo.insert(params("a2", "s9", 1006)).await.unwrap();
        repo.insert(params("b1", "s8", 1007)).await.unwrap();

        let removed = repo.delete_all_for_session("s9").await.unwrap();
        assert_eq!(removed.len(), 2);
        assert!(removed.iter().any(|r| r.file_id == "a1"));
        assert!(removed.iter().any(|r| r.file_id == "a2"));

        // s8 file still exists
        assert!(repo.get("s8", "b1").await.unwrap().is_some());
        // s9 files are gone
        assert!(repo.get("s9", "a1").await.unwrap().is_none());
        assert!(repo.get("s9", "a2").await.unwrap().is_none());
    }

    #[tokio::test]
    async fn list_filter_by_prefix() {
        let repo = MemorySessionFileRepo::new();
        let make = |id: &str, name: &str| NewSessionFileParams {
            file_id: id.into(),
            session_id: "s4".into(),
            file_name: name.into(),
            mime_type: "text/plain".into(),
            size: 10,
            owner: ActorRef {
                actor_kind: ActorKind::Human,
                actor_id: "human_1".into(),
            },
            storage_backend: "local".into(),
            object_handle: serde_json::json!({ "expires_at": 9999u64 }).to_string(),
            expires_at: 9999,
            operation: test_operation(),
        };
        repo.insert(make("p1", "images/cat.png")).await.unwrap();
        repo.insert(make("p2", "images/dog.png")).await.unwrap();
        repo.insert(make("p3", "docs/readme.txt")).await.unwrap();

        let page = repo
            .list(
                "s4",
                SessionFileListParams {
                    prefix: Some("images/".into()),
                    status: None,
                    limit: 100,
                    offset: 0,
                },
            )
            .await
            .unwrap();
        assert_eq!(page.items.len(), 2);
        assert_eq!(page.total, 2);
    }
}