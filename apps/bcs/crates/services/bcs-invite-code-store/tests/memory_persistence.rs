use bcs_invite_code_store::MemoryInviteCodeRepo;
use bcs_service_api::port::repo::{InviteCodeRecord, InviteCodeRepoPort, InviteCodeStatus, InviteCodeBindOutcome};

fn record(hash: &str) -> InviteCodeRecord {
    InviteCodeRecord { id: 0, code_hash: hash.into(), code_hint: "hint".into(), status: InviteCodeStatus::Active,
        bound_user_id: None, bound_at: None, created_by: Some("creator".into()), created_at: 1, updated_at: 1 }
}

#[tokio::test]
async fn coverage_invite_binding_is_exclusive_idempotent_and_survives_reload() {
    let dir = tempfile::tempdir().unwrap();
    let repo = MemoryInviteCodeRepo::with_data_dir(dir.path().into());
    repo.load_from_disk().await.unwrap();
    assert!(repo.insert_code(record("first")).await.unwrap());
    assert!(!repo.insert_code(record("first")).await.unwrap());
    assert!(repo.insert_code(record("second")).await.unwrap());
    let mut disabled = record("disabled"); disabled.status = InviteCodeStatus::Disabled;
    assert!(repo.insert_code(disabled).await.unwrap());
    assert_eq!(repo.count_by_created_by("creator").await.unwrap(), 3);
    assert_eq!(repo.count_by_created_by("other").await.unwrap(), 0);
    assert_eq!(repo.bind_code("missing", "alice", 2).await.unwrap(), InviteCodeBindOutcome::Unavailable);
    assert_eq!(repo.bind_code("disabled", "alice", 2).await.unwrap(), InviteCodeBindOutcome::Unavailable);
    let bound = repo.bind_code("first", "alice", 2).await.unwrap();
    let InviteCodeBindOutcome::Bound(bound) = bound else { panic!("expected first binding"); };
    assert_eq!(bound.bound_at, Some(2));
    assert_eq!(bound.status, InviteCodeStatus::Bound);
    assert!(matches!(repo.bind_code("first", "alice", 3).await.unwrap(), InviteCodeBindOutcome::AlreadyBoundToSameCode(ref r) if r == &bound));
    assert!(matches!(repo.bind_code("second", "alice", 3).await.unwrap(), InviteCodeBindOutcome::AlreadyBoundToDifferentCode(ref r) if r == &bound));
    assert!(matches!(repo.bind_code("first", "bob", 3).await.unwrap(), InviteCodeBindOutcome::AlreadyBoundToDifferentCode(ref r) if r == &bound));
    let reloaded = MemoryInviteCodeRepo::with_data_dir(dir.path().into());
    reloaded.load_from_disk().await.unwrap();
    assert_eq!(reloaded.find_by_user_id("alice").await.unwrap(), Some(bound.clone()));
    assert_eq!(reloaded.find_by_code_hash("first").await.unwrap(), Some(bound));
    assert!(reloaded.find_by_user_id("bob").await.unwrap().is_none());
    assert!(reloaded.find_by_code_hash("missing").await.unwrap().is_none());
}

#[tokio::test]
async fn coverage_invite_disk_errors_propagate_and_corruption_does_not_erase_memory() {
    let dir = tempfile::tempdir().unwrap();
    let repo = MemoryInviteCodeRepo::with_data_dir(dir.path().into());
    assert!(repo.insert_code(record("retained")).await.unwrap());
    std::fs::write(dir.path().join("invite_codes.json"), "not JSON").unwrap();
    assert!(repo.load_from_disk().await.is_err());
    assert!(repo.find_by_code_hash("retained").await.unwrap().is_some());
    let blocked = dir.path().join("blocked"); std::fs::write(&blocked, "file, not directory").unwrap();
    assert!(MemoryInviteCodeRepo::with_data_dir(blocked).insert_code(record("failed")).await.is_err());
    let memory = MemoryInviteCodeRepo::default();
    memory.load_from_disk().await.unwrap();
    assert!(memory.insert_code(record("memory-only")).await.unwrap());
    assert!(matches!(memory.bind_code("memory-only", "user", 9).await.unwrap(), InviteCodeBindOutcome::Bound(_)));
}
