use bcs_domain::{ActorKind, ActorRef, FileStatus};
use bcs_service_api::port::repo::{NewSessionFileParams, SessionFileListParams, SessionFileRepoPort};
use bcs_session_file_store::MemorySessionFileRepo;

fn params(id: &str, sess: &str, created_offset: u64) -> NewSessionFileParams {
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
        object_handle: serde_json::json!({ "expires_at": 1000u64 + created_offset }).to_string(),
        expires_at: 1000 + created_offset,
    }
}

#[tokio::test]
async fn insert_get_list_update_delete() {
    let repo = MemorySessionFileRepo::new();
    let r = repo.insert(params("f1", "s1", 1)).await.unwrap();
    assert_eq!(r.file_id, "f1");
    assert_eq!(r.status, FileStatus::Pending);
    let got = repo.get("s1", "f1").await.unwrap();
    assert!(got.is_some());
    assert_eq!(got.unwrap().file_id, "f1");
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
    assert!(repo.delete("s1", "f1").await.unwrap());
    assert!(repo.get("s1", "f1").await.unwrap().is_none());
}

#[tokio::test]
async fn expired_pending_filtered() {
    let repo = MemorySessionFileRepo::new();
    repo.insert(params("f2", "s2", 5)).await.unwrap(); // expires_at 1005
    let expired = repo.list_expired_pending(2000, 10).await.unwrap();
    assert_eq!(expired.len(), 1);
    let none = repo.list_expired_pending(500, 10).await.unwrap();
    assert!(none.is_empty());
}