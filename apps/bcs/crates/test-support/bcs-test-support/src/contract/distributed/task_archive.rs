//! Provider-independent assertions for the per-task immutable final-product
//! archive (plan T4). One `(scope, group, Tid)` owns at most one archive:
//! archiving never confers public eligibility, uncommitted archives are
//! recovery-only, expiry keeps a readable tombstone before (and after)
//! proof/result deletion, and unauthorized callers get one uniform denial
//! across every stored state.
//!
//! These are real callable harnesses, not proof that a backend exists. Each
//! provider commits its own authorized effect evidence, constructs its store
//! with the D0-frozen projection contract, and calls these from its
//! conformance entry with provider-built fixtures.

use super::ContractResult;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::common::IdempotentWrite;
use bcs_service_api::port::repo::distributed::result::{
    ArchiveGrants, ArchivedTaskView, SavedArchive, TaskArchiveRepo, TaskFinalArchive,
};
use std::num::NonZeroU32;

/// One full pass over the contract's lifecycle for one provider-supplied
/// authorized archive request and its exactly matching terminal proof:
/// uniform denial, all-parts-or-none archiving, idempotent replay vs.
/// content conflict, ordered part reads, saved projection metadata,
/// full-field proof confirmation, page listing, and tombstone-first
/// idempotent reclamation with no re-executable gap.
pub async fn task_archive_repo_contract_tests(
    repo: &dyn TaskArchiveRepo,
    source: &bcs_service_api::port::repo::distributed::common::EffectSource,
    archive: &TaskFinalArchive,
    proof: &TerminalProof,
    commit_terminal: impl Future<Output = ()>,
) -> ContractResult {
    let authorized = ArchiveGrants { read: true };
    let denied = ArchiveGrants { read: false };
    let scope = &archive.round.scope;
    let group = &archive.round.group;
    let tid = &archive.round.tid;

    // Unauthorized denial is uniform across states and discloses nothing.
    let absent_denial = repo.get_archived(&denied, scope, group, tid).await;
    assert!(matches!(absent_denial, Err(
        bcs_service_api::port::repo::distributed::common::StoreError::NotApplied { .. }
    )));
    // Authorized absence precedes the archive.
    assert_eq!(repo.get_archived(&authorized, scope, group, tid).await?, None);
    assert_eq!(
        repo.get_terminal_proof(&authorized, scope, group, tid).await?,
        None
    );

    // All parts or none: the archive commits, replays idempotently and never
    // accepts different content under the same identity.
    assert_eq!(
        repo.archive_final(source.clone(), archive.clone()).await?,
        SavedArchive::Saved
    );
    assert_eq!(
        repo.archive_final(source.clone(), archive.clone()).await?,
        SavedArchive::AlreadySaved
    );
    let mut conflicting = archive.clone();
    if let Some(last) = conflicting.artifacts.last_mut() {
        // Same identity, different bytes: a contract-level content change
        // that keeps orders and ids valid, yet must never overwrite the
        // committed archive.
        last.part = match &last.part {
            RoundPart::Text { text } => RoundPart::Text {
                text: format!("{text}!"),
            },
            RoundPart::Data { data } => RoundPart::Data {
                data: serde_json::json!({ "changed": data }),
            },
        };
    } else {
        conflicting.artifacts.push(ArtifactPart {
            artifact_id: ArtifactId("conflict".into()),
            order: NonZeroU32::new(1).expect("one"),
            part: RoundPart::Text {
                text: "different content".into(),
            },
        });
    }
    assert_eq!(
        repo.archive_final(source.clone(), conflicting).await?,
        SavedArchive::ContentConflict
    );
    let same_denial = repo.get_archived(&denied, scope, group, tid).await;
    assert_eq!(
        format!("{absent_denial:?}").split('"').next(),
        format!("{same_denial:?}").split('"').next(),
        "denial must stay uniform across absent and committed states"
    );

    // The committed view keeps full equality and saves the measured
    // projection metadata taken under the store's injected budget.
    let check = match repo.get_archived(&authorized, scope, group, tid).await? {
        Some(ArchivedTaskView::Committed { archive: stored, check }) => {
            assert_eq!(&stored, archive);
            assert!(check.measured_bytes > 0, "projection metadata is saved");
            check
        }
        other => panic!("expected committed archive, got {other:?}"),
    };
    let _ = check;
    // Ordered parts read back exactly once per artifact under the own Tid.
    let parts = repo
        .task_artifacts(&authorized, scope, group, std::slice::from_ref(tid))
        .await?;
    let expected: Vec<&ArtifactPart> = archive.artifacts.iter().collect();
    let actual: Vec<&ArtifactPart> = parts
        .iter()
        .map(|(part_tid, part)| {
            assert_eq!(part_tid, tid, "parts must answer under their own Tid");
            part
        })
        .collect();
    assert_eq!(actual.len(), expected.len());
    for (index, (stored, wanted)) in actual.iter().zip(expected.iter()).enumerate() {
        assert_eq!(stored.artifact_id, wanted.artifact_id, "part {index} identity");
        assert_eq!(stored.order, wanted.order, "part {index} order");
        assert_eq!(&stored.part, &wanted.part, "part {index} content");
    }
    // Results read length-bounded from the start of IO.
    let limit = NonZeroU32::new(16).expect("one");
    match repo
        .read_task_result(&authorized, scope, group, tid, limit)
        .await?
    {
        Some((prefix, full_length)) => {
            assert!(full_length > 0);
            assert!(
                prefix.len() as u32 <= limit.get(),
                "prefix reads must respect the IO bound"
            );
        }
        None => panic!("committed archive must expose its result object"),
    }
    // Listing pages contain the archived Tid under its scope and group.
    let page = repo
        .list_archived_tasks(&authorized, scope, group, 0, NonZeroU32::new(100).expect("one"))
        .await?;
    assert_eq!(page.iter().filter(|listed| *listed == tid).count(), 1);

    // Archive completion cannot authorize a terminal proof on its own.
    assert!(repo.confirm_terminal_proof(scope, group, tid, proof).await.is_err());
    commit_terminal.await;
    // Full-field proof confirmation is idempotent; one changed field rejects.
    let mut forged = proof.clone();
    forged.commit_id = CommitId("forged".into());
    assert!(
        repo.confirm_terminal_proof(scope, group, tid, &forged)
            .await
            .is_err()
    );
    assert_eq!(
        repo.confirm_terminal_proof(scope, group, tid, proof).await?,
        IdempotentWrite::Applied
    );
    assert_eq!(
        repo.confirm_terminal_proof(scope, group, tid, proof).await?,
        IdempotentWrite::AlreadyApplied
    );
    assert_eq!(
        repo.get_terminal_proof(&authorized, scope, group, tid).await?,
        Some(proof.clone())
    );

    // Expiry and reclamation stay idempotent, keep a readable tombstone and
    // close the re-execution gap.
    assert_eq!(
        repo.mark_task_expired(scope, group, tid).await?,
        IdempotentWrite::Applied
    );
    assert_eq!(
        repo.mark_task_expired(scope, group, tid).await?,
        IdempotentWrite::AlreadyApplied
    );
    assert_eq!(
        repo.reclaim_expired(scope, group, tid).await?,
        IdempotentWrite::Applied
    );
    assert_eq!(
        repo.reclaim_expired(scope, group, tid).await?,
        IdempotentWrite::AlreadyApplied
    );
    assert!(matches!(
        repo.get_archived(&authorized, scope, group, tid).await?,
        Some(ArchivedTaskView::Expired { .. })
    ));
    assert_eq!(
        repo.get_terminal_proof(&authorized, scope, group, tid).await?,
        None
    );
    assert!(
        repo.archive_final(source.clone(), archive.clone())
            .await
            .is_err(),
        "a reclaimed Tid never becomes re-archivable"
    );
    Ok(())
}
