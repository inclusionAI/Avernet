//! In-memory SessionFileRepo implementation.
//!
//! Intended for tests and local single-node development.
//! Production deployments use [`crate::mysql::MySqlSessionFileStore`].

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use tokio::sync::RwLock;

use bcs_domain::{FileStatus, SessionFile};
use bcs_service_api::port::repo::{
    NewSessionFileParams, SessionFileListPage, SessionFileListParams, SessionFileRepoPort,
};
use bcs_service_api::{ServiceError, ServiceResult};

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
}

impl MemorySessionFileRepo {
    /// Create a new empty in-memory session file repository.
    pub fn new() -> Self {
        Self::default()
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
        rows.insert(key, row.clone());
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

    async fn update_object_handle_and_status(
        &self,
        session_id: &str,
        file_id: &str,
        object_handle: &str,
        status: FileStatus,
        size: u64,
    ) -> ServiceResult<Option<SessionFile>> {
        let mut rows = self.rows.write().await;
        if let Some(r) = rows.get_mut(&(session_id.to_string(), file_id.to_string())) {
            r.object_handle = object_handle.to_string();
            r.status = status;
            r.size = size;
            r.updated_at = now_secs();
            Ok(Some(r.clone()))
        } else {
            Ok(None)
        }
    }

    async fn update_status(
        &self,
        session_id: &str,
        file_id: &str,
        status: FileStatus,
    ) -> ServiceResult<Option<SessionFile>> {
        let mut rows = self.rows.write().await;
        if let Some(r) = rows.get_mut(&(session_id.to_string(), file_id.to_string())) {
            r.status = status;
            r.updated_at = now_secs();
            Ok(Some(r.clone()))
        } else {
            Ok(None)
        }
    }

    async fn delete(&self, session_id: &str, file_id: &str) -> ServiceResult<bool> {
        Ok(self
            .rows
            .write()
            .await
            .remove(&(session_id.to_string(), file_id.to_string()))
            .is_some())
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
            .cloned()
            .collect();
        items.sort_by(|a, b| a.created_at.cmp(&b.created_at).then(a.file_id.cmp(&b.file_id)));

        // marker = "<created_at>:<file_id>"; resume strictly after marker
        if let Some(m) = &params.marker {
            if let Some((mc_str, mf)) = m.split_once(':') {
                let mc: u64 = mc_str.parse().unwrap_or(0);
                // Skip all items up to and including the marker row.
                items.retain(|r| {
                    r.created_at > mc
                        || (r.created_at == mc && r.file_id.as_str() > mf)
                });
            }
        }

        let limit = if params.limit == 0 {
            100
        } else {
            params.limit.min(1000)
        } as usize;
        let truncated = items.len() > limit;
        if truncated {
            items.truncate(limit);
        }
        let next_marker = if truncated {
            items
                .last()
                .map(|r| format!("{}:{}", r.created_at, r.file_id))
        } else {
            None
        };
        Ok(SessionFileListPage {
            items,
            truncated,
            next_marker,
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
                    limit: 100,
                    marker: None,
                },
            )
            .await
            .unwrap();
        assert_eq!(page.items.len(), 1);
        assert!(!page.truncated);

        // update_object_handle_and_status
        let updated = repo
            .update_object_handle_and_status(
                "s1",
                "f1",
                r#"{"expires_at":1}"#,
                FileStatus::Ready,
                10,
            )
            .await
            .unwrap()
            .unwrap();
        assert_eq!(updated.status, FileStatus::Ready);

        // delete
        assert!(repo.delete("s1", "f1").await.unwrap());
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
    async fn marker_pagination() {
        let repo = MemorySessionFileRepo::new();
        // Insert 5 files for session s3 — created_at is monotonic but
        // Rapid insertion within the same second is possible, so second
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

        // First page: limit=2
        let p1 = repo
            .list(
                "s3",
                SessionFileListParams {
                    prefix: None,
                    limit: 2,
                    marker: None,
                },
            )
            .await
            .unwrap();
        assert_eq!(p1.items.len(), 2);
        assert!(p1.truncated);
        let marker = p1.next_marker.as_ref().unwrap();

        // Second page: using marker
        let p2 = repo
            .list(
                "s3",
                SessionFileListParams {
                    prefix: None,
                    limit: 2,
                    marker: Some(marker.clone()),
                },
            )
            .await
            .unwrap();
        assert_eq!(p2.items.len(), 2);
        assert!(p2.truncated);

        // Third page: last item
        let marker2 = p2.next_marker.as_ref().unwrap();
        let p3 = repo
            .list(
                "s3",
                SessionFileListParams {
                    prefix: None,
                    limit: 2,
                    marker: Some(marker2.clone()),
                },
            )
            .await
            .unwrap();
        assert_eq!(p3.items.len(), 1);
        assert!(!p3.truncated);
        assert!(p3.next_marker.is_none());
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
        };
        repo.insert(make("p1", "images/cat.png")).await.unwrap();
        repo.insert(make("p2", "images/dog.png")).await.unwrap();
        repo.insert(make("p3", "docs/readme.txt")).await.unwrap();

        let page = repo
            .list(
                "s4",
                SessionFileListParams {
                    prefix: Some("images/".into()),
                    limit: 100,
                    marker: None,
                },
            )
            .await
            .unwrap();
        assert_eq!(page.items.len(), 2);
        assert!(!page.truncated);
    }
}