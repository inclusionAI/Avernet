//! Public SQLite provider conformance for the per-task immutable
//! final-product archive (plan T4). The store is the real DbPlugin-backed
//! `GroupArtifactStore`; migrations run first so the tables exist in a fresh
//! file, and the projection contract is the D0-frozen pure validator with an
//! explicit small injected budget.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "contract assertion")]
#[path = "group_artifact_support/mod.rs"]
mod group_artifact_support;
use group_artifact_support::*;
use bcs_dist_store::group_artifact::{GroupArtifactStore, JsonProjectionValidator};
use bcs_service_api::port::repo::distributed::result::PublicProjectionValidator as _;
use bcs_service_api::port::repo::distributed::result::TaskArchiveRepo as _;
use bcs_test_support::contract::distributed::task_archive::task_archive_repo_contract_tests;
use std::num::NonZeroU64;

#[tokio::test]
async fn conformance_task_archive_repo() {
    let f = GroupFixture::new(small_budget(64 * 1024)).await;
    let archive = task_archive(
        7,
        NonZeroU64::new(1).unwrap(),
        vec![
            text_part("p-1", 1, "conformance part"),
            data_part("p-2", 2, serde_json::json!({"ok":true})),
        ],
    );
    // The projection check must respect the frame budget beyond doubt.
    JsonProjectionValidator
        .measure(&f.budget, &archive)
        .unwrap();
    let source = f.commit(&archive).await;
    let proof = proof_for(&archive, &source);
    let store: &GroupArtifactStore = &f.group;
    task_archive_repo_contract_tests(
        store,
        &source,
        &archive,
        &proof,
        f.commit_terminal(&archive, &proof),
        )
    .await
    .unwrap();
}

#[tokio::test]
async fn conformance_task_archive_service_edge_restarts() {
    // The conformance entry also proves the provider keeps serving across a
    // new database connection (cold readback), exactly like the runtime.
    let f = GroupFixture::new(small_budget(64 * 1024)).await;
    let archive = task_archive(8, NonZeroU64::new(3).unwrap(), vec![text_part(
        "p-1", 1, "restart part",
    )]);
    let source = f.commit(&archive).await;
    assert_eq!(
        f.group
            .archive_final(source.clone(), archive.clone())
            .await
            .unwrap(),
        bcs_service_api::port::repo::distributed::result::SavedArchive::Saved
    );
    let restarted = f.reopen_database();
    match restarted
        .get_archived(&grants(true), &scope(), &group(), &archive.round.tid)
        .await
        .unwrap()
    {
        Some(bcs_service_api::port::repo::distributed::result::ArchivedTaskView::Committed {
            archive: stored,
            ..
        }) => assert_eq!(archive, stored),
        other => panic!("unexpected {other:?}"),
    }
}
