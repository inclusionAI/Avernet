//! Shared SQL lifecycle contract scenarios.
use super::*;

pub fn retire_mutation_provider(provider_id: &str) -> AuthorityMutation {
    AuthorityMutation {
        mutation_id: format!("retire-{provider_id}-1"),
        actor: AuthorityActor::Provider {
            provider_id: provider_id.to_string(),
        },
    }
}

pub fn retire_mutation_human(owner: &str) -> AuthorityMutation {
    AuthorityMutation {
        mutation_id: format!("retire-human-{owner}"),
        actor: AuthorityActor::Human {
            user_id: owner.to_string(),
        },
    }
}

pub(super) async fn seed_pending_transfer(fixture: &Fixture, bot_id: &str, target: &str) {
    // +2 days in the DB clock domain of each flavor.
    let expires_at = match fixture.flavor {
        DbSqlFlavor::Mysql => "DATE_ADD(CURRENT_TIMESTAMP, INTERVAL 2 DAY)",
        DbSqlFlavor::Sqlite => "datetime('now', '+2 days')",
    };
    fixture
        .db
        .execute(DbStatement::with_params(
            &format!(
                "INSERT INTO bot_ownership_transfers \
             (env, bot_id, target_owner_actor_id, from_owner_actor_id, \
              expected_ownership_version, status, requested_at, \
              expires_at, bot_name_snapshot) \
             VALUES (?, ?, ?, '11111111', 1, 'pending', \
                     CURRENT_TIMESTAMP, {expires_at}, \
                     'snapshot')"
            ),
            vec![
                resolve_env().as_str().into(),
                bot_id.into(),
                target.into(),
            ],
        ))
        .await
        .expect("seed pending transfer");
}

pub(super) async fn edge_status(
    fixture: &Fixture,
    bot_id: &str,
    owner: &str,
) -> String {
    column(
        &fixture.db,
        "SELECT status FROM edge_grants WHERE env = ? AND to_id = ? \
         AND from_id = ? AND grant_kind = 'owner' AND grant_ref_id = 0",
        vec![resolve_env().as_str().into(), bot_id.into(), owner.into()],
        "status",
    )
    .await
    .expect("edge present")
    .as_str()
    .expect("text")
    .to_string()
}

pub(super) async fn audit_rows_count(fixture: &Fixture, bot_id: &str) -> usize {
    fixture
        .db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS c FROM bot_authority_changes WHERE bot_id = ?",
            vec![bot_id.into()],
        ))
        .await
        .expect("audit count")[0]
        .get("c")
        .expect("count")
        .as_i64()
        .expect("int") as usize
}

pub(super) async fn audit_actions(fixture: &Fixture, bot_id: &str) -> Vec<String> {
    fixture
        .db
        .query(DbStatement::with_params(
            "SELECT action FROM bot_authority_changes \
             WHERE bot_id = ? ORDER BY sequence",
            vec![bot_id.into()],
        ))
        .await
        .expect("audit actions")
        .iter()
        .map(|row| bcs_db_api::db_get_column::<String>(row, "action").unwrap())
        .collect()
}

/// (action, actor_kind, actor_id) pairs ordered by sequence —
/// the retire audit carries the REAL initiator; the closure
/// audits carry the fixed system marker.
pub(super) async fn audit_actor_pairs(
    fixture: &Fixture,
    bot_id: &str,
) -> Vec<(String, String, String)> {
    fixture
        .db
        .query(DbStatement::with_params(
            "SELECT action, actor_kind, actor_id FROM bot_authority_changes \
             WHERE bot_id = ? ORDER BY sequence",
            vec![bot_id.into()],
        ))
        .await
        .expect("audit rows")
        .iter()
        .map(|row| {
            (
                bcs_db_api::db_get_column::<String>(row, "action").unwrap(),
                bcs_db_api::db_get_column::<String>(row, "actor_kind").unwrap(),
                bcs_db_api::db_get_column::<String>(row, "actor_id").unwrap(),
            )
        })
        .collect()
}

pub async fn provider_retire_metadata_branch_commits_the_whole_state(fixture: &Fixture) {
    fixture
        .provider_repo
        .create_provider_bot_with_ownership(
            provider_record_ref("bot_pr1", BotConnectionMode::Plugin, "ret-ref"),
            capabilities(),
            "11111111",
            "plugin-token",
            mutation("11111111"),
        )
        .await
        .expect("registered");
    seed_pending_transfer(&fixture, "bot_pr1", "human_22222222").await;

    let retirement = fixture
        .provider_repo
        .retire_with_authority(ProviderRetire {
            provider_id: "provider-a".into(),
            bot_id: "bot_pr1".into(),
            provider_bot_ref: "ret-ref".into(),
            mutation: retire_mutation_provider("provider-a"),
            expected_branch: ProviderRetireBranch::Metadata,
        })
        .await
        .expect("retired");
    assert!(retirement.changed);
    assert_eq!(retirement.branch, ProviderRetireBranch::Metadata);

    // Tombstone (the Provider/ref tombstone IS the Bot row
    // tombstone in the metadata branch).
    assert_eq!(
        column(&fixture.db,
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = 'bot_pr1'",
            vec![], "is_deleted").await.unwrap().as_i64().unwrap(),
        1
    );
    // Owner edge revoked in the SAME transaction.
    assert_eq!(
        edge_status(&fixture, "bot_pr1", "human_11111111").await,
        "revoked"
    );
    // Pending closed with the fixed system marker and reason.
    let rows = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT status, decided_by_kind, decided_by_id, reason \
             FROM bot_ownership_transfers WHERE bot_id = 'bot_pr1'",
            vec![]))
        .await
        .expect("transfer");
    let row = &rows[0];
    let status: String =
        bcs_db_api::db_get_column(row, "status").unwrap();
    let decided_kind: String =
        bcs_db_api::db_get_column(row, "decided_by_kind").unwrap();
    let decided_id: String =
        bcs_db_api::db_get_column(row, "decided_by_id").unwrap();
    let reason: String =
        bcs_db_api::db_get_column(row, "reason").unwrap();
    assert_eq!(status, "invalidated");
    assert_eq!(decided_kind, "system");
    assert_eq!(decided_id, "system_transfer_close");
    assert_eq!(reason, "bot_retired");
    // The audits: retire with the REAL Provider actor plus the
    // system closure; the provider token never appears.
    // initialize (registration) + retire + one closure audit.
    let audit_count = audit_rows_count(&fixture, "bot_pr1").await;
    assert_eq!(audit_count, 3);
    let actions = audit_actions(&fixture, "bot_pr1").await;
    // The registration's `initialize` row stays; the retire
    // + system closure follow.
    assert_eq!(
        actions,
        vec![
            "initialize".to_string(),
            "retire".to_string(),
            "transfer_invalidate".to_string(),
        ]
    );
    let pairs: Vec<_> = audit_actor_pairs(&fixture, "bot_pr1")
        .await
        .into_iter()
        .filter(|(action, _, _)| action != "initialize")
        .collect();
    assert_eq!(
        pairs,
        vec![
            ("retire".to_string(), "provider".to_string(),
             "provider_provider-a".to_string()),
            ("transfer_invalidate".to_string(), "system".to_string(),
             "system_transfer_close".to_string()),
        ]
    );
}

pub async fn provider_retire_failure_is_atomic(fixture: &Fixture) {
    fixture
        .provider_repo
        .create_provider_bot_with_ownership(
            provider_record_ref("bot_pr2", BotConnectionMode::Plugin, "ret-fail"),
            capabilities(),
            "11111111",
            "plugin-token2",
            mutation("11111111"),
        )
        .await
        .expect("registered");
    // Inject the retire audit failure: the WHOLE transaction
    // (tombstone, binding disable, edge revocation, pending
    // closure) rolls back — Bot, projection, edges and
    // pending are untouched.
    fixture
        .db
        .arm("INSERT INTO bot_authority_changes \
              (env, bot_id, mutation_id, sequence, action, actor_kind, \
               actor_id, subject_id, before_state, after_state) \
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?");
    let outcome = fixture
        .provider_repo
        .retire_with_authority(ProviderRetire {
            provider_id: "provider-a".into(),
            bot_id: "bot_pr2".into(),
            provider_bot_ref: "ret-fail".into(),
            mutation: retire_mutation_provider("provider-a"),
            expected_branch: ProviderRetireBranch::Metadata,
        })
        .await;
    assert!(outcome.is_err(), "the injected audit failure is an error");
    assert_eq!(
        column(&fixture.db,
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = 'bot_pr2'",
            vec![], "is_deleted").await.unwrap().as_i64().unwrap(),
        0
    );
    assert_eq!(
        edge_status(&fixture, "bot_pr2", "human_11111111").await,
        "approved"
    );
    assert_eq!(
        audit_rows_count(&fixture, "bot_pr2").await,
        1, // only the registration's `initialize` audit
    );
}

pub async fn provider_retire_retry_is_idempotent_without_duplicate_audit(fixture: &Fixture) {
    fixture
        .provider_repo
        .create_provider_bot_with_ownership(
            provider_record_ref("bot_pr3", BotConnectionMode::Plugin, "ret-re"),
            capabilities(),
            "11111111",
            "plugin-token3",
            mutation("11111111"),
        )
        .await
        .expect("registered");
    let params = |mutation: AuthorityMutation| ProviderRetire {
        provider_id: "provider-a".into(),
        bot_id: "bot_pr3".into(),
        provider_bot_ref: "ret-re".into(),
        mutation,
        expected_branch: ProviderRetireBranch::Metadata,
    };
    let first = fixture
        .provider_repo
        .retire_with_authority(params(retire_mutation_provider("provider-a")))
        .await
        .expect("retired");
    assert!(first.changed);
    let second = fixture
        .provider_repo
        .retire_with_authority(params(retire_mutation_provider("provider-a")))
        .await
        .expect("retry");
    // The already-tombstoned retire returns the SAME
    // identity/location data with changed=false and NO
    // duplicate retire audit.
    assert!(!second.changed);
    assert_eq!(first.bot_id, second.bot_id);
    assert_eq!(first.branch, second.branch);
    assert_eq!(
        audit_rows_count(&fixture, "bot_pr3").await,
        2,
        "no duplicate retire audit on retry"
    );
    let retire_rows = audit_actions(&fixture, "bot_pr3")
        .await
        .into_iter()
        .filter(|action| action == "retire")
        .count();
    assert_eq!(retire_rows, 1, "the retire audit is written once");
}

pub async fn provider_retire_binding_fallback_branch_disables_binding(fixture: &Fixture) {
    // A legacy gateway Bot BEFORE metadata backfill: no
    // metadata record, only the compatibility binding.
    fixture
        .bot_repo
        .create_registration_if_absent(
            "bot_fb1".to_string(),
            capabilities(),
            "11111111",
            "fb-token",
        )
        .await.expect("runtime bot");
    // Seed the legacy binding + an owner edge (pre-authority lore).
    fixture
        .db
        .execute(DbStatement::with_params(
            "INSERT INTO bcs_provider_bot_bindings \
             (bot_uuid, env, provider_id, provider_bot_ref, disabled) \
             VALUES ('bot_fb1', ?, 'provider-a', 'fb-ref', 0)",
            vec![resolve_env().as_str().into()],
        ))
        .await
        .expect("binding");
    fixture
        .db
        .execute(DbStatement::with_params(
            &format!(
                "{} INTO edge_grants \
             (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
              originator_policy_type) \
             VALUES (?, 'human_11111111', 'bot_fb1', 'owner', 0, \
                     NULL, 'approved', 'same_as_from')",
                match fixture.flavor {
                    DbSqlFlavor::Mysql => "INSERT IGNORE",
                    DbSqlFlavor::Sqlite => "INSERT OR IGNORE",
                }
            ),
            vec![resolve_env().as_str().into()],
        ))
        .await
        .expect("edge");
    let retirement = fixture
        .provider_repo
        .retire_with_authority(ProviderRetire {
            provider_id: "provider-a".into(),
            bot_id: "bot_fb1".into(),
            provider_bot_ref: "fb-ref".into(),
            mutation: retire_mutation_provider("provider-a"),
            expected_branch: ProviderRetireBranch::BindingFallback,
        })
        .await
        .expect("retired via binding");
    assert!(retirement.changed);
    assert_eq!(retirement.branch, ProviderRetireBranch::BindingFallback);
    assert_eq!(
        column(&fixture.db,
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = 'bot_fb1'",
            vec![], "is_deleted").await.unwrap().as_i64().unwrap(),
        1
    );
    let disabled = column(&fixture.db,
        "SELECT disabled FROM bcs_provider_bot_bindings WHERE bot_uuid = 'bot_fb1'",
        vec![], "disabled").await.unwrap().as_i64().unwrap();
    assert_eq!(disabled, 1, "the binding carries the retry identity");
    // The tombstone retains the branch identity: the retry
    // re-derives the SAME branch with changed = false.
    let retry = fixture
        .provider_repo
        .retire_with_authority(ProviderRetire {
            provider_id: "provider-a".into(),
            bot_id: "bot_fb1".into(),
            provider_bot_ref: "fb-ref".into(),
            mutation: retire_mutation_provider("provider-a"),
            expected_branch: ProviderRetireBranch::BindingFallback,
        })
        .await
        .expect("retry");
    assert!(!retry.changed);
    assert_eq!(retry.branch, ProviderRetireBranch::BindingFallback);
}

pub async fn provider_retire_legacy_unbound_and_branch_mismatch(fixture: &Fixture) {
    fixture
        .bot_repo
        .create_registration_if_absent(
            "legacy-suffix:11111111".to_string(),
            capabilities(),
            "11111111",
            "legacy-token",
        )
        .await.expect("legacy bot");
    // No metadata, no binding; the owner-suffix shape holds.
    fixture
        .provider_repo
        .retire_with_authority(ProviderRetire {
            provider_id: "provider-a".into(),
            bot_id: "legacy-suffix:11111111".into(),
            provider_bot_ref: "legacy-suffix:11111111".into(),
            mutation: retire_mutation_provider("provider-a"),
            expected_branch: ProviderRetireBranch::LegacyUnbound,
        })
        .await
        .expect("retired via legacy unbound");
    assert_eq!(
        column(&fixture.db,
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = 'legacy-suffix:11111111'",
            vec![], "is_deleted").await.unwrap().as_i64().unwrap(),
        1
    );

    // A non-owner-suffixed Bot can NEVER take the LegacyUnbound
    // branch: the branch expectation mismatches the durable
    // state instead of silently reinterpreting it.
    fixture
        .bot_repo
        .create_registration_if_absent(
            "plain_bot".to_string(),
            capabilities(),
            "11111111",
            "plain-token",
        )
        .await.expect("plain bot");
    let mismatch = fixture
        .provider_repo
        .retire_with_authority(ProviderRetire {
            provider_id: "provider-a".into(),
            bot_id: "plain_bot".into(),
            provider_bot_ref: "plain_bot".into(),
            mutation: retire_mutation_provider("provider-a"),
            expected_branch: ProviderRetireBranch::LegacyUnbound,
        })
        .await;
    assert!(mismatch.is_err(), "branch mismatch is an error");
    assert_eq!(
        column(&fixture.db,
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = 'plain_bot'",
            vec![], "is_deleted").await.unwrap().as_i64().unwrap(),
        0
    );
}

pub async fn normal_retire_revokes_authority_and_never_chains_provider(fixture: &Fixture) {
    fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_norm".to_string(),
            capabilities(),
            "11111111",
            "norm-token",
            mutation("11111111"),
        )
        .await.expect("registered bot");
    seed_pending_transfer(&fixture, "bot_norm", "human_33333333").await;

    let changed = fixture
        .bot_repo
        .retire_with_authority("bot_norm", retire_mutation_human("11111111"))
        .await
        .expect("retired");
    assert!(changed);
    assert_eq!(
        column(&fixture.db,
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = 'bot_norm'",
            vec![], "is_deleted").await.unwrap().as_i64().unwrap(),
        1
    );
    assert_eq!(
        edge_status(&fixture, "bot_norm", "human_11111111").await,
        "revoked"
    );
    // The registration's initialize row stays; the retirement
    // adds the retire (REAL Human initiator) and the system
    // closure audit.
    let rows = audit_actor_pairs(&fixture, "bot_norm").await;
    assert_eq!(
        rows,
        vec![
            ("initialize".to_string(), "human".to_string(),
             "human_11111111".to_string()),
            ("retire".to_string(), "human".to_string(), "human_11111111".to_string()),
            ("transfer_invalidate".to_string(), "system".to_string(),
             "system_transfer_close".to_string()),
        ]
    );

    // The ordinary retire is a DISTINCT command: a
    // provider-managed Bot is refused, never re-deleted.
    fixture
        .provider_repo
        .create_provider_bot_with_ownership(
            provider_record_ref("bot_pm", BotConnectionMode::Gateway, "pm-ref"),
            capabilities(),
            "11111111",
            "pm-token",
            mutation("11111111"),
        )
        .await
        .expect("provider bot");
    let refused = fixture
        .bot_repo
        .retire_with_authority("bot_pm", retire_mutation_human("11111111"))
        .await;
    assert!(refused.is_err(), "provider-managed bots retire through Provider commands");
    assert_eq!(
        column(&fixture.db,
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = 'bot_pm'",
            vec![], "is_deleted").await.unwrap().as_i64().unwrap(),
        0
    );
}

/// A retired Bot NEVER resurrects through the lifecycle
/// write paths: the governance backfill refuses tombstoned
/// IDs (already for pre-authority tombstones); the retire
/// tombstone gets the same refusal, and creation stays
/// create-only. The Human-deletion full-flow competition
/// (deleting a Human that still owns live Bots) is Task 6;
/// this pins the same "no approved owner edge to a dying
/// identity" store invariant on the Bot side.
pub async fn retired_bot_id_never_resurrects_through_lifecycle_writes(fixture: &Fixture) {
    fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_gone".to_string(),
            capabilities(),
            "11111111",
            "gone-token",
            mutation("11111111"),
        )
        .await
        .expect("registered");
    fixture
        .bot_repo
        .retire_with_authority("bot_gone", retire_mutation_human("11111111"))
        .await
        .expect("retired");

    // Governance initialization refuses the tombstoned ID.
    let backfill = fixture
        .bot_repo
        .initialize_ownership("bot_gone", "11111111", retire_mutation_human("11111111"))
        .await;
    assert!(
        backfill.is_err(),
        "initialize never resurrects a retired Bot"
    );
    assert_eq!(
        column(&fixture.db,
            "SELECT COUNT(*) AS c FROM edge_grants WHERE to_id = 'bot_gone' \
             AND status = 'approved'",
            vec![], "c").await.unwrap().as_i64().unwrap(),
        0,
        "no approved owner edge exists to the retiring identity"
    );
    // Atomic re-creation stays create-only: a reused retired
    // ID never resets anything and never comes back alive.
    assert!(
        !fixture
            .bot_repo
            .create_registration_if_absent_with_ownership(
                "bot_gone".to_string(),
                capabilities(),
                "11111111",
                "another-token",
                mutation("11111111"),
            )
            .await
            .expect("create-only"),
    );
    assert_eq!(
        column(&fixture.db,
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = 'bot_gone'",
            vec![], "is_deleted").await.unwrap().as_i64().unwrap(),
        1
    );
}

/// The retire ↔ accept barrier (spec §6.3: accept and Bot
/// retirement serialize on the same Bot row): the retirement
/// closes the pending first; the later accept decision sees
/// terminal state and refuses — one side wins, the other
/// observes, no half-way resurrection of the pending.
pub async fn retire_serializes_against_the_transfer_accept(fixture: &Fixture) {
    fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_race".to_string(),
            capabilities(),
            "11111111",
            "race-token",
            mutation("11111111"),
        )
        .await
        .expect("registered");
    seed_pending_transfer(&fixture, "bot_race", "human_22222222").await;

    fixture
        .bot_repo
        .retire_with_authority("bot_race", retire_mutation_human("11111111"))
        .await
        .expect("retired first (serialized)");

    let accept = fixture
        .authority
        .change_transfer(TransferCommand {
            key: TransferKey {
                bot_id: "bot_race".to_string(),
                target_owner_actor_id: "human_22222222".to_string(),
            },
            action: TransferAction::Accept,
            mutation: AuthorityMutation {
                mutation_id: "accept-after-retire".to_string(),
                actor: AuthorityActor::Human {
                    user_id: "22222222".to_string(),
                },
            },
        })
        .await;
    assert!(
        accept.is_err(),
        "the accept cannot act on the retired Bot's closed pending"
    );
    assert_eq!(
        column(&fixture.db,
            "SELECT status FROM bot_ownership_transfers \
             WHERE bot_id = 'bot_race'",
            vec![], "status").await.unwrap().as_str().expect("text"),
        "invalidated",
        "the pending stays closed"
    );
}
