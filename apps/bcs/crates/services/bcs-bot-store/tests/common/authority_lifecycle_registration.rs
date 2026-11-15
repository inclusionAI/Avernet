//! Shared SQL lifecycle contract scenarios.
use super::*;

// ---------------------------------------------------------------------------
// v1 Human-token composite (the SQL Bot Repo).
// ---------------------------------------------------------------------------

pub async fn trusted_v1_registration_commits_the_whole_state(fixture: &Fixture) {
    let created = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_abc12345".to_string(),
            capabilities(),
            "11111111",
            "bot-token-1",
            mutation("11111111"),
        )
        .await
        .expect("committed");
    assert!(created);

    // Bot row: version 1, owner as first created_by, token persisted.
    let version = column(
        &fixture.db,
        "SELECT ownership_version AS v, created_by AS cb, session_token AS tok, \
         actor_kind AS kind FROM bcs_bots WHERE bot_uuid = ?",
        vec!["bot_abc12345".into()],
        "v",
    )
    .await
    .expect("version");
    assert_eq!(version.as_i64(), Some(1));
    let (created_by, _): (String, String) = {
        let rows = fixture
            .db
            .query(DbStatement::with_params(
                "SELECT created_by AS cb, session_token AS tok, actor_kind AS kind \
                 FROM bcs_bots WHERE bot_uuid = ?",
                vec!["bot_abc12345".into()],
            ))
            .await
            .expect("row");
        (
            bcs_db_api::db_get_column(&rows[0], "cb").unwrap(),
            bcs_db_api::db_get_column(&rows[0], "tok").unwrap(),
        )
    };
    assert_eq!(created_by, "11111111");
    assert_eq!(
        fixture.bot_repo.load_token("bot_abc12345").await.as_deref(),
        Some("bot-token-1")
    );
    // Runtime was NOT activated by registration.
    assert!(!fixture.bot_repo.is_connected("bot_abc12345").await);

    // Human ACTOR row exists next to the bot row.
    let human = column(
        &fixture.db,
        "SELECT actor_kind FROM bcs_bots WHERE bot_uuid = ?",
        vec!["human_11111111".into()],
        "actor_kind",
    )
    .await;
    let _ = human;
    let actor: Option<String> = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT actor_kind FROM bcs_bots WHERE bot_uuid = ?",
            vec!["human_11111111".into()],
        ))
        .await
        .expect("human row")
        .first()
        .map(|row| bcs_db_api::db_get_column(row, "actor_kind").expect("str"));
    assert_eq!(actor.as_deref(), Some("human"));

    // Owner edge: approved, unified grant_ref_id = 0.
    let edge = fixture
        .authority
        .role("human_11111111", "bot_abc12345")
        .await
        .expect("role");
    assert_eq!(edge, Some(BotAccessRelation::Owner));

    // Default profile: wildcard allow, revision 1, active.
    let profile = fixture
        .profile_port
        .get_active_default("bot_abc12345", &resolve_env())
        .await
        .expect("default profile");
    assert_eq!(profile.revision, 1);
    assert_eq!(profile.is_default, true);
    assert_eq!(
        profile.rules_template,
        serde_json::from_str::<serde_json::Value>(
            bcs_edge_permission_store::lifecycle::TRUSTED_DEFAULT_RULES
        )
        .unwrap()
    );

    // Audit: one initialize row with the real initiator and no token.
    let rows = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT action, actor_kind, actor_id, subject_id, after_state, \
             bot_id FROM bot_authority_changes",
            vec![],
        ))
        .await
        .expect("audit");
    assert_eq!(rows.len(), 1, "exactly one initialize audit row");
    let action: String = bcs_db_api::db_get_column(&rows[0], "action").unwrap();
    assert_eq!(action, "initialize");
    let actor_id: String = bcs_db_api::db_get_column(&rows[0], "actor_id").unwrap();
    assert_eq!(actor_id, "human_11111111");
    let subject: String = bcs_db_api::db_get_column(&rows[0], "subject_id").unwrap();
    assert_eq!(subject, "human_11111111");
    let after: String = bcs_db_api::db_get_column(&rows[0], "after_state").unwrap();
    assert_eq!(
        after,
        "{\"owner\":\"human_11111111\",\"version\":1}"
    );
    let audit_bot: String = bcs_db_api::db_get_column(&rows[0], "bot_id").unwrap();
    assert_eq!(audit_bot, "bot_abc12345");
    assert!(!after.contains("bot-token"));

    // Human idempotence: registering a second bot for the same
    // owner keeps one Human row.
    let second = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_abc99999".to_string(),
            capabilities(),
            "11111111",
            "bot-token-2",
            mutation("11111111"),
        )
        .await
        .expect("second bot");
    assert!(second);
    let humans = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS c FROM bcs_bots WHERE actor_kind = 'human'",
            vec![],
        ))
        .await
        .expect("humans");
    assert_eq!(humans.len(), 1);
}

pub async fn trusted_v1_reused_bot_id_never_resets_owner(fixture: &Fixture) {
    // A version-0 runtime Bot pre-exists.
    assert!(seed_runtime_bot(&fixture, "bot_runtime1").await);
    // Trusted registration on the SAME id is refused (create-only).
    let reused = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_runtime1".to_string(),
            capabilities(),
            "11111111",
            "attacker-token",
            mutation("11111111"),
        )
        .await
        .expect("absent result");
    assert!(!reused);
    // Nothing on the preexisting state moved.
    let rows = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT session_token, created_by, ownership_version FROM bcs_bots \
             WHERE bot_uuid = ?",
            vec!["bot_runtime1".into()],
        ))
        .await
        .expect("row");
    let token: String = bcs_db_api::db_get_column(&rows[0], "session_token").unwrap();
    assert_eq!(token, "runtime-token");
    let version: i64 = bcs_db_api::db_get_column(&rows[0], "ownership_version").unwrap();
    assert_eq!(version, 0, "runtime version-0 entry is preserved");
    let edges = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS c FROM edge_grants",
            vec![],
        ))
        .await
        .expect("edges");
    assert_eq!(edges.len(), 1);
    let count: i64 = bcs_db_api::db_get_column(&edges[0], "c").unwrap();
    assert_eq!(count, 0, "no authority rows added");
}

pub async fn trusted_v1_tombstoned_bot_id_never_resurrects(fixture: &Fixture) {
    assert!(seed_runtime_bot(&fixture, "bot_dead1").await);
    fixture
        .db
        .execute(DbStatement::with_params(
            "UPDATE bcs_bots SET is_deleted = 1 WHERE bot_uuid = ?",
            vec!["bot_dead1".into()],
        ))
        .await
        .expect("tombstone");
    let reused = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_dead1".to_string(),
            capabilities(),
            "11111111",
            "some-token",
            mutation("11111111"),
        )
        .await
        .expect("create-only refusals are not errors");
    assert!(!reused);
    let rows = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = ?",
            vec!["bot_dead1".into()],
        ))
        .await
        .expect("row");
    let deleted: i64 = bcs_db_api::db_get_column(&rows[0], "is_deleted").unwrap();
    assert_eq!(deleted, 1, "still deleted");
}

/// The INSERT-ignore owner-actor write cannot prove an
/// EXISTING owner row is a live Human: a tombstoned owner
/// must refuse the whole transaction (Bot row, owner edge,
/// profile and audit all ride the same rollback).
pub async fn trusted_v1_registration_refuses_a_tombstoned_owner(fixture: &Fixture) {
    // Plant the owner Human actor directly, then tombstone it.
    fixture
        .db
        .execute(DbStatement::with_params(
            "INSERT INTO bcs_bots \
             (bot_uuid, actor_kind, name, env, created_by, visibility, status) \
             VALUES ('human_77777777', 'human', 'Dead Owner', ?, '77777777', \
                     'protected', 'online')",
            vec![resolve_env().into()],
        ))
        .await
        .expect("plant human actor");
    fixture
        .db
        .execute(DbStatement::new(
            "UPDATE bcs_bots SET is_deleted = 1 WHERE bot_uuid = 'human_77777777'",
        ))
        .await
        .expect("tombstone owner");

    let result = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_dead-owner".to_string(),
            capabilities(),
            "77777777",
            "owner-token",
            mutation("77777777"),
        )
        .await;
    assert!(
        result.is_err(),
        "a tombstoned owner must refuse the registration: {result:?}"
    );

    // Nothing leaked: no Bot row, no owner edge, no profile,
    // no audit row for this mutation, and the tombstone stands.
    for (table, where_clause) in [
        ("bcs_bots", "bot_uuid = 'bot_dead-owner'"),
        ("edge_grants", "to_id = 'bot_dead-owner'"),
        ("permission_profiles", "bot_id = 'bot_dead-owner'"),
        ("bot_authority_changes", "bot_id = 'bot_dead-owner'"),
    ] {
        let rows = fixture
            .db
            .query(DbStatement::new(&format!(
                "SELECT COUNT(*) AS c FROM {table} WHERE {where_clause}"
            )))
            .await
            .expect("rollback probe");
        let count: i64 = bcs_db_api::db_get_column(&rows[0], "c").unwrap();
        assert_eq!(count, 0, "no half-written rows in {table}");
    }
    let rows = fixture
        .db
        .query(DbStatement::new(
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = 'human_77777777'",
        ))
        .await
        .expect("human row");
    let deleted: i64 = bcs_db_api::db_get_column(&rows[0], "is_deleted").unwrap();
    assert_eq!(deleted, 1, "tombstone untouched by the refused registration");
}

// Injected failure points (4.1): owner write, profile write and
// audit write abort the WHOLE transaction.
pub async fn owner_edge_failure_rolls_back_everything(fixture: &Fixture) {
    fixture.db.arm("INSERT");
    // The bot-row INSERT is the first statement; arming every
    // insert aborts at the same transaction boundary — scope the
    // injection to the owner edge instead.
    fixture.db.armed.lock().unwrap().clear();
    fixture.db.arm("edge_grants");
    let error = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_fail1".to_string(),
            capabilities(),
            "11111111",
            "bot-token-x",
            mutation("11111111"),
        )
        .await
        .expect_err("owner write failure surfaces");
    assert!(
        error.to_string().contains("trusted bot registration failed"),
        "no SQL text leaks: {error}"
    );
    assert_no_half_registration(&fixture, "bot_fail1").await;
}

pub async fn profile_failure_rolls_back_everything(fixture: &Fixture) {
    fixture.db.arm("permission_profiles");
    let error = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_fail2".to_string(),
            capabilities(),
            "11111111",
            "bot-token-x",
            mutation("11111111"),
        )
        .await
        .expect_err("profile write failure surfaces");
    assert!(error.to_string().contains("trusted bot registration failed"));
    assert_no_half_registration(&fixture, "bot_fail2").await;
}

pub async fn audit_failure_rolls_back_everything(fixture: &Fixture) {
    fixture.db.arm("bot_authority_changes");
    let error = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_fail3".to_string(),
            capabilities(),
            "11111111",
            "bot-token-x",
            mutation("11111111"),
        )
        .await
        .expect_err("audit failure surfaces");
    assert!(error.to_string().contains("trusted bot registration failed"));
    assert_no_half_registration(&fixture, "bot_fail3").await;
}

pub async fn human_actor_write_failure_rolls_back_everything(fixture: &Fixture) {
    // The Human ACTOR insert is identifiable by its column list.
    fixture.db.arm("actor_kind, name, bot_info, session_token");
    let error = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_fail4".to_string(),
            capabilities(),
            "11111111",
            "bot-token-x",
            mutation("11111111"),
        )
        .await
        .expect_err("human create failure surfaces");
    assert!(error.to_string().contains("trusted bot registration failed"));
    assert_no_half_registration(&fixture, "bot_fail4").await;
}

/// Ruling T4-1 acceptance (spec §10.2 literal): a
/// fresh-Human registration creates the owner's Human
/// ACTOR row in the SAME transaction, and that owner can
/// afterwards pass the Task-2 human-existence guards (a
/// manager grant by that owner succeeds). The OAuth
/// identity table is never touched: a pre-existing row
/// (e.g. a real OAuth sync) keeps its bytes, and a Human
/// without one needs no workaround.
pub async fn registration_creates_the_owner_actor_row_and_the_owner_can_manage(fixture: &Fixture) {
    // A PRE-EXISTING OAuth identity row keeps its bytes
    // through the registration: the actor row — not this
    // table — carries authority existence now.
    fixture
        .db
        .execute(DbStatement::with_params(
            "INSERT INTO bcs_user_identities \
             (user_id, auth_source, external_user_id, env) \
             VALUES ('vip-owner', 'oauth-provider', 'ext-vip', 'fixture')",
            vec![],
        ))
        .await
        .expect("pre-existing identity");

    let registered = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_ownermanage".to_string(),
            capabilities(),
            "vip-owner",
            "bot-token-own",
            mutation("vip-owner"),
        )
        .await
        .expect("vip-owner registration commits");
    assert!(registered);
    // The registration ensured the owner's Human ACTOR row
    // (authorities read THIS registry, spec §3.1)...
    let row = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT actor_kind FROM bcs_bots WHERE bot_uuid = ?",
            vec!["human_vip-owner".into()],
        ))
        .await
        .expect("actor row");
    assert_eq!(row.len(), 1, "exactly one Human actor row");
    let actor_kind: String =
        bcs_db_api::db_get_column(&row[0], "actor_kind").unwrap();
    assert_eq!(actor_kind, "human");
    // ...and never wrote or replaced an OAuth identity row.
    let row = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT auth_source, external_user_id FROM bcs_user_identities \
             WHERE user_id = ?",
            vec!["vip-owner".into()],
        ))
        .await
        .expect("identity row");
    assert_eq!(row.len(), 1, "no duplicate identity row");
    let auth_source: String =
        bcs_db_api::db_get_column(&row[0], "auth_source").unwrap();
    assert_eq!(auth_source, "oauth-provider");

    // The second Human of the pairing: their own fresh
    // registration creates their actor row too (and needs
    // no OAuth identity row at all).
    let second = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_second1".to_string(),
            capabilities(),
            "second-owner",
            "bot-token-2nd",
            mutation("second-owner"),
        )
        .await
        .expect("second-owner registration commits");
    assert!(second);

    // The FIRST owner grants the second owner as a
    // manager — this used to fail with
    // bot_authority_human_unknown before the identity row
    // was ensured in-transaction (ruling T4-1).
    let change = fixture
        .authority
        .edit_manager(ManagerEdit {
            bot_id: "bot_ownermanage".to_string(),
            // The repo port contracts are keyed by the
            // persisted Human ACTOR id.
            user_id: "human_second-owner".to_string(),
            action: ManagerEditAction::Grant,
            mutation: mutation("vip-owner").mutation,
        })
        .await
        .expect("manager grant by the registered owner");
    assert_eq!(change.added, 1);
    assert_eq!(change.total, 1);
}

pub async fn mutation_must_match_the_verified_initiator(fixture: &Fixture) {
    let forged = OwnershipInitialization {
        initial_manager_user_ids: Vec::new(),
        owner_user_id: "11111111".to_string(),
        mutation: AuthorityMutation {
            mutation_id: "m-1".to_string(),
            actor: AuthorityActor::Human {
                user_id: "someone-else".to_string(),
            },
        },
    };
    let error = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_fail6".to_string(),
            capabilities(),
            "11111111",
            "bot-token-x",
            forged,
        )
        .await
        .expect_err("diverged initiator is refused");
    assert!(matches!(error, bcs_service_api::ServiceError::InvalidOperation { .. }));
    assert_no_half_registration(&fixture, "bot_fail6").await;
}

// ---------------------------------------------------------------------------
// Governance initialize_ownership.
// ---------------------------------------------------------------------------

pub async fn governance_initialize_backfills_once_and_is_idempotent(fixture: &Fixture) {
    assert!(seed_runtime_bot(&fixture, "bot_gov1").await);
    // Production shape: the governance backfill only applies
    // while the recorded Human actor is live (bcs-admin's
    // classify gate); the stored creator record (seeded as
    // '11111111' by the runtime entry) names the owner the
    // composite may initialize — byte-exact.
    fixture
        .bot_repo
        .ensure_human_actor("11111111", "11111111")
        .await
        .expect("ensure backfill owner actor");
    let view = fixture
        .bot_repo
        .initialize_ownership("bot_gov1", "11111111", mutation("11111111").mutation)
        .await
        .expect("initialize");
    assert_eq!(view.owner_user_id, "11111111");
    assert_eq!(view.owner_actor_id, "human_11111111");
    let role = fixture
        .authority
        .role("human_11111111", "bot_gov1")
        .await
        .expect("role");
    assert_eq!(role, Some(BotAccessRelation::Owner));
    // Repeat: the CURRENT owner is returned untouched.
    let view2 = fixture
        .bot_repo
        .initialize_ownership("bot_gov1", "11111111", mutation("11111111").mutation)
        .await
        .expect("idempotent");
    assert_eq!(view2.owner_user_id, "11111111");
    let audits = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS c FROM bot_authority_changes",
            vec![],
        ))
        .await
        .expect("audits");
    let count: i64 = bcs_db_api::db_get_column(&audits[0], "c").unwrap();
    assert_eq!(count, 1, "no duplicate initialize audit");

    // A later DIFFERENT initialize never resets the owner.
    let error = fixture
        .bot_repo
        .initialize_ownership("bot_gov1", "99999999", mutation("99999999").mutation)
        .await
        .expect_err("cannot reset a committed owner");
    assert!(matches!(
        error,
        bcs_service_api::ServiceError::Conflict(_)
    ));
    let role = fixture
        .authority
        .role("human_11111111", "bot_gov1")
        .await
        .expect("role");
    assert_eq!(role, Some(BotAccessRelation::Owner));
}

pub async fn governance_initialize_refuses_creator_mismatch(fixture: &Fixture) {
    // The stored creator record is authority for the governance
    // backfill: its owner must name the recorded creator
    // byte-for-byte (a case-insensitive candidate list query is
    // never permission to mint).
    assert!(seed_runtime_bot(&fixture, "bot_mismatch").await);
    fixture
        .bot_repo
        .ensure_human_actor("22222222", "22222222")
        .await
        .expect("ensure echo candidate");
    let error = fixture
        .bot_repo
        .initialize_ownership("bot_mismatch", "22222222", mutation("22222222").mutation)
        .await
        .expect_err("mismatching operator must be refused");
    assert!(
        matches!(error, bcs_service_api::ServiceError::Conflict(_)),
        "Conflict expected, got: {error:?}"
    );
    // Nothing half-applied: version stays 0, no owner edge, no audit.
    let bots = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT ownership_version FROM bcs_bots WHERE bot_uuid = ?",
            vec!["bot_mismatch".into()],
        ))
        .await
        .expect("bot row");
    let version: i64 = bcs_db_api::db_get_column(&bots[0], "ownership_version").unwrap();
    assert_eq!(version, 0, "mismatch must not move the version");
    let roles = fixture
        .authority
        .role("human_22222222", "bot_mismatch")
        .await
        .expect("role");
    assert_eq!(roles, None, "no owner edge minted by the refusal");
    let audits = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS c FROM bot_authority_changes",
            vec![],
        ))
        .await
        .expect("audit count");
    let count: i64 = bcs_db_api::db_get_column(&audits[0], "c").unwrap();
    assert_eq!(count, 0, "no audit written by the refusal");
}

pub async fn governance_initialize_never_targets_a_human_actor_row(fixture: &Fixture) {
    // A Human ACTOR row is the authority registry's record of a
    // person, never a physical Bot; management edges point only to
    // physical Bots. ensure_human_actor seeds such a row whose
    // created record matches the caller so only the kind guard
    // discriminates this scenario.
    fixture
        .bot_repo
        .ensure_human_actor("22222222", "22222222")
        .await
        .expect("ensure human actor row");
    let error = fixture
        .bot_repo
        .initialize_ownership("human_22222222", "22222222", mutation("22222222").mutation)
        .await
        .expect_err("a Human actor row must never be initialized");
    assert!(
        matches!(error, bcs_service_api::ServiceError::Conflict(_)),
        "Conflict expected, got: {error:?}"
    );
    let rows = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT ownership_version FROM bcs_bots WHERE bot_uuid = ?",
            vec!["human_22222222".into()],
        ))
        .await
        .expect("human row");
    let version: i64 = bcs_db_api::db_get_column(&rows[0], "ownership_version").unwrap();
    assert_eq!(version, 0, "a Human actor row never gains an ownership version");
    let role = fixture
        .authority
        .role("human_22222222", "human_22222222")
        .await
        .expect("role");
    assert_eq!(role, None, "no human→human owner edge");
}

pub async fn governance_initialize_refuses_missing_and_deleted_bots(fixture: &Fixture) {
    let error = fixture
        .bot_repo
        .initialize_ownership("bot_missing", "22222222", mutation("22222222").mutation)
        .await
        .expect_err("missing bot");
    assert!(matches!(error, bcs_service_api::ServiceError::BotNotFound(_)));
    assert!(seed_runtime_bot(&fixture, "bot_gone").await);
    fixture
        .db
        .execute(DbStatement::with_params(
            "UPDATE bcs_bots SET is_deleted = 1 WHERE bot_uuid = ?",
            vec!["bot_gone".into()],
        ))
        .await
        .expect("tombstone");
    let error = fixture
        .bot_repo
        .initialize_ownership("bot_gone", "22222222", mutation("22222222").mutation)
        .await
        .expect_err("deleted bot");
    assert!(matches!(error, bcs_service_api::ServiceError::Conflict(_)));
}

// ---------------------------------------------------------------------------
// v2 Provider composite (the SQL Bot Provider store).
// ---------------------------------------------------------------------------

pub fn provider_record(
    bot_id: &str,
    mode: BotConnectionMode,
) -> BotProviderRecord {
    provider_record_ref(bot_id, mode, "ref-1")
}

pub(super) fn provider_record_ref(
    bot_id: &str,
    mode: BotConnectionMode,
    provider_bot_ref: &str,
) -> BotProviderRecord {
    BotProviderRecord {
        bot_uuid: bot_id.to_string(),
        provider_id: "provider-a".to_string(),
        provider_bot_ref: provider_bot_ref.to_string(),
        connection_mode: mode,
        webhook_url: if mode == BotConnectionMode::Gateway {
            Some("https://gw.example.test".to_string())
        } else {
            None
        },
        is_deleted: false,
    }
}

pub async fn trusted_v2_registration_commits_binding_owner_and_authority(fixture: &Fixture) {
    fixture
        .provider_repo
        .create_provider_bot_with_ownership(
            provider_record("bot_prov1", BotConnectionMode::Gateway),
            capabilities(),
            "11111111",
            "bot-token-p1",
            mutation("11111111"),
        )
        .await
        .expect("committed");
    let binding = fixture
        .provider_repo
        .get_connection_mode("bot_prov1")
        .await
        .expect("mode")
        .expect("bot exists");
    assert_eq!(binding, BotConnectionMode::Gateway);
    let role = fixture
        .authority
        .role("human_11111111", "bot_prov1")
        .await
        .expect("role");
    assert_eq!(role, Some(BotAccessRelation::Owner));
    let profile = fixture
        .profile_port
        .get_active_default("bot_prov1", &resolve_env())
        .await;
    assert!(profile.is_some(), "default profile committed");
    let rows = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT action FROM bot_authority_changes WHERE bot_id = ?",
            vec!["bot_prov1".into()],
        ))
        .await
        .expect("audit");
    let action: String = bcs_db_api::db_get_column(&rows[0], "action").unwrap();
    assert_eq!(action, "initialize");
    // Version-1 Human composite parity: plugin mode too.
    fixture
        .provider_repo
        .create_provider_bot_with_ownership(
            provider_record_ref("bot_prov2", BotConnectionMode::Plugin, "ref-2"),
            BotCapabilities { name: Some("Plugin bot".into()), ..capabilities() },
            "11111111",
            "bot-token-p2",
            mutation("11111111"),
        )
        .await
        .expect("committed plugin bot");
    // A reused Provider/ref (even tombstoned) conflicts.
    let error = fixture
        .provider_repo
        .create_provider_bot_with_ownership(
            provider_record_ref("bot_prov3", BotConnectionMode::Plugin, "ref-1"),
            BotCapabilities { name: Some("Duplicate ref bot".into()), ..capabilities() },
            "11111111",
            "bot-token-p3",
            mutation("11111111"),
        )
        .await
        .expect_err("duplicate ref");
    assert!(matches!(error, bcs_service_api::ServiceError::Conflict(_)));
    let rows = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS c FROM bcs_bots WHERE bot_uuid = ?",
            vec!["bot_prov3".into()],
        ))
        .await
        .expect("bots");
    let count: i64 = bcs_db_api::db_get_column(&rows[0], "c").unwrap();
    assert_eq!(count, 0, "no Bot written for the conflicting ref");
}

pub async fn trusted_v2_binding_failure_rolls_back_everything(fixture: &Fixture) {
    fixture.db.arm("bcs_provider_bot_bindings");
    let error = fixture
        .provider_repo
        .create_provider_bot_with_ownership(
            provider_record("bot_prov_fail", BotConnectionMode::Gateway),
            capabilities(),
            "11111111",
            "bot-token-pf",
            mutation("11111111"),
        )
        .await
        .expect_err("binding failure surfaces");
    assert!(
        matches!(error, bcs_service_api::ServiceError::InternalError(_)),
        "persistence failures stay internal: {error}"
    );
    assert_no_half_registration(&fixture, "bot_prov_fail").await;
    // The Provider/ref stays free for the honest retry.
    fixture.db.armed.lock().unwrap().clear();
    fixture
        .provider_repo
        .create_provider_bot_with_ownership(
            provider_record("bot_prov_fail", BotConnectionMode::Gateway),
            capabilities(),
            "11111111",
            "bot-token-pf",
            mutation("11111111"),
        )
        .await
        .expect("retry succeeds");
}

pub async fn trusted_v2_owner_write_failure_rolls_back_everything(fixture: &Fixture) {
    fixture.db.arm("edge_grants");
    let error = fixture
        .provider_repo
        .create_provider_bot_with_ownership(
            provider_record("bot_prov_fail2", BotConnectionMode::Plugin),
            capabilities(),
            "11111111",
            "bot-token-pf2",
            mutation("11111111"),
        )
        .await
        .expect_err("owner write failure surfaces");
    assert!(
        matches!(error, bcs_service_api::ServiceError::InternalError(_)),
        "persistence failures stay internal: {error}"
    );
    assert_no_half_registration(&fixture, "bot_prov_fail2").await;
}

// ---------------------------------------------------------------------------
// Runtime propagation: no runtime activation from store writes.
// ---------------------------------------------------------------------------

pub async fn committed_registration_does_not_activate_runtime(fixture: &Fixture) {
    fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_rt1".to_string(),
            capabilities(),
            "11111111",
            "bot-token-rt",
            mutation("11111111"),
        )
        .await
        .expect("committed");
    assert!(!fixture.bot_repo.is_connected("bot_rt1").await);
    // The persisted token remains the only credential
    // source; the registration published no process-local
    // session state.
    assert_eq!(
        fixture.bot_repo.load_token("bot_rt1").await.as_deref(),
        Some("bot-token-rt")
    );
}
