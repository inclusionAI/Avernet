//! The shared team-sync conformance suites (plan Task 7): one RED
//! snapshot plus the aggregate-transaction/idempotency contract pieces,
//! run identically against BOTH production repo drivers (SQLite over the
//! full migration chain and the shared MemoryBotRepo state boundary).
//! Every assertion reads post-state through the PRODUCTION port surface
//! (`repo`) or the driver's real-query levers — never a private shortcut.

use bcs_domain::BotAccessRelation;

use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::types::team_manager_sync::{
    TeamManagerOperation, TeamManagerSync, VerifiedTeamManagerService,
};
use bcs_service_api::ServiceError;

use super::{move_cmd, restricted_service, sync_cmd, verified_service, Harness};

/// The Bot's binding rows in assertion form `(team_id, status)`.
async fn bindings_of(h: &Harness, bot_id: &str) -> Vec<(String, String)> {
    h.driver
        .team_bindings(bot_id)
        .await
        .into_iter()
        .map(|(team, status, _last_op)| (team, status))
        .collect()
}

/// Brief RED snapshot (step 1), verbatim semantics plus the surrounding
/// assertions: an empty snapshot is a validated FULL revoke of THIS
/// team's source only; replays return the original receipt with zero
/// recomputation (audit unchanged, no second receipt row); a
/// direct-only manager stays a Manager.
async fn red_empty_sync_replay_and_direct_only_survives(h: &Harness) {
    let repo = h.repo.clone();
    let service = verified_service(&h.driver.env());
    let empty = TeamManagerSync {
        service,
        bot_id: "bot-a".into(),
        team_id: "team-old".into(),
        operation: TeamManagerOperation::Sync,
        manager_user_ids: Vec::new(),
        idempotency_key: "empty-1".into(),
    };
    let first = repo.sync_team(empty.clone()).await.unwrap();
    assert_eq!(first.granted_count, 0);
    assert_eq!(first.revoked_count, 1, "the seeded team-old member is revoked");
    assert_eq!(first.bot_id, "bot-a");
    assert_eq!(first.team_id, "team-old");
    let audit_after_first = h.driver.audit_count().await;
    let receipts_after_first = h.driver.sync_receipt_row_count("bot-a").await;
    assert_eq!(receipts_after_first, 1, "the receipt persists durably");
    let again = repo.sync_team(empty).await.unwrap();
    assert_eq!(first, again, "replay returns the ORIGINAL receipt");
    assert_eq!(h.driver.audit_count().await, audit_after_first);
    assert_eq!(
        h.driver.sync_receipt_row_count("bot-a").await,
        receipts_after_first,
        "a replay never appends a second receipt row"
    );
    assert_eq!(
        repo.role("direct-only", "bot-a").await.unwrap(),
        Some(BotAccessRelation::Manager),
        "the direct source survives the empty team sync untouched"
    );
    assert_eq!(
        repo.role("m1", "bot-a").await.unwrap(),
        None,
        "empty snapshot is a validated full revoke of this team's source"
    );
    // The binding stays ACTIVE for an empty snapshot: a team may be an
    // active source with no members.
    assert_eq!(
        bindings_of(h, "bot-a").await,
        vec![("team-old".to_string(), "active".to_string())]
    );
}

/// A completely no-difference sync still commits a receipt (spec §5.4:
/// 无差异也有回执) — and a later same-content command with a DIFFERENT
/// key gets its own receipt that also changes nothing.
async fn no_difference_sync_still_persists_receipt(h: &Harness) {
    let repo = h.repo.clone();
    let service = verified_service(&h.driver.env());
    let audit_before = h.driver.audit_count().await;

    // 1) brand-new team, empty snapshot: nothing to change, still a live
    //    binding and a durable receipt.
    let fresh = repo
        .sync_team(sync_cmd(service.clone(), "bot-a", "team-fresh", &[], "fresh-1"))
        .await
        .unwrap();
    assert_eq!(fresh.granted_count, 0);
    assert_eq!(fresh.revoked_count, 0);
    assert_eq!(h.driver.audit_count().await, audit_before);
    assert_eq!(h.driver.sync_receipt_row_count("bot-a").await, 2);

    // 2) a second, DIFFERENT key with the same content: its own receipt,
    //    still no audit change.
    let again_other_key = repo
        .sync_team(sync_cmd(service.clone(), "bot-a", "team-fresh", &[], "fresh-2"))
        .await
        .unwrap();
    assert_eq!(again_other_key.granted_count, 0);
    assert_eq!(again_other_key.revoked_count, 0);
    assert_ne!(
        again_other_key.operation_id, fresh.operation_id,
        "a new key is a new operation"
    );
    assert_eq!(h.driver.audit_count().await, audit_before);
    assert_eq!(h.driver.sync_receipt_row_count("bot-a").await, 3);

    // 3) an idempotent no-difference repeat over an EXISTING snapshot
    //    (seeded through the formal source semantics on a fresh team —
    //    the RED bot's team-old slot already holds m1's revoked row).
    h.driver
        .seed_manager_source("bot-a", "m2", "team", "team-nd")
        .await;
    for key in ["same-snapshot-1", "same-snapshot-2"] {
        let repeat = repo
            .sync_team(sync_cmd(
                verified_service(&h.driver.env()),
                "bot-a",
                "team-nd",
                &["m2"],
                key,
            ))
            .await
            .unwrap();
        assert_eq!(repeat.granted_count, 0);
        assert_eq!(repeat.revoked_count, 0);
    }
    assert_eq!(
        repo.role("m2", "bot-a").await.unwrap(),
        Some(BotAccessRelation::Manager)
    );
    assert_eq!(
        h.driver.audit_count().await,
        audit_before,
        "no-difference syncs append zero audit rows"
    );
}

/// Same key with a different operation/payload is a Conflict — never a
/// recompute; the state stays exactly as the original command left it.
async fn same_key_changed_payload_conflicts(h: &Harness) {
    let repo = h.repo.clone();
    let service = verified_service(&h.driver.env());
    let audit_before = h.driver.audit_count().await;
    let receipts_before = h.driver.sync_receipt_row_count("bot-k").await;

    match repo
        .sync_team(sync_cmd(service.clone(), "bot-k", "team-k", &["m1"], "key-x"))
        .await
    {
        Ok(receipt) => {
            assert_eq!(receipt.granted_count, 1);
        }
        Err(other) => panic!("the first sync must succeed, got {other:?}"),
    }
    // Same key, different members → 409 Conflict.
    match repo
        .sync_team(sync_cmd(service.clone(), "bot-k", "team-k", &["m2"], "key-x"))
        .await
    {
        Err(ServiceError::Authority(AuthorityError::Conflict(_))) => {}
        other => panic!("changed payload must Conflict, got {:?}", other.map(|r| r.granted_count)),
    }
    // Same key, different operation (Move with the same key) → 409.
    match repo
        .sync_team(move_cmd(service, "bot-k", "team-k", "team-k2", &["m1"], "key-x"))
        .await
    {
        Err(ServiceError::Authority(AuthorityError::Conflict(_))) => {}
        other => panic!("changed operation must Conflict, got {:?}", other.map(|r| r.granted_count)),
    }
    // Nothing changed by the refused commands.
    assert_eq!(h.driver.audit_count().await, audit_before + 1);
    assert_eq!(h.driver.sync_receipt_row_count("bot-k").await, receipts_before + 1);
    assert_eq!(
        repo.role("m1", "bot-k").await.unwrap(),
        Some(BotAccessRelation::Manager)
    );
    assert_eq!(repo.role("m2", "bot-k").await.unwrap(), None);
}

/// A Move atomically stops the old team, replaces the new team's entire
/// snapshot, and survives replays (its own key AND the old sync's key)
/// without resurrecting the stopped team's edges.
async fn move_replays_and_old_sync_replay_do_not_resurrect(h: &Harness) {
    let repo = h.repo.clone();
    let service = verified_service(&h.driver.env());

    // Fill team-old with one member through the production lane.
    let fill = repo
        .sync_team(sync_cmd(service.clone(), "bot-mv", "team-old", &["m1"], "fill-old"))
        .await
        .unwrap();
    assert_eq!(fill.granted_count, 1);
    // Give the move's TARGET team its own prior snapshot: the move must
    // REPLACE it with the complete new one, never merge into it.
    let preexisting = repo
        .sync_team(sync_cmd(service.clone(), "bot-mv", "team-new", &["m3"], "pre-new"))
        .await
        .unwrap();
    assert_eq!(preexisting.granted_count, 1);

    // Move the source to team-new with m2's snapshot (m1 loses authority;
    // m3's prior team-new edge is revoked as part of the replacement).
    let moved = repo
        .sync_team(move_cmd(
            service.clone(),
            "bot-mv",
            "team-old",
            "team-new",
            &["m2"],
            "move-1",
        ))
        .await
        .unwrap();
    assert_eq!(moved.granted_count, 1, "m2 granted at the new team");
    assert_eq!(
        moved.revoked_count, 2,
        "m1's old-team edge + m3's replaced target edge revoked"
    );
    assert_eq!(repo.role("m3", "bot-mv").await.unwrap(), None);
    assert_eq!(
        repo.role("m1", "bot-mv").await.unwrap(),
        None,
        "the stopped team's authority is gone"
    );
    assert_eq!(
        repo.role("m2", "bot-mv").await.unwrap(),
        Some(BotAccessRelation::Manager)
    );
    assert_eq!(
        bindings_of(h, "bot-mv").await,
        vec![
            ("team-new".to_string(), "active".to_string()),
            ("team-old".to_string(), "stopped".to_string()),
        ]
    );

    // Replay the MOVE key: the original receipt, the stopped team stays
    // stopped, the new snapshot stays.
    let receipts_before = h.driver.sync_receipt_row_count("bot-mv").await;
    let moved_again = repo
        .sync_team(move_cmd(service.clone(), "bot-mv", "team-old", "team-new", &["m2"], "move-1"))
        .await
        .unwrap();
    assert_eq!(moved, moved_again);
    assert_eq!(
        h.driver.sync_receipt_row_count("bot-mv").await,
        receipts_before
    );
    assert_eq!(repo.role("m1", "bot-mv").await.unwrap(), None);

    // Replay the OLD SYNC's key: its receipt returns verbatim WITHOUT
    // re-applying its recorded grant — team-old stays stopped and empty.
    let fill_again = repo
        .sync_team(sync_cmd(service, "bot-mv", "team-old", &["m1"], "fill-old"))
        .await
        .unwrap();
    assert_eq!(fill, fill_again, "the old key replays its original receipt");
    assert_eq!(
        repo.role("m1", "bot-mv").await.unwrap(),
        None,
        "an old-key replay never resurrects the stopped team's snapshot"
    );
    assert_eq!(
        repo.role("m2", "bot-mv").await.unwrap(),
        Some(BotAccessRelation::Manager),
        "the moved snapshot is not disturbed by the old key's replay"
    );
    assert_eq!(
        bindings_of(h, "bot-mv").await,
        vec![
            ("team-new".to_string(), "active".to_string()),
            ("team-old".to_string(), "stopped".to_string()),
        ]
    );
}

/// Source isolation: one command reconciles ONE team — an intersection
/// member keeps the other team's edge, an ordinary absent member loses
/// authority, and other teams' snapshots are never touched.
async fn team_intersection_and_other_team_union(h: &Harness) {
    let repo = h.repo.clone();
    let service = verified_service(&h.driver.env());
    repo.sync_team(sync_cmd(service.clone(), "bot-i", "t1", &["m1", "m2"], "i1"))
        .await
        .unwrap();
    repo.sync_team(sync_cmd(service.clone(), "bot-i", "t2", &["m2", "m3"], "i2"))
        .await
        .unwrap();
    assert_eq!(repo.role("m1", "bot-i").await.unwrap(), Some(BotAccessRelation::Manager));
    assert_eq!(repo.role("m2", "bot-i").await.unwrap(), Some(BotAccessRelation::Manager));
    assert_eq!(repo.role("m3", "bot-i").await.unwrap(), Some(BotAccessRelation::Manager));

    // Drop m1 from t1: m1 loses authority (absent from the new snapshot),
    // m2 keeps the t2 edge, m3 and the whole t2 snapshot remain intact.
    let shrink = repo
        .sync_team(sync_cmd(service, "bot-i", "t1", &["m2"], "i3"))
        .await
        .unwrap();
    assert_eq!(shrink.granted_count, 0);
    assert_eq!(shrink.revoked_count, 1);
    assert_eq!(repo.role("m1", "bot-i").await.unwrap(), None);
    assert_eq!(repo.role("m2", "bot-i").await.unwrap(), Some(BotAccessRelation::Manager));
    assert_eq!(repo.role("m3", "bot-i").await.unwrap(), Some(BotAccessRelation::Manager));
    // t2's snapshot is untouched: re-syncing it with the same set is a
    // no-difference receipt.
    let t2_again = repo
        .sync_team(sync_cmd(
            verified_service(&h.driver.env()),
            "bot-i",
            "t2",
            &["m2", "m3"],
            "i4",
        ))
        .await
        .unwrap();
    assert_eq!(t2_again.granted_count, 0);
    assert_eq!(t2_again.revoked_count, 0);
}

/// The owner edge coexists with team edges and empty team edges; the
/// owner never derives authority from a team source (owner-in-snapshot
/// is a Conflict that changes nothing).
async fn owner_edge_coexists_and_owner_never_listable(h: &Harness) {
    let repo = h.repo.clone();
    let service = verified_service(&h.driver.env());
    let audit_before = h.driver.audit_count().await;
    repo.sync_team(sync_cmd(service.clone(), "bot-o", "team-o", &["m1"], "o1"))
        .await
        .unwrap();
    assert_eq!(
        repo.role("own", "bot-o").await.unwrap(),
        Some(BotAccessRelation::Owner),
        "the owner edge coexists with team edges and is untouched by the sync"
    );
    // Empty snapshot: the team edges go away; the owner edge stays.
    repo.sync_team(sync_cmd(service.clone(), "bot-o", "team-o", &[], "o2"))
        .await
        .unwrap();
    assert_eq!(repo.role("m1", "bot-o").await.unwrap(), None);
    assert_eq!(repo.role("own", "bot-o").await.unwrap(), Some(BotAccessRelation::Owner));
    assert_eq!(repo.ownership("bot-o").await.unwrap().owner_user_id, "own");

    // The owner in the snapshot is a Conflict; nothing changes.
    match repo
        .sync_team(sync_cmd(service, "bot-o", "team-o", &["own", "m1"], "o3"))
        .await
    {
        Err(ServiceError::Authority(AuthorityError::Conflict(_))) => {}
        other => panic!("owner-in-snapshot must Conflict, got {:?}", other.map(|r| r.granted_count)),
    }
    assert_eq!(repo.role("m1", "bot-o").await.unwrap(), None);
    assert_eq!(h.driver.audit_count().await, audit_before + 2, "grant + revoke only");
}

/// Gate 0 snapshot bound (spec §1.3): 1,000 deduplicated Humans commit;
/// 1,001 is rejected BEFORE any transaction (no receipt, no state).
async fn snapshot_limit_boundary(h: &Harness) {
    let repo = h.repo.clone();
    let service = verified_service(&h.driver.env());
    let thousand: Vec<String> = (0..1_000).map(|i| format!("bulk-{i:03}")).collect();
    h.driver
        .seed_humans(&thousand.iter().map(|s| s.as_str()).collect::<Vec<_>>())
        .await;
    let thousand_refs: Vec<&str> = thousand.iter().map(|s| s.as_str()).collect();
    let receipt = repo
        .sync_team(sync_cmd(
            service.clone(),
            "bot-bulk",
            "team-bulk",
            &thousand_refs,
            "bulk-1",
        ))
        .await
        .unwrap();
    assert_eq!(receipt.granted_count, 1_000);
    assert_eq!(receipt.revoked_count, 0);
    assert_eq!(h.driver.sync_receipt_row_count("bot-bulk").await, 1);

    // 1,001 distinct humans: rejected pre-transaction (no receipt, no state).
    let mut thousand_one = thousand.clone();
    thousand_one.push("over-limit".to_string());
    let thousand_one_refs: Vec<&str> = thousand_one.iter().map(|s| s.as_str()).collect();
    match repo
        .sync_team(sync_cmd(
            service.clone(),
            "bot-bulk",
            "team-bulk",
            &thousand_one_refs,
            "bulk-1001",
        ))
        .await
    {
        Err(ServiceError::Authority(AuthorityError::InvalidSubject(_))) => {}
        other => panic!("over-limit snapshot must be rejected, got {:?}", other.map(|r| r.granted_count)),
    }
    assert_eq!(h.driver.sync_receipt_row_count("bot-bulk").await, 1);

    // Duplicates collapse before the bound applies: 1,001 RAW entries
    // de-duplicated to <= 1,000 distinct Humans commit as one snapshot.
    let mut thousand_dup = thousand.clone();
    thousand_dup.push("bulk-000".to_string());
    let dup_refs: Vec<&str> = thousand_dup.iter().map(|s| s.as_str()).collect();
    let dedup = repo
        .sync_team(sync_cmd(service, "bot-bulk", "team-bulk", &dup_refs, "bulk-dup"))
        .await
        .unwrap();
    assert_eq!(dedup.granted_count, 0, "already granted after dedup");
    assert_eq!(dedup.revoked_count, 0);
}

/// An injected failure of the audit/receipt commit rolls back EVERYTHING:
/// no partial source change, no audit residue, no receipt row, no
/// binding change.
async fn injected_commit_failure_rolls_back_everything(h: &Harness) {
    let repo = h.repo.clone();
    let service = verified_service(&h.driver.env());
    let audit_before = h.driver.audit_count().await;
    let bindings_before = h.driver.team_bindings("bot-rb").await;
    let receipts_before = h.driver.sync_receipt_row_count("bot-rb").await;

    h.driver.arm_sync_write_failure();
    let outcome = repo
        .sync_team(sync_cmd(service, "bot-rb", "team-rb", &["m1", "m2"], "rb-1"))
        .await;
    assert!(outcome.is_err(), "the injected commit failure must surface");
    assert_eq!(
        repo.role("m1", "bot-rb").await.unwrap(),
        None,
        "no granted edge may survive the roll back"
    );
    assert_eq!(repo.role("m2", "bot-rb").await.unwrap(), None);
    assert_eq!(h.driver.audit_count().await, audit_before, "no audit residue");
    assert_eq!(
        h.driver.sync_receipt_row_count("bot-rb").await,
        receipts_before,
        "no durable receipt may survive the roll back"
    );
    assert_eq!(
        h.driver.team_bindings("bot-rb").await,
        bindings_before,
        "no partial binding may survive the roll back"
    );
}

/// Credential scopes are re-validated by the STORE, fail closed, first:
/// wrong env, disallowed bot, disallowed team (and a move's NEW team), a
/// disallowed operation — the state never changes.
async fn credential_scopes_fail_closed(h: &Harness) {
    let repo = h.repo.clone();
    let env = h.driver.env();
    let audit_before = h.driver.audit_count().await;

    // Right roles, wrong env → Forbidden.
    let wrong_env = if env == "local" { "other-env" } else { "local" };
    match repo
        .sync_team(sync_cmd(
            verified_service(wrong_env),
            "bot-sc",
            "team-sc",
            &["m1"],
            "sc-env",
        ))
        .await
    {
        Err(ServiceError::Authority(AuthorityError::Forbidden(_))) => {}
        other => panic!("wrong env must be Forbidden, got {:?}", other.map(|r| r.granted_count)),
    }
    // Bot outside allowed_bots → Forbidden.
    match repo
        .sync_team(sync_cmd(
            restricted_service(&env),
            "bot-sc",
            "team-sc",
            &["m1"],
            "sc-bot",
        ))
        .await
    {
        Err(ServiceError::Authority(AuthorityError::Forbidden(_))) => {}
        other => panic!("disallowed bot must be Forbidden, got {:?}", other.map(|r| r.granted_count)),
    }
    // Team outside allowed_teams → Forbidden.
    match repo
        .sync_team(sync_cmd(
            restricted_service(&env),
            "bot-b",
            "wrong-team",
            &["m1"],
            "sc-team",
        ))
        .await
    {
        Err(ServiceError::Authority(AuthorityError::Forbidden(_))) => {}
        other => panic!("disallowed team must be Forbidden, got {:?}", other.map(|r| r.granted_count)),
    }
    // Operation outside allowed_operations (an unrestricted-teams
    // credential that may only run Sync) → Forbidden.
    let sync_only = VerifiedTeamManagerService {
        service_id: "sync-only-svc".to_string(),
        env: env.clone(),
        allowed_bots: None,
        allowed_teams: None,
        allowed_operations: Some(vec![TeamManagerOperation::Sync]),
    };
    match repo
        .sync_team(move_cmd(sync_only, "bot-b", "team-src", "team-dst", &["m1"], "sc-op"))
        .await
    {
        Err(ServiceError::Authority(AuthorityError::Forbidden(_))) => {}
        other => panic!("disallowed operation must be Forbidden, got {:?}", other.map(|r| r.granted_count)),
    }
    // A move's NEW team must be inside allowed_teams too.
    let one_team = VerifiedTeamManagerService {
        service_id: "one-team-svc".to_string(),
        env: env.clone(),
        allowed_bots: None,
        allowed_teams: Some(vec!["team-src".to_string()]),
        allowed_operations: None,
    };
    match repo
        .sync_team(move_cmd(one_team, "bot-b", "team-src", "team-dst", &["m1"], "sc-move"))
        .await
    {
        Err(ServiceError::Authority(AuthorityError::Forbidden(_))) => {}
        other => panic!("a move into a disallowed new team must be Forbidden, got {:?}", other.map(|r| r.granted_count)),
    }
    // Structural defects are rejected too: blank identity fields and a
    // move onto the same team.
    for defect in [
        TeamManagerSync {
            service: verified_service(&env),
            bot_id: "bot-b".into(),
            team_id: "".into(),
            operation: TeamManagerOperation::Sync,
            manager_user_ids: vec![],
            idempotency_key: "k".into(),
        },
        TeamManagerSync {
            service: verified_service(&env),
            bot_id: "   ".into(),
            team_id: "t".into(),
            operation: TeamManagerOperation::Sync,
            manager_user_ids: vec![],
            idempotency_key: "k".into(),
        },
        TeamManagerSync {
            service: verified_service(&env),
            bot_id: "bot-b".into(),
            team_id: "t".into(),
            operation: TeamManagerOperation::Sync,
            manager_user_ids: vec![],
            idempotency_key: " ".into(),
        },
        TeamManagerSync {
            service: verified_service(&env),
            bot_id: "bot-b".into(),
            team_id: "t".into(),
            operation: TeamManagerOperation::Move {
                new_team_id: "t".into(),
            },
            manager_user_ids: vec![],
            idempotency_key: "k".into(),
        },
    ] {
        assert!(
            repo.sync_team(defect).await.is_err(),
            "structural command defects must be rejected"
        );
    }
    assert_eq!(
        h.driver.audit_count().await,
        audit_before,
        "no refused command may change any state"
    );
}

/// Bot validation branches: ghost bot → BotNotFound; uninitialized bot →
/// OwnershipNotInitialized. Snapshot member validation: an unknown
/// human OR a blank entry is InvalidSubject with no partial state.
async fn validation_branches_fail_closed(h: &Harness) {
    let repo = h.repo.clone();
    let env = h.driver.env();
    assert!(matches!(
        repo.sync_team(sync_cmd(
            verified_service(&env),
            "ghost-bot",
            "team-x",
            &["m1"],
            "v-ghost",
        ))
        .await,
        Err(ServiceError::BotNotFound(_))
    ));
    h.driver.seed_uninitialized_bot("bot-zero").await;
    assert!(matches!(
        repo.sync_team(sync_cmd(
            verified_service(&env),
            "bot-zero",
            "team-x",
            &["m1"],
            "v-zero",
        ))
        .await,
        Err(ServiceError::Authority(AuthorityError::OwnershipNotInitialized { .. }))
    ));
    // Unknown member.
    let service = verified_service(&env);
    match repo
        .sync_team(sync_cmd(service.clone(), "bot-v", "team-v", &["m1", "ghost-user"], "v-members"))
        .await
    {
        Err(ServiceError::Authority(AuthorityError::InvalidSubject(_))) => {}
        other => panic!("unknown member must be InvalidSubject, got {:?}", other.map(|r| r.granted_count)),
    }
    // Blank member entries (an explicit empty string and a whitespace-only
    // id) — never "defaulted" into an empty snapshot silently.
    for bad in ["", "   "] {
        match repo
            .sync_team(sync_cmd(service.clone(), "bot-v", "team-v", &[bad], "v-blank"))
            .await
        {
            Err(ServiceError::Authority(AuthorityError::InvalidSubject(_))) => {}
            other => panic!("blank member must be InvalidSubject, got {:?}", other.map(|r| r.granted_count)),
        }
    }
    // Nothing was applied: no receipt rows, no audit rows, no roles.
    assert_eq!(h.driver.sync_receipt_row_count("bot-v").await, 0);
    assert_eq!(repo.role("m1", "bot-v").await.unwrap(), None);
}

/// A granted team edge that is later revoked RESTORES under the same
/// identity when re-added (never a second slot, never INSERT-IGNORE):
/// re-syncing the reverted snapshot re-grants exactly the revoked
/// member, and the counts stay true to the actual changes.
async fn revoked_snapshot_restores_through_sync(h: &Harness) {
    let repo = h.repo.clone();
    let service = verified_service(&h.driver.env());
    let audit_before = h.driver.audit_count().await;
    h.driver
        .seed_manager_source("bot-rs", "m2", "team", "team-rs")
        .await;

    // Empty sync: m2's seeded team-rs edge is revoked.
    let emptied = repo
        .sync_team(sync_cmd(service.clone(), "bot-rs", "team-rs", &[], "rs-1"))
        .await
        .unwrap();
    assert_eq!(emptied.granted_count, 0);
    assert_eq!(emptied.revoked_count, 1);
    assert_eq!(repo.role("m2", "bot-rs").await.unwrap(), None);

    // Re-add: the previously revoked row RESTORES (grant) — same slot.
    let restored = repo
        .sync_team(sync_cmd(service, "bot-rs", "team-rs", &["m2"], "rs-2"))
        .await
        .unwrap();
    assert_eq!(restored.granted_count, 1);
    assert_eq!(restored.revoked_count, 0);
    assert_eq!(
        repo.role("m2", "bot-rs").await.unwrap(),
        Some(BotAccessRelation::Manager)
    );
    assert_eq!(
        h.driver.audit_count().await,
        audit_before + 2,
        "one revoke + one restore-grant, nothing else"
    );
}

/// The full shared suite; run for every driver.
pub async fn team_manager_sync_contract_tests(h: &Harness) {
    // Shared fixture (all levers, never writing audit/receipt rows):
    // owner `own` for the RED bot; the seeded team-old member m1; a
    // direct-only manager; one base human pool.
    h.driver.seed_owned("bot-a", "own").await;
    h.driver.seed_humans(&["own", "m1", "m2", "m3", "direct-only"]).await;
    h.driver
        .seed_manager_source("bot-a", "direct-only", "direct", "manual")
        .await;
    h.driver.seed_manager_source("bot-a", "m1", "team", "team-old").await;
    assert_eq!(h.driver.audit_count().await, 0, "levers never write audit rows");

    red_empty_sync_replay_and_direct_only_survives(h).await;

    // Dedicated bots for the isolated scenarios (audit deltas read
    // around each case).
    for (bot, owner) in [
        ("bot-k", "ko"),
        ("bot-mv", "mo"),
        ("bot-i", "io"),
        ("bot-o", "own"),
        ("bot-bulk", "bo"),
        ("bot-rb", "ro"),
        ("bot-rs", "ro"),
        ("bot-sc", "so"),
        ("bot-v", "vo"),
        ("bot-b", "bbo"),
    ] {
        h.driver.seed_owned(bot, owner).await;
    }
    h.driver
        .seed_humans(&["ko", "mo", "io", "bo", "ro", "so", "vo", "bbo", "m2", "over-limit"])
        .await;

    same_key_changed_payload_conflicts(h).await;
    no_difference_sync_still_persists_receipt(h).await;
    move_replays_and_old_sync_replay_do_not_resurrect(h).await;
    team_intersection_and_other_team_union(h).await;
    owner_edge_coexists_and_owner_never_listable(h).await;
    revoked_snapshot_restores_through_sync(h).await;
    snapshot_limit_boundary(h).await;
    injected_commit_failure_rolls_back_everything(h).await;
    credential_scopes_fail_closed(h).await;
    validation_branches_fail_closed(h).await;
}