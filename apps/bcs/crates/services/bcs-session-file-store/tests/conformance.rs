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
                status: None,
                limit: 100,
                offset: 0,
            },
        )
        .await
        .unwrap();
    assert_eq!(page.items.len(), 1);
    assert_eq!(page.total, 1);
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

#[tokio::test]
async fn list_offset_total_and_status_filter() {
    let repo = MemorySessionFileRepo::new();
    // Insert 5 files with distinct created_at via created_offset.
    for i in 0u64..5 {
        repo.insert(params(&format!("f{i}"), "s3", i * 10 + 1)).await.unwrap();
    }

    // List with limit=2, offset=1 → items.len()==2 && total==5
    let page = repo
        .list(
            "s3",
            SessionFileListParams {
                prefix: None,
                status: None,
                limit: 2,
                offset: 1,
            },
        )
        .await
        .unwrap();
    assert_eq!(page.items.len(), 2);
    assert_eq!(page.total, 5);

    // Complete one file (f0 → Ready) and filter by status=Ready
    repo.update_object_handle_and_status(
        "s3",
        "f0",
        r#"{"expires_at":1}"#,
        FileStatus::Ready,
        10,
    )
        .await
        .unwrap();
    let page = repo
        .list(
            "s3",
            SessionFileListParams {
                prefix: None,
                status: Some(FileStatus::Ready),
                limit: 100,
                offset: 0,
            },
        )
        .await
        .unwrap();
    assert_eq!(page.items.len(), 1);
    assert_eq!(page.total, 1);
    assert_eq!(page.items[0].status, FileStatus::Ready);

    // offset >= total → empty items, but `total` is still the full match count.
    let page = repo
        .list(
            "s3",
            SessionFileListParams {
                prefix: None,
                status: None,
                limit: 100,
                offset: 10,
            },
        )
        .await
        .unwrap();
    assert_eq!(page.items.len(), 0);
    assert_eq!(page.total, 5);

    // Combined prefix + status filter: only f0 is Ready, and only f0 matches prefix "f-f0".
    // (params() sets file_name = "f-{id}", so f0's file_name is "f-f0".)
    let page = repo
        .list(
            "s3",
            SessionFileListParams {
                prefix: Some("f-f0".to_string()),
                status: Some(FileStatus::Ready),
                limit: 100,
                offset: 0,
            },
        )
        .await
        .unwrap();
    assert_eq!(page.items.len(), 1);
    assert_eq!(page.total, 1);
    // Prefix matches but status doesn't: "f-f1" is Pending, filter Ready → zero.
    let page = repo
        .list(
            "s3",
            SessionFileListParams {
                prefix: Some("f-f1".to_string()),
                status: Some(FileStatus::Ready),
                limit: 100,
                offset: 0,
            },
        )
        .await
        .unwrap();
    assert_eq!(page.items.len(), 0);
    assert_eq!(page.total, 0);
}