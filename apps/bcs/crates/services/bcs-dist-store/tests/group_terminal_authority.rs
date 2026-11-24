//! Regression: archive authorization is not terminal authorization.
#[path = "group_artifact_support/mod.rs"]
mod support;
use support::*;
use bcs_service_api::port::repo::distributed::result::TaskArchiveRepo;
use std::num::NonZeroU64;

#[tokio::test]
async fn archived_result_without_terminal_commit_cannot_confirm_proof() {
    let f = GroupFixture::new(small_budget(64 * 1024)).await;
    let archive = task_archive(1, NonZeroU64::new(1).unwrap(), vec![text_part("p", 1, "answer")]);
    let source = f.commit(&archive).await;
    f.group.archive_final(source.clone(), archive.clone()).await.unwrap();
    assert!(f.group.confirm_terminal_proof(&scope(), &group(), &archive.round.tid,
        &proof_for(&archive, &source)).await.is_err());
    let proof = proof_for(&archive, &source);
    f.commit_terminal(&archive, &proof).await;
    // A correctly bound authority must still reject missing/corrupt closure bytes.
    use bcs_store_api::document::DocumentStorePlugin;
    let stored = f.inner.documents.read_document(&proof.closure_ref.key).await.unwrap().unwrap();
    f.inner.documents.replace_document(&proof.closure_ref.key, stored.version,
        &serde_json::json!({"key":archive.round,"stopped":false})).await.unwrap();
    assert!(f.group.confirm_terminal_proof(&scope(), &group(), &archive.round.tid, &proof).await.is_err());
    let stored = f.inner.documents.read_document(&proof.closure_ref.key).await.unwrap().unwrap();
    f.inner.documents.replace_document(&proof.closure_ref.key, stored.version,
        &serde_json::json!({"key":archive.round,"stopped":true})).await.unwrap();
    let mut forged = proof.clone();
    forged.closure_ref.key = "closure:absent".into();
    assert!(f.group.confirm_terminal_proof(&scope(), &group(), &archive.round.tid, &forged).await.is_err());
    f.group.confirm_terminal_proof(&scope(), &group(), &archive.round.tid, &proof).await.unwrap();
}
