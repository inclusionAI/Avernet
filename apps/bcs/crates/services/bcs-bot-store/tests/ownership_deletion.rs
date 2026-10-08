//! Deletion half of the Task 5 atomic ownership lifecycle boundary:
//! retirement (role-edge withdrawal + pending-transfer termination +
//! soft delete, audited in the same commit), leader-wins semantics
//! against the ownership-acceptance probe, and the Human deletion
//! boundary (live-owner protection, no orphan edges under grant races).
//! Shares the support harness with `ownership_lifecycle.rs`.

use support::*;

#[path = "common/ownership.rs"]
mod support;


// ---------------------------------------------------------------------------
// Deletion boundary: retirement withdraws every role edge, terminates the
// pending transfer slot and soft-deletes — all in one committed transaction
// whose failed audit step rolls everything back.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn sqlite_retirement_is_atomic_and_its_failing_audit_leaves_the_bot_live() {
    let db = sqlite().await;
    let injected = InjectedStepDb::new(db.clone());
    let repo = persistent(injected.clone());
    assert!(
        repo.create_registration_if_absent_with_initialization(
            "bot-ret".into(),
            caps("retire"),
            "owner-registration",
            "token-ret",
            human_init("user-1"),
        )
        .await
        .unwrap()
    );
    seed_manager_edge(db.as_ref(), "bot-ret", "user-mgr").await;
    let transfer_id = seed_pending_transfer(db.as_ref(), "bot-ret", "user-1", "user-2").await;
    injected.arm(DELETE_AUDIT_MARKER);
    assert!(
        repo.retire_bot_lifecycle("bot-ret", operation("user-1"))
            .await
            .is_err(),
        "an audit failure must fail the whole retirement"
    );
    // Nothing from the retirement survived: the bot is live, edges approved,
    // the pending slot still pending (real row projections).
    assert!(!is_deleted(db.as_ref(), "bot-ret").await);
    assert_eq!(
        approved_role_edge_count_to(db.as_ref(), "bot-ret").await,
        2
    );
    let (_, status, reason) = pending_transfer_row(db.as_ref(), "bot-ret").await;
    assert_eq!(status, "pending");
    assert_eq!(reason, None);
    assert!(repo.try_get("bot-ret").await.unwrap().is_some());
    injected.disarm();
    assert!(repo.retire_bot_lifecycle("bot-ret", operation("user-1")).await.unwrap());
    assert!(is_deleted(db.as_ref(), "bot-ret").await);
    assert_eq!(approved_role_edge_count_to(db.as_ref(), "bot-ret").await, 0);
    let (_, status, reason) = pending_transfer_row(db.as_ref(), "bot-ret").await;
    assert_eq!(status, "invalidated");
    assert_eq!(reason.as_deref(), Some("bot_deleted"));
    assert_eq!(
        scalar(
            db.as_ref(),
            "SELECT COUNT(*) AS value FROM bot_ownership_transfers WHERE transfer_id = ? \
             AND decision_actor_kind IS NOT NULL AND decided_by IS NOT NULL \
             AND decided_at IS NOT NULL AND result_owner_version IS NULL",
            vec![transfer_id.into()],
        )
        .await,
        1,
        "invalidation fills the decision columns required by the frozen schema"
    );
    assert_eq!(
        scalar(
            db.as_ref(),
            "SELECT COUNT(*) AS value FROM bcs_bot_action_audits \
             WHERE resource_id = 'bot-ret' AND action = 'delete' AND phase = 'applied'",
            vec![],
        )
        .await,
        1,
        "exactly one lifecycle audit row is written by the successful retirement"
    );
    // A second retirement reports nothing left to retire.
    assert!(!repo.retire_bot_lifecycle("bot-ret", operation("user-1")).await.unwrap());
    assert!(repo.try_get("bot-ret").await.unwrap().is_none());
    assert!(repo.find_bot_by_token("token-ret").await.is_none());
}

#[tokio::test]
async fn sqlite_acceptance_cannot_resurrect_a_retired_bot_in_either_order() {
    // Winner A: retirement commits first — the probe must fail and leave no
    // trace of an attempted acceptance.
    {
        let db = sqlite().await;
        let repo = persistent(db.clone());
        assert!(
            repo.create_registration_if_absent_with_initialization(
                "bot-race".into(),
                caps("accept"),
                "owner-registration",
                "token-race",
                human_init("user-1"),
            )
            .await
            .unwrap()
        );
        seed_pending_transfer(db.as_ref(), "bot-race", "user-1", "user-2").await;
        assert!(repo.retire_bot_lifecycle("bot-race", operation("user-1")).await.unwrap());
        let probe = probe_accept_ownership(db.as_ref(), "bot-race", "user-2", &{
            pending_transfer_row(db.as_ref(), "bot-race").await.0
        })
        .await;
        assert!(probe.is_err(), "a deleted Bot can never be accepted onto");
        assert_eq!(ownership_version_of(db.as_ref(), "bot-race").await, 1);
        let (_, status, reason) = pending_transfer_row(db.as_ref(), "bot-race").await;
        assert_eq!(status, "invalidated");
        assert_eq!(reason.as_deref(), Some("bot_deleted"));
        assert!(is_deleted(db.as_ref(), "bot-race").await);
    }
    // Winner B: acceptance commits first — retirement still terminates the
    // (already accepted) surface and revokes the swapped-in owner edge.
    {
        let db = sqlite().await;
        let repo = persistent(db.clone());
        assert!(
            repo.create_registration_if_absent_with_initialization(
                "bot-race".into(),
                caps("accept"),
                "owner-registration",
                "token-race",
                human_init("user-1"),
            )
            .await
            .unwrap()
        );
        let transfer_id = seed_pending_transfer(db.as_ref(), "bot-race", "user-1", "user-2").await;
        probe_accept_ownership(db.as_ref(), "bot-race", "user-2", &transfer_id)
            .await
            .unwrap();
        assert_eq!(ownership_version_of(db.as_ref(), "bot-race").await, 2);
        assert!(repo.retire_bot_lifecycle("bot-race", operation("user-1")).await.unwrap());
        assert!(is_deleted(db.as_ref(), "bot-race").await);
        assert_eq!(approved_role_edge_count_to(db.as_ref(), "bot-race").await, 0);
        let (_, status, _) = pending_transfer_row(db.as_ref(), "bot-race").await;
        assert_eq!(status, "accepted", "retirement never rewrites a decided transfer");
        assert_eq!(ownership_version_of(db.as_ref(), "bot-race").await, 2);
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn sqlite_retirement_races_the_acceptance_probe_with_clear_winner_semantics() {
    for _ in 0..8 {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("race.sqlite");
        sqlite_file(&path).await;
        let repo_db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap());
        let probe_db = Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap());
        let repo = persistent(repo_db.clone());
        assert!(
            repo.create_registration_if_absent_with_initialization(
                "bot-race".into(),
                caps("accept"),
                "owner-registration",
                "token-race",
                human_init("user-1"),
            )
            .await
            .unwrap()
        );
        let transfer_id = seed_pending_transfer(repo_db.as_ref(), "bot-race", "user-1", "user-2").await;
        let barrier = Arc::new(Barrier::new(2));
        let retire_task = {
            let barrier = barrier.clone();
            let repo = persistent(Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap()));
            tokio::spawn(async move {
                barrier.wait().await;
                repo.retire_bot_lifecycle("bot-race", operation("user-1")).await.unwrap()
            })
        };
        let probe_task = {
            let barrier = barrier.clone();
            let db = probe_db.clone();
            let transfer_id = transfer_id.clone();
            tokio::spawn(async move {
                barrier.wait().await;
                db.transaction(vec![
                    DbTransactionStep::Query(DbStatement::with_params(
                        "SELECT bot_uuid FROM bcs_bots \
                         WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0",
                        vec!["bot-race".into(), bcs_config::resolve_env_str().into()],
                    )),
                    DbTransactionStep::ExecuteChecked {
                        statement: DbStatement::with_params(
                            "UPDATE bcs_bots SET ownership_version = ownership_version + 1 \
                             WHERE bot_uuid = ? AND env = ? AND ownership_version = 1 \
                               AND COALESCE(is_deleted, 0) = 0",
                            vec!["bot-race".into(), bcs_config::resolve_env_str().into()],
                        ),
                        expected_affected_rows: 1,
                    },
                    DbTransactionStep::ExecuteChecked {
                        statement: DbStatement::with_params(
                            "UPDATE bot_ownership_transfers SET status = 'accepted', \
                             decision_actor_kind = 'human', decided_by = 'user-2', \
                             decided_at = CURRENT_TIMESTAMP, result_owner_version = 2, \
                             gmt_modified = CURRENT_TIMESTAMP \
                             WHERE transfer_id = ? AND status = 'pending'",
                            vec![transfer_id.into()],
                        ),
                        expected_affected_rows: 1,
                    },
                ])
                .await
                .map(|_| ())
            })
        };
        let (_retired, probed) = (retire_task.await.unwrap(), probe_task.await);
        // Leader-wins semantics: whatever interleaving ran, both committed
        // flows agree on the final state (real query projections).
        assert!(is_deleted(probe_db.as_ref(), "bot-race").await, "retirement always commits");
        let probe_won = probed.is_ok();
        let (_, status, reason) = pending_transfer_row(probe_db.as_ref(), "bot-race").await;
        match status.as_str() {
            "invalidated" => {
                assert!(!probe_won, "a failed probe must never leave a decided transfer");
                assert_eq!(reason.as_deref(), Some("bot_deleted"));
                assert_eq!(ownership_version_of(probe_db.as_ref(), "bot-race").await, 1);
            }
            "accepted" => {
                assert!(probe_won, "only a committed probe may mark the transfer accepted");
                assert_eq!(ownership_version_of(probe_db.as_ref(), "bot-race").await, 2);
            }
            other => panic!("transfer must be terminal after the race, got {other}"),
        }
        assert_eq!(
            approved_role_edge_count_to(probe_db.as_ref(), "bot-race").await,
            0,
            "no approved edge may survive a retired Bot"
        );
    }
}

#[tokio::test]
async fn memory_retirement_terminates_pending_transfers_and_withdraws_every_role_edge() {
    let temp = tempfile::tempdir().unwrap();
    let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().into()));
    assert!(
        repo.create_registration_if_absent_with_initialization(
            "bot-ret".into(),
            caps("retire"),
            "owner-registration",
            "token-ret",
            human_init("user-1"),
        )
        .await
        .unwrap()
    );
    repo.ensure_human_actor("user-mgr", "User Mgr").await.unwrap();
    repo.mutate_manager(
        AuditActor::Human {
            user_id: "user-1".into(),
        },
        "bot-ret",
        bcs_service_api::types::ManagerMutation::GrantDirect {
            user_id: "user-mgr".into(),
        },
    )
    .await
    .unwrap();
    repo.seed_authority_pending_transfer("bot-ret", "user-1", "user-2")
        .await
        .unwrap();
    assert!(
        repo.retire_bot_lifecycle("bot-ret", operation("user-1"))
            .await
            .unwrap()
    );
    assert!(repo.get("bot-ret").await.is_none());
    assert!(repo.load_token("bot-ret").await.is_none());
    let statuses = repo.authority_transfer_statuses("bot-ret").await.unwrap();
    assert_eq!(statuses.len(), 1);
    assert_eq!(statuses[0].1, "invalidated");
    assert_eq!(statuses[0].2.as_deref(), Some("bot_deleted"));
    assert!(
        repo.role("user-1", "bot-ret").await.unwrap().is_none()
            && repo.role("user-mgr", "bot-ret").await.unwrap().is_none(),
        "retirement withdraws the owner and manager edges together"
    );
    let records = repo.authority_action_audit_records().await.unwrap();
    assert_eq!(records.len(), 1);
    assert_eq!(records[0].resource_id, "bot-ret");
    assert_eq!(records[0].step_key, "delete/bot/applied");
    assert!(!repo.retire_bot_lifecycle("bot-ret", operation("user-1")).await.unwrap());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn memory_retirement_races_grants_with_no_orphan_edges_or_late_publication() {
    for round in 0..8 {
        let temp = tempfile::tempdir().unwrap();
        let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().join(round.to_string())));
        assert!(
            repo.create_registration_if_absent_with_initialization(
                "bot-race".into(),
                caps("retire"),
                "owner-registration",
                "token-ret",
                human_init("user-1"),
            )
            .await
            .unwrap()
        );
        repo.ensure_human_actor("user-mgr", "User Mgr").await.unwrap();
        let barrier = Arc::new(Barrier::new(2));
        let grant_repo = repo.clone();
        let retire_repo = repo.clone();
        let grant_barrier = barrier.clone();
        let retire_barrier = barrier.clone();
        let grant = tokio::spawn(async move {
            grant_barrier.wait().await;
            grant_repo
                .mutate_manager(
                    AuditActor::Human {
                        user_id: "user-1".into(),
                    },
                    "bot-race",
                    bcs_service_api::types::ManagerMutation::GrantDirect {
                        user_id: "user-mgr".into(),
                    },
                )
                .await
        });
        let retire = tokio::spawn(async move {
            retire_barrier.wait().await;
            retire_repo
                .retire_bot_lifecycle("bot-race", operation("user-1"))
                .await
        });
        let _grant_result = grant.await.unwrap();
        assert!(retire.await.unwrap().unwrap(), "retirement always commits last");
        assert!(repo.get("bot-race").await.is_none());
        // No orphan edges in any interleaving (real role reads).
        assert!(repo.role("user-1", "bot-race").await.unwrap().is_none());
        assert!(repo.role("user-mgr", "bot-race").await.unwrap().is_none());
        assert_eq!(repo.authority_transfer_statuses("bot-race").await.unwrap().len(), 0);
    }
}

// ---------------------------------------------------------------------------
// Human deletion boundary: a live owner is undeletable; a deletable Human
// loses every held edge; grants racing the deletion never leave orphans.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn sqlite_live_owner_human_is_undeletable_until_owned_bots_are_retired() {
    let db = sqlite().await;
    let repo = persistent(db.clone());
    assert!(
        repo.create_registration_if_absent_with_initialization(
            "bot-owned".into(),
            caps("owned"),
            "owner-registration",
            "token-owned",
            human_init("user-owner"),
        )
        .await
        .unwrap()
    );
    // The live owner of a live Bot cannot be deleted.
    let error = repo
        .delete_human_actor("user-owner", operation("admin-1"))
        .await
        .unwrap_err();
    assert!(matches!(
        error,
        ServiceError::Authority(AuthorityError::Forbidden(_))
    ));
    assert!(
        repo.try_get("human_user-owner").await.unwrap().is_some(),
        "the refused deletion must not touch the Human row"
    );
    assert_eq!(approved_owner_edge_count(db.as_ref(), "bot-owned").await, 1);
    // Once the owned Bot is retired, the same Human deletes atomically.
    assert!(repo.retire_bot_lifecycle("bot-owned", operation("user-owner")).await.unwrap());
    assert!(repo.delete_human_actor("user-owner", operation("admin-1")).await.unwrap());
    assert!(repo.try_get("human_user-owner").await.unwrap().is_none());
    assert_eq!(
        approved_role_edge_count_from(db.as_ref(), "human_user-owner").await,
        0
    );
    assert_eq!(
        scalar(
            db.as_ref(),
            "SELECT COUNT(*) AS value FROM bcs_bot_action_audits \
             WHERE resource_id = 'human_user-owner' AND action = 'delete'",
            vec![],
        )
        .await,
        1,
        "the Human deletion writes its lifecycle audit row in the same commit"
    );
    // A second deletion of the same Human is a plain false.
    assert!(!repo.delete_human_actor("user-owner", operation("admin-1")).await.unwrap());
}

#[tokio::test]
async fn sqlite_human_deletion_withdraws_held_subject_edges_without_orphans() {
    let db = sqlite().await;
    let repo = persistent(db.clone());
    assert!(
        repo.create_registration_if_absent_with_initialization(
            "bot-a".into(),
            caps("a"),
            "owner-registration",
            "token-a",
            human_init("user-owner"),
        )
        .await
        .unwrap()
    );
    assert!(
        repo.create_registration_if_absent_with_initialization(
            "bot-b".into(),
            caps("b"),
            "owner-registration",
            "token-b",
            human_init("user-owner"),
        )
        .await
        .unwrap()
    );
    assert!(repo.ensure_human_actor("user-mgr", "Manager").await.unwrap().created);
    seed_manager_edge(db.as_ref(), "bot-a", "user-mgr").await;
    seed_manager_edge(db.as_ref(), "bot-b", "user-mgr").await;
    assert_eq!(
        approved_role_edge_count_from(db.as_ref(), "human_user-mgr").await,
        2
    );
    assert!(repo.delete_human_actor("user-mgr", operation("admin-1")).await.unwrap());
    assert_eq!(
        approved_role_edge_count_from(db.as_ref(), "human_user-mgr").await,
        0,
        "every held manager edge is withdrawn with the Human"
    );
    for bot in ["bot-a", "bot-b"] {
        assert!(
            repo.try_get(bot).await.unwrap().is_some(),
            "the owned bots survive their manager's deletion"
        );
        assert_eq!(approved_owner_edge_count(db.as_ref(), bot).await, 1);
    }
    assert!(repo.try_get("human_user-mgr").await.unwrap().is_none());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn memory_human_deletion_races_manager_grants_without_orphan_edges() {
    for round in 0..8 {
        let temp = tempfile::tempdir().unwrap();
        let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().join(round.to_string())));
        assert!(
            repo.create_registration_if_absent_with_initialization(
                "bot-race".into(),
                caps("race"),
                "owner-registration",
                "token-race",
                human_init("user-owner"),
            )
            .await
            .unwrap()
        );
        repo.ensure_human_actor("user-mgr", "Manager").await.unwrap();
        let barrier = Arc::new(Barrier::new(2));
        let grant_repo = repo.clone();
        let delete_repo = repo.clone();
        let grant_barrier = barrier.clone();
        let delete_barrier = barrier.clone();
        let grant = tokio::spawn(async move {
            grant_barrier.wait().await;
            grant_repo
                .mutate_manager(
                    AuditActor::Human {
                        user_id: "user-owner".into(),
                    },
                    "bot-race",
                    bcs_service_api::types::ManagerMutation::GrantDirect {
                        user_id: "user-mgr".into(),
                    },
                )
                .await
                .map(|_| ())
        });
        let deletion = tokio::spawn(async move {
            delete_barrier.wait().await;
            delete_repo
                .delete_human_actor("user-mgr", operation("admin-1"))
                .await
        });
        // In every interleaving the invariants hold (real reads): whichever
        // side won, no approved edge from a deleted Human survives, and a
        // grant racing the deletion either lost fail-closed (InvalidSubject
        // on the deleted Human) or committed before the deletion withdrew
        // the Human's edges.
        let _grant_result = grant.await.unwrap();
        let _deleted = deletion.await.unwrap().unwrap();
        assert!(repo.role("user-mgr", "bot-race").await.unwrap().is_none());
        assert!(repo.try_get("bot-race").await.unwrap().is_some());
        assert_eq!(
            repo.ownership("bot-race").await.unwrap().owner_user_id,
            "user-owner"
        );
    }
}

// ---------------------------------------------------------------------------
// Fail-closed branches around missing, retired and human rows.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn sqlite_missing_retired_and_human_rows_fail_closed() {
    let db = sqlite().await;
    let repo = persistent(db.clone());
    assert!(
        !repo
            .retire_bot_lifecycle("bot-missing", operation("admin-1"))
            .await
            .unwrap()
    );
    let missing = repo.initialize_existing_ownership("bot-missing", human_init("user-1")).await;
    assert_bot_not_found(&missing.unwrap_err());
    assert!(
        repo.create_registration_if_absent_with_initialization(
            "bot-h".into(),
            caps("human-row"),
            "owner-registration",
            "token-h",
            human_init("user-1"),
        )
        .await
        .unwrap()
    );
    // Human actor rows retire through delete_human_actor, never the Bot lane.
    assert!(
        !repo
            .retire_bot_lifecycle("human_user-1", operation("admin-1"))
            .await
            .unwrap()
    );
    assert!(repo.try_get("human_user-1").await.unwrap().is_some());
    assert!(repo.retire_bot_lifecycle("bot-h", operation("admin-1")).await.unwrap());
    assert_bot_not_found(
        &repo
            .initialize_existing_ownership("bot-h", human_init("user-1"))
            .await
            .unwrap_err(),
    );
    assert!(
        repo.ensure_human_actor("user-plain", "Plain").await.unwrap().created
    );
    assert!(repo.delete_human_actor("user-plain", operation("admin-1")).await.unwrap());
    assert!(!repo.delete_human_actor("user-plain", operation("admin-1")).await.unwrap());
}

#[tokio::test]
async fn sqlite_human_deletion_converges_when_a_grant_races_the_first_attempt() {
    // A manager grant commits between the pre-read (held-role count = 1)
    // and the first write attempt: the held-edge expectation drifts and the
    // first attempt rolls back. The deletion must re-read the FRESH held
    // count and converge inside its own retry budget — not exhaust the
    // budget against the stale expectation and surface Conflict.
    let db = sqlite().await;
    let injected = InjectedStepDb::new(db.clone());
    let repo = persistent(injected.clone());
    assert!(
        repo.create_registration_if_absent_with_initialization(
            "bot-race".into(),
            caps("grant-race"),
            "owner-registration",
            "token-race",
            human_init("user-owner"),
        )
        .await
        .unwrap()
    );
    assert!(repo.ensure_human_actor("user-mgr", "Manager").await.unwrap().created);
    seed_manager_edge(db.as_ref(), "bot-race", "user-mgr").await;
    assert_eq!(
        approved_role_edge_count_from(db.as_ref(), "human_user-mgr").await,
        1
    );
    // One-shot racer: a team-sourced manager grant commits on a separate
    // autocommit right before the first deletion attempt's transaction.
    let env = bcs_config::resolve_env_str();
    injected.arm_racing_write(
        "actor_kind = 'human'",
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, \
         status, originator_policy_type, originator_policy_data, \
         management_source_kind, management_source_id) \
         VALUES (?, ?, ?, 'manager', 0, NULL, 'approved', 'same_as_from', NULL, \
                 'team', 'team-race')",
        vec![
            bcs_db_api::DbValue::from(env.as_str()),
            bcs_db_api::DbValue::from("human_user-mgr"),
            bcs_db_api::DbValue::from("bot-race"),
        ],
    );
    // One call: the internal retry loop must absorb the drifted count
    // (this is the RED assertion — the stale-expectation defect ends in
    // Err(Conflict) here).
    let deleted = repo
        .delete_human_actor("user-mgr", operation("admin-1"))
        .await
        .unwrap();
    assert!(
        deleted,
        "the deletion converges within its retry budget after the drifted first attempt"
    );
    assert!(repo.try_get("human_user-mgr").await.unwrap().is_none());
    // Real row projections: the racer's edge and the seeded edge are BOTH
    // withdrawn by the converged deletion, the owned Bot survives, and the
    // audit recorded the one committed deletion.
    assert_eq!(
        approved_role_edge_count_from(db.as_ref(), "human_user-mgr").await,
        0,
        "no edge held by the deleted Human may survive, including the racer's"
    );
    assert!(repo.try_get("bot-race").await.unwrap().is_some());
    assert_eq!(
        approved_owner_edge_count(db.as_ref(), "bot-race").await,
        1
    );
    assert_eq!(
        scalar(
            db.as_ref(),
            "SELECT COUNT(*) AS value FROM bcs_bot_action_audits \
             WHERE resource_id = 'human_user-mgr' AND action = 'delete' AND phase = 'applied'",
            vec![],
        )
        .await,
        1,
        "exactly one lifecycle audit row survives the retried (rolled-back-then-committed) deletion"
    );
}
